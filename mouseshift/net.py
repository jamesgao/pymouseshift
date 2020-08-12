import asyncio
import json
import socket
import time
import ssl

import logging
logger = logging.getLogger(__name__)

from gi.repository import GLib

from . import db, config, enums, clamp, Event, ui

PORT = 8975

"""Implement a dirt simple communication method:
4 byte integer with the number of bytes
n bytes json-encoded dict with data"""
async def _xfer(writer, obj):
    obj_buf = json.dumps(obj).encode()
    writer.write(len(obj_buf).to_bytes(4, 'big'))
    writer.write(obj_buf)
    logger.debug(f"Send {len(obj_buf)} bytes: {obj_buf}")
    await writer.drain()

async def _recv(reader):
    size = await reader.read(4)
    nbytes = int.from_bytes(size, 'big')
    buf = await reader.read(nbytes)
    logger.debug(f"Recv {nbytes} bytes: {buf}")
    return json.loads(buf.decode())

class Server(object):
    """Abstract base class for different platforms
    Implements the basic server algorithm to shuffle the mouse pointer between clients and the local server"""
    def __init__(self, screen, accel=1.8):
        self.accel = accel
        self.pos = [0, 0]
        self.screen = screen
        self.buffer_size = [0,0,screen[0],screen[1]]
        self.clients = []
        self.capabilities = dict()

        self._last_screen = False
        self.local_event(Event(enums.EV_REL, enums.REL_X, -screen[0]))
        self.local_event(Event(enums.EV_REL, enums.REL_Y, -screen[1]))
        self.local_event(Event(enums.SYN_REPORT, 0, 0))

        self.sslcontext = ssl.SSLContext()

    async def serve(self):
        server = await asyncio.start_server(self.add_client, '0.0.0.0', PORT)
        self.hbtask = asyncio.create_task(self.heartbeat())

        logger.debug(f'Starting server on {server.sockets[0].getsockname()}')
        async with server:
            await server.serve_forever()

    async def add_client(self, reader, writer):
        client = await _recv(reader)
        logger.info(f"Client {client['hostname']} connected")
        try:
            client = db.get_client(client['hostname'], client['token'])
            await self.confirm_client(client, reader, writer)
        except KeyError:
            #New client, confirm with user that it's ok first
            res = client['resolution']
            client['topleft'] = self.buffer_size[2], 0
            client['bottomright'] = self.buffer_size[2]+res[0], res[1]
            GLib.idle_add(ui.confirm_client, self, client, reader, writer)

    async def confirm_client(self, client, reader, writer):
        client = Client(**client)
        if client not in db:
            db.add_client(client)

        self.buffer_size = [
                min(self.buffer_size[0], client.xlim[0]),
                min(self.buffer_size[1], client.ylim[0]),
                max(self.buffer_size[0], client.xlim[1]),
                max(self.buffer_size[1], client.ylim[1]),
            ]
        client.reader = reader
        client.writer = writer
        self.clients.append(client)
        await _xfer(writer, self.capabilities)

    async def deny_client(self, client, reader, writer):
        pass

    @property
    def offscreen(self):
        return ( 
            self.pos[0] < 0 or 
            self.pos[0] > self.screen[0] or
            self.pos[1] < 0 or 
            self.pos[1] > self.screen[1])

    def move_x(self, x):
        x = self.pos[0] + int(x * self.accel)
        if x > self.buffer_size[2]:
            x = self.buffer_size[2]
        elif x < self.buffer_size[0]:
            x = self.buffer_size[0]

        dx = x - self.pos[0]
        self.pos[0] = x

        if self.offscreen:
            #position gets recomputed by send_event
            self.send_event(Event(enums.EV_REL, enums.REL_X, dx))
        else:
            #x = int(clamp(self.pos[0], 0, self.screen[0]))
            #self.local_event(Event(enums.EV_ABS, enums.ABS_X, x))
            self.local_event(Event(enums.EV_REL, enums.REL_X, dx))

    def move_y(self, y):
        y = self.pos[1] + int(y * self.accel)

        if y > self.buffer_size[3]:
            y = self.buffer_size[3]
        elif y < self.buffer_size[1]:
            y = self.buffer_size[1]

        dy = y - self.pos[1]
        self.pos[1] = y

        if self.offscreen:
            self.send_event(Event(enums.EV_REL, enums.REL_Y, dy))
        else:
            #y = int(clamp(self.pos[1], 0, self.screen[1]))
            #self.local_event(Event(enums.EV_ABS, enums.ABS_Y, y))
            self.local_event(Event(enums.EV_REL, enums.REL_Y, dy))

    async def handle_keyboard(self, event):
        if self.offscreen:
            await self.send_event(event)

    async def handle_mouse(self, ev):
        if ev.type == enums.EV_REL:
            #Single move event
            #update the internal cursor tracker
            #the move_x and move_y commands will delegate the moves to local or remote
            if ev.code == enums.REL_X:
                self.move_x(ev.value)
            elif ev.code == enums.REL_Y:
                self.move_y(ev.value)
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
            #execute move
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
                if evcode == enums.REL_X:
                    evtype = enums.EV_ABS
                    evcode = enums.ABS_X
                    val = self.pos[0] - client.xlim[0]
                elif evcode == enums.REL_Y:
                    evtype = enums.EV_ABS
                    evcode = enums.ABS_Y
                    val = self.pos[1] - client.ylim[1]

                await _xfer(client.writer, [evtype, evcode, val])

    def remove_client(self, client):
        self.clients.remove(client)
        #do math to remove the client from the screen

    async def heartbeat(self):
        while True:
            #emit a heartbeat every 5 seconds
            logger.debug('Sending heartbeat')
            for client in self.clients:
                await _xfer(client.writer, dict(heartbeat=True))
                try:
                    task = asyncio.wait_for(_recv(client.reader), 2)
                except asyncio.TimeoutError:
                    #client hasn't responded to a heartbeat, remove it
                    logger.warning(f"Client {client.hostname} failed to respond to heartbeat, removing")
                    self.remove_client(client)
            await asyncio.sleep(5)

class Client(object):
    def __init__(self, hostname=None, token=config['token'], topleft=None, bottomright=None):
        if hostname is None:
            self.hostname = socket.gethostname()
        self.token = token
        self.caps = None
        if topleft is not None:
            self.position(topleft, bottomright)

    def __contains__(self, pos):
        return (
            self.xlim[0] < pos[0] and pos[0] < self.xlim[1] and
            self.ylim[0] < pos[1] and pos[1] < self.ylim[1])

    def position(self, topleft, bottomright):
        self.xlim = topleft[0], bottomright[0]
        self.ylim = topleft[1], bottomright[1]
        self.xrange = self.xlim[1] - self.xlim[0]
        self.yrange = self.ylim[1] - self.ylim[0]

    async def connect(self, server, resolution):
        logger.debug(f"Connecting to {self.server}")
        reader, writer = await asyncio.open_connection(self.server, PORT)
        metadata = dict(hostname=socket.gethostname(), token=self.token, resolution=resolution)
        await _xfer(writer, metadata)
        self.caps = await _recv(reader)

        self.reader = reader
        self.writer = writer

    async def handle_event(self):
        """Handle received data

        Responds to heartbeat requests."""
        while True:
            data = await _recv(self.reader)
            while "heartbeat" in data:
                await _xfer(self.writer, dict(alive=True))
                data = await _recv(self.reader)
            yield data
