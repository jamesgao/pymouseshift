import os
import asyncio
import json
import socket
import time
import ssl

import logging
logger = logging.getLogger(__name__)

from OpenSSL import crypto
from gi.repository import GLib

from . import db, config, config_dir, cert_dir, enums, clamp, Event, ui

PORT = 8976

def gen_cert(name=socket.gethostname()):
    """Generate a self-signed certificate to encrypt traffic"""
    from OpenSSL import crypto, SSL
    k = crypto.PKey()
    k.generate_key(crypto.TYPE_RSA, 4096)
    cert = crypto.X509()
    cert.get_subject().CN = name
    cert.set_serial_number(0)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(365*24*60*60*5) #5 year cert renewal
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(k)
    cert.sign(k, 'sha512')
    with open(os.path.join(config_dir, f'{name}.crt'), 'wt') as fp:
        fp.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode('utf-8'))
    with open(os.path.join(config_dir, f'{name}.key'), 'wt') as fp:
        fp.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k).decode('utf-8'))

"""Implement a dirt simple communication method:
4 byte integer with the number of bytes
n bytes json-encoded dict with data"""
async def _xfer(writer, obj):
    obj_buf = json.dumps(obj).encode()
    writer.write(len(obj_buf).to_bytes(4, 'big'))
    writer.write(obj_buf)
    #await writer.drain()

async def _recv(reader):
    size = await reader.read(4)
    nbytes = int.from_bytes(size, 'big')
    buf = await reader.read(nbytes)
    return json.loads(buf.decode())

class Server(object):
    """Abstract base class for different platforms

    Implements the basic server to shift the mouse pointer
    """
    def __init__(self, screen, accel=1.8, app=None):
        self.name = socket.gethostname()
        self.accel = accel
        self.pos = [0, 0]
        self.screen = screen
        self.buffer_size = [0,0,screen[0],screen[1]]
        self.clients = []
        self.app = app

        self._last_screen = False
        self.running = True

        #load the server certificate 
        certpath = os.path.join(config_dir, f'{self.name}.crt')
        try:
            with open(certpath) as fp:
                self.cert = crypto.load_certificate(crypto.FILETYPE_PEM, fp.read())
        except OSError:
            #couldn't find the server cert, generate a self-signed one
            gen_cert()
            with open(certpath) as fp:
                self.cert = crypto.load_certificate(crypto.FILETYPE_PEM, fp.read())
        #Generate the cert hash to display to the user
        self.cert_hash = self.cert.digest('md5')

        #Generate the ssl context with the server certificate
        self.sslctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        kpath = os.path.join(config_dir, f'{self.name}.key')
        self.sslctx.load_cert_chain(certfile=certpath, keyfile=kpath)

    async def serve(self):
        """Serve function to be run by asyncio.run

        Also adds the heartbeat task to keep track of clients
        """
        server = await asyncio.start_server(self.client_connect, '0.0.0.0', PORT, ssl=self.sslctx)
        self.hbtask = asyncio.create_task(self.heartbeat())
        logger.info(f'Starting server on {server.sockets[0].getsockname()}')
        self.server_task = asyncio.create_task(server.serve_forever())

    async def client_connect(self, reader, writer):
        """Handles a new client connecting

        Confirm with the client 
        """
        try:
            client = await _recv(reader)
            try:
                logger.debug(f"Client {client['hostname']} connecting...")
                client = db.get_client(client['hostname'], client['token'])
                await self.add_client(client, reader, writer)
            except KeyError:
                #Unknown client, confirm with user
                res = client['resolution']
                client['topleft'] = self.buffer_size[2], 0
                client['bottomright'] = self.buffer_size[2]+res[0], res[1]

                if self.app is not None:
                    #if there's an app, pop up a message to confirm
                    GLib.idle_add(self.app.confirm_client, client, reader, writer)
                else:
                    #No app, assume user will deal with configuration themselves
                    await self.add_client(client, reader, writer)
        except json.decoder.JSONDecodeError:
            #Cert query doesn't transmit json, ignore
            pass

    async def add_client(self, client, reader, writer):
        client = Client(**client)
        logger.debug(f'Client {client.hostname} confirmed')
        if client not in db:
            db.update_client(client)
        if self.app is not None:
            self.app.add_client(client)

        self.buffer_size = [
                min(self.buffer_size[0], client.xlim[0]),
                min(self.buffer_size[1], client.ylim[0]),
                max(self.buffer_size[2], client.xlim[1]),
                max(self.buffer_size[3], client.ylim[1]),
            ]
        logger.debug(f'New screen size: {self.buffer_size}')
        client.reader = reader
        client.writer = writer
        self.clients.append(client)
        #Add the absolute axes for this client
        caps = dict(self.capabilities)
        #caps[enums.EV_REL].remove(enums.REL_X)
        #caps[enums.EV_REL].remove(enums.REL_Y)
        caps[enums.EV_ABS] = [
            (enums.ABS_X, (0,0,client.resolution[0],0,0,0)),
            (enums.ABS_Y, (0,0,client.resolution[1],0,0,0))]

        await _xfer(writer, caps)
        logger.info(f"Client {client.hostname} connected")

    def remove_client(self, client):
        logger.info(f'Removing client {client.hostname}')
        # client.reader.close()
        # client.writer.close()
        # logger.debug(f'Client {client.hostname} sockets closed')

        self.clients.remove(client)
        if self.app is not None:
            self.app.rm_client(client)
        #do math to remove the client from the screen
        self.update_buffer()

    def update_buffer(self):
        screen = [0,0,self.screen[0], self.screen[1]]
        for client in self.clients:
            screen[0] = min(screen[0], client.xlim[0])
            screen[1] = min(screen[1], client.ylim[0])
            screen[2] = max(screen[2], client.xlim[1])
            screen[3] = max(screen[3], client.ylim[1])
        self.buffer_size = screen
        logger.debug(f'Current screen size: {self.buffer_size}')

    async def deny_client(self, client, reader, writer):
        #TODO: decide what to store and send when we want to deny this client
        pass

    @property
    def offscreen(self):
        return ( 
            self.pos[0] < 0 or 
            self.pos[0] > self.screen[0] or
            self.pos[1] < 0 or 
            self.pos[1] > self.screen[1])

    async def move_x(self, x):
        x = self.pos[0] + int(x * self.accel)
        if x > self.buffer_size[2]:
            x = self.buffer_size[2]
        elif x < self.buffer_size[0]:
            x = self.buffer_size[0]

        #dx = x - self.pos[0]
        self.pos[0] = x

        if self.offscreen:
            #position gets recomputed by send_event
            await self.send_event(Event(enums.EV_REL, enums.REL_X, x))
        else:
            x = int(clamp(self.pos[0], 0, self.screen[0]))
            self.local_event(Event(enums.EV_ABS, enums.ABS_X, x))
            #self.local_event(Event(enums.EV_REL, enums.REL_X, dx))

    async def move_y(self, y):
        y = self.pos[1] + int(y * self.accel)

        if y > self.buffer_size[3]:
            y = self.buffer_size[3]
        elif y < self.buffer_size[1]:
            y = self.buffer_size[1]

        #dy = y - self.pos[1]
        self.pos[1] = y

        if self.offscreen:
            await self.send_event(Event(enums.EV_REL, enums.REL_Y, y))
        else:
            y = int(clamp(self.pos[1], 0, self.screen[1]))
            self.local_event(Event(enums.EV_ABS, enums.ABS_Y, y))
            #self.local_event(Event(enums.EV_REL, enums.REL_Y, dy))

    async def handle_keyboard(self, event):
        if self.offscreen:
            await self.send_event(event)

    async def handle_mouse(self, ev):
        if ev.type == enums.EV_REL:
            #Single move event
            #update the internal cursor tracker
            #the move_x and move_y commands will delegate the moves to local or remote
            if ev.code == enums.REL_X:
                await self.move_x(ev.value)
            elif ev.code == enums.REL_Y:
                await self.move_y(ev.value)
            else:
                #scroll events get funneled here
                if self.offscreen:
                    await self.send_event(ev)
                else:
                    self.local_event(ev)
        elif ev.type == enums.EV_KEY:
            #left, middle, right click events
            if self.offscreen:
                await self.send_event(ev)
            else:
                self.local_event(ev)
                
        elif ev.type == enums.SYN_REPORT:
            #detect if we've moved off this screen
            if self.offscreen and not self._last_screen:
                self.grab_keyboard()
                self._last_screen = True
            elif not self.offscreen and self._last_screen:
                self.grab_keyboard(False)
                self._last_screen = False

            if self.offscreen:
                await self.send_event(ev)
            else:
                self.local_event(ev)

    async def send_event(self, ev):
        for client in self.clients:
            if self.pos in client:
                evtype, evcode, val = ev.type, ev.code, ev.value
                #if event is a mouse move, rewrite the position
                if ev.type == enums.EV_REL:
                    if evcode == enums.REL_X:
                        evtype = enums.EV_ABS
                        evcode = enums.ABS_X
                        val = int((self.pos[0] - client.xlim[0]) * client.move_scale)
                    elif evcode == enums.REL_Y:
                        evtype = enums.EV_ABS
                        evcode = enums.ABS_Y
                        val = int((self.pos[1] - client.ylim[0]) * client.move_scale)
                pkg = dict(type=evtype, code=evcode, value=val)
                try:
                    await asyncio.wait_for(_xfer(client.writer, pkg), 2)
                except asyncio.TimeoutError:
                    #this client died, remove it!
                    self.remove_client(client)

    async def heartbeat(self):
        logger.debug("Running heartbeat loop")
        while self.running:
            #emit a heartbeat every 5 seconds
            for client in self.clients:
                await _xfer(client.writer, dict(heartbeat=True))
                try:
                    resp = await asyncio.wait_for(_recv(client.reader), 2)
                    if not 'alive' in resp:
                        raise asyncio.TimeoutError
                except (asyncio.TimeoutError, json.decoder.JSONDecodeError):
                    #client hasn't responded to a heartbeat, remove it
                    logger.warning(f"Client {client.hostname} failed to respond to heartbeat, removing")
                    self.remove_client(client)
            await asyncio.sleep(5)
        logger.debug("Exited heartbeat loop")

    def stop(self):
        self.running = False
        #self.hbtask.cancel()
        self.server_task.cancel()

class Client(object):
    def __init__(self, hostname=None, token=config['token'], 
        resolution=None, topleft=None, bottomright=None):
        self.hostname = hostname
        self.resolution = resolution

        if hostname is None:
            self.hostname = socket.gethostname()
        self.token = token
        if topleft is not None:
            self.position(topleft, bottomright)

        self.sslctx = ssl.create_default_context(capath=cert_dir)
        #self.sslctx = None

    def __contains__(self, pos):
        return (
            self.xlim[0] < pos[0] and pos[0] < self.xlim[1] and
            self.ylim[0] < pos[1] and pos[1] < self.ylim[1])

    def position(self, topleft, bottomright):
        self.xlim = topleft[0], bottomright[0]
        self.ylim = topleft[1], bottomright[1]
        self.xrange = self.xlim[1] - self.xlim[0]
        self.yrange = self.ylim[1] - self.ylim[0]

        self.move_scale = self.resolution[0] / self.xrange 

    async def connect(self, server, resolution):
        logger.info(f"Connecting to {server}")
        reader, writer = await asyncio.open_connection(server, PORT, ssl=self.sslctx)
        
        metadata = dict(hostname=self.hostname, 
            token=self.token, 
            resolution=resolution)
        await _xfer(writer, metadata)
        logger.info(f'Connected to {server}')

        self.reader = reader
        self.writer = writer
        return await self.handle_heartbeat()

    async def handle_heartbeat(self):
        """Handle received packets

        Packets transmitted by the server contains heartbeats, which require a response.
        This function returns the next actual event, while responding to all intervening 
        heartbeats.
        """
        data = await _recv(self.reader)
        while "heartbeat" in data:
            await _xfer(self.writer, dict(alive=True))
            data = await _recv(self.reader)
                #ignore invalid json
        return data

    def stop(self):
        pass

def protect_ssl(addr, retry=None):
    """Decorator function which protects a new connection

    If an unknown ssl certificate is encountered, confirm with user that it's OK.
    If user affirms, retry the connection using the retry function
    """
    def protect(func):
        def inside():
            try:
                func(addr)
            except ssl.SSLCertVerificationError:
                certstr = ssl.get_server_certificate((addr, PORT))
                cert = crypto.load_certificate(crypto.FILETYPE_PEM, certstr)
                certhash = cert.digest('md5')

                def confirm():
                    certpath = hex(cert.subject_name_hash())[2:]
                    certpath = os.path.join(cert_dir, certpath+".0")
                    with open(certpath, "wt") as fp:
                        fp.write(certstr)

                    if retry is None:
                        func(addr)
                    else:
                        retry(addr)

                def ask():
                    msg = f"Connecting to a new server with certificate hash {certhash}, proceed?"
                    ui.confirm_dialog(msg, confirm, title="New server")

                GLib.idle_add(ask)
        return inside
    return protect