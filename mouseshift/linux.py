import asyncio
import json
import socket
import evdev

import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

from . import enums
from .net import Server, Client

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Gio, GLib

resolution = Gdk.Screen.width(), Gdk.Screen.height()

class LinuxServer(Server):
    def __init__(self, mouse="/dev/input/event4", keyboards=["/dev/input/event2"]):
        self.mouse = evdev.InputDevice(mouse)
        self.keyboards = [evdev.InputDevice(kbd) for kbd in keyboards]
        self.mouse.grab()

        #get mouse capabilities to forward to absolute device
        caps = self.mouse.capabilities()
        del caps[evdev.ecodes.EV_SYN]

        #merge keyboard and mouse capabilities for transfer
        self.capabilities = dict(caps)
        for kbd in self.keyboards:
            kcap = kbd.capabilities()
            for k, v in kcap.items():
                if k == evdev.ecodes.EV_SYN:
                    continue
                elif k not in self.capabilities:
                    self.capabilities[k] = v
                else:
                    self.capabilities[k].extend(v)
        
        self.dev = evdev.UInput(caps)
        self._set_dconf()

        super(LinuxServer, self).__init__((resolution[0], resolution[1]))

    async def serve(self):
        self.task_mouse = asyncio.create_task(self.readmouse())
        self.task_kbds = []
        for kbd in self.keyboards:
            task = asyncio.create_task(self.readkbd(kbd))
            self.task_kbds.append(task)

        try:
            await super(LinuxServer, self).serve()
        except:
            self._unset_dconf()

    def _set_dconf(self):
        self._mouse_settings = Gio.Settings.new('org.gnome.desktop.peripherals.mouse')
        self._mouse_speed = self._mouse_settings.get_value('speed')
        self._mouse_accel = self._mouse_settings.get_value('accel-profile')
        self._mouse_settings.set_value('speed', GLib.Variant.new_double(0))
        self._mouse_settings.set_value('accel-profile', GLib.Variant.new_string('flat'))

    def _unset_dconf(self):
        self._mouse_settings.set_value('speed', self._mouse_speed)
        self._mouse_settings.set_value('accel-profile', self._mouse_accel)

    def grab_keyboard(self, grabbed=True):
        for kbd in self.keyboards:
            if grabbed:
                kbd.grab()
            else:
                kbd.ungrab()

    def local_event(self, event):
        if event.type == enums.SYN_REPORT:
            self.dev.syn()
        else:
            self.dev.write(event.type, event.code, event.value)

    async def readmouse(self):
        async for ev in self.mouse.async_read_loop():
            await self.handle_mouse(ev)

    async def readkbd(self, keyboard):
        async for ev in keyboard.async_read_loop():
            await self.handle_keyboard(ev)

class LinuxClient(Client):
    def __init__(self, **kwargs):
        super(LinuxClient, self).__init__(**kwargs)
        self.dev = None

    async def connect(self, server):
        caps = await super(LinuxClient, self).connect(server, resolution)
        self.capabilities = dict((int(k), v) for k, v in caps.items())
        self.dev = evdev.UInput(self.capabilities)
        logger.debug(f'Received capabilities: {self.capabilities}')
        await self.handle_event()

    async def handle_event(self):
        while True:
            ev = await self.handle_heartbeat()
            try:
                if ev['type'] == enums.SYN_REPORT:
                    self.dev.syn()
                else:
                    self.dev.write(ev['type'], ev['code'], ev['value'])
            except KeyError:
                logger.debug(f'Invalid event: {ev}')


def find_devs():
    mouse = None
    keyboard = []
    for dev in evdev.list_devices():
        dev = evdev.InputDevice(dev)
        if "mouse" in dev.name.lower():
            mouse = dev.path
        elif "keyboard" in dev.name.lower():
            keyboard.append(dev.path)
    return mouse, keyboard

def run_server():
    mouse, keyboards = find_devs()
    kvm = LinuxServer(mouse, keyboards)
    def threadloop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        kvm.loop = loop
        loop.run_until_complete(kvm.serve())
        loop.run_forever()
    import threading
    threading.Thread(target=threadloop).start()

def run_client(server="10.1.1.2"):
    kvm = LinuxClient()
    asyncio.run(kvm.connect(server))
