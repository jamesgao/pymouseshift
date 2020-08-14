import asyncio
import json
import socket
import evdev

import logging
logger = logging.getLogger(__name__)

from . import enums
from .net import Server, Client

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Gio, GLib

resolution = Gdk.Screen.width(), Gdk.Screen.height()

class LinuxServer(Server):
    def __init__(self, mouse, keyboards, **kwargs):
        self.mouse = evdev.InputDevice(mouse)
        self.keyboards = [evdev.InputDevice(kbd) for kbd in keyboards]
        self.mouse.grab()

        #get mouse capabilities to forward to absolute device
        caps = self.mouse.capabilities()
        del caps[evdev.ecodes.EV_SYN]

        #modify capabilities to delete relative axes and add absolute axes
        caps[enums.EV_REL].remove(enums.REL_X)
        caps[enums.EV_REL].remove(enums.REL_Y)
        caps[enums.EV_ABS] = [
            (enums.ABS_X, (0,0,resolution[0],0,0,0)),
            (enums.ABS_Y, (0,0,resolution[1],0,0,0))]

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

        super(LinuxServer, self).__init__(resolution, **kwargs)

    async def serve(self):
        self.task_mouse = asyncio.create_task(self.readmouse())
        self.task_kbds = []
        for kbd in self.keyboards:
            task = asyncio.create_task(self.readkbd(kbd))
            self.task_kbds.append(task)

        await super(LinuxServer, self).serve()

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

    def stop(self):
        logger.info("Shutting down server")
        super(LinuxServer, self).stop()
        self.mouse.ungrab()
        self.task_mouse.cancel()
        for task in self.task_kbds:
            task.cancel()

class LinuxClient(Client):
    def __init__(self, **kwargs):
        super(LinuxClient, self).__init__(**kwargs)
        self.dev = None
        self.running = True

    async def connect(self, server):
        caps = await super(LinuxClient, self).connect(server, resolution)
        #caps may be None if user rejects server cert request
        if caps is not None:
            self.capabilities = dict((int(k), v) for k, v in caps.items())
            self.dev = evdev.UInput(self.capabilities)
            logger.debug(f'Received capabilities: {self.capabilities}')
            await self.handle_event()

    async def handle_event(self):
        while self.running:
            try:
                ev = await self.handle_heartbeat()
            except json.decoder.JSONDecodeError:
                #No json received, quit out of loop
                self.running = False
            try:
                if ev['type'] == enums.SYN_REPORT:
                    self.dev.syn()
                else:
                    self.dev.write(ev['type'], ev['code'], ev['value'])
            except KeyError:
                logger.debug(f'Invalid event: {ev}')

    def stop(self):
        self.running = False

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

def make_server(**kwargs):
    mouse, keyboards = find_devs()
    return LinuxServer(mouse, keyboards, **kwargs)
