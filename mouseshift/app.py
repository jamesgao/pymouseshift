import os
import asyncio
import threading
import logging
logger = logging.getLogger(__name__)

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
import pystray
import PIL.Image

from . import ui, net, config, save_config, cert_dir

icon_path = os.path.abspath(os.path.split(__file__)[0])
icon_empty = PIL.Image.open(os.path.join(icon_path, "mouse.png"))
icon_filled = PIL.Image.open(os.path.join(icon_path, "mouse_fill.png"))

class App(object):
    def __init__(self):
        self.icon = pystray.Icon('pymouseshift', 
            icon=icon_empty, 
            title="pymouseshift",
            menu=self.default_menu)

        #currently only linux server and clients supported
        #eventually need to do platform detection
        from . import linux
        self.server_cls = linux.make_server
        self.client_cls = linux.LinuxClient

    @property
    def default_menu(self):
        #generate all the previous servers
        servers = []
        for server in config['servers']:
            def connect(icon, item):
                self.start_client(server)
            servers.append(pystray.MenuItem(server, connect))

        servers.append(pystray.MenuItem("+ Add new server", self.connect))
        client_menu = pystray.Menu(*servers)
        return pystray.Menu(
            pystray.MenuItem("pymouseshift",None),
            pystray.MenuItem("Start Server", self.start_server),
            pystray.MenuItem("Connect as client", client_menu),
            pystray.MenuItem("Quit", self.quit),
        )

    def start_server(self, icon, item):
        server = self.server_cls(app=self)

        loop = asyncio.new_event_loop()
        def target():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(server.serve())
            try:
                loop.run_forever()
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())

        self.server, self.loop = server, loop
        self.thread = threading.Thread(target=target)
        self.thread.start()

        menu = pystray.Menu(
            pystray.MenuItem("pymouseshift", None),
            pystray.MenuItem("Server Preferences", self.preferences),
            pystray.MenuItem('Stop Server', self.stop),
            pystray.MenuItem("Quit", self.quit))
        icon.menu = menu
        icon.update_menu()

    def connect(self, icon, item):
        """Pops open a connect address dialog"""
        def callback(addr):
            logger.debug(f'Connecting to {addr}')
            config['servers'].insert(0, addr)
            if len(config['servers']) > 10:
                config['servers'].pop()
            save_config()
            self.start_client(addr)

        ui.connect_dialog(callback)

    def start_client(self, addr):
        """Start up the client app
        
        """
        self.client = self.client_cls()

        self.loop = asyncio.new_event_loop()

        @net.protect_ssl(addr, retry=self.start_client)
        def target(addr):
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.client.connect(addr))

        logger.debug('Starting client thread')
        self.thread = threading.Thread(target=target)
        self.thread.start()

        logger.debug('Updating menu')
        menu = pystray.Menu(
            pystray.MenuItem("pymouseshift", None),
            pystray.MenuItem(f'Disconnect from {addr}', self.stop),
            pystray.MenuItem("Quit", self.quit))
        self.icon.menu = menu
        self.icon.update_menu()
        self.icon.icon = icon_filled

    def stop(self, icon, item):
        """Gracefully exit either the server or client loops"""
        try:
            self.server.stop()
            del self.server
        except AttributeError:
            pass
        try:
            self.client.stop()
            del self.client
        except AttributeError:
            pass
        try:
            self.loop.stop()
            self.thread.join()
            del self.thread
            del self.loop
        except AttributeError:
            pass

        self.icon.menu = self.default_menu
        self.icon.update_menu()
        self.icon.icon = icon_empty

    def preferences(self, icon, item):
        self.prefs = ui.ServerPrefs(self)
        for client in self.server.clients:
            self.prefs.add_client(client)

    def confirm_client(self, client, reader, writer):
        server, loop = self.server, self.loop

        def confirm():
            coro = server.add_client(client, reader, writer)
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            future.result()
            self.preferences(None, None)

        def cancel():
            coro = server.deny_client(client, reader, writer)
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            future.result()

        hostname, token, cert = client['hostname'], client['token'], server.cert_hash
        msg = f'Client <b>{hostname}</b> with token <i>{token}</i> is trying to connect, allow?\r\rServer ID: <tt>{cert}</tt>'
        ui.confirm_dialog(msg, confirm, cancel)

    def add_client(self, client):
        self.icon.icon = icon_filled

    def rm_client(self, client):
        if len(self.server.clients) == 0:
            self.icon.icon = icon_empty

    def quit(self):
        self.stop(None, None)
        self.icon.stop()

    def run(self):
        self.icon.run()