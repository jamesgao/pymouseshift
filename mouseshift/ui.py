import os
import sys
import time
import socket
import logging
logger = logging.getLogger(__name__)

import asyncio

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

from . import db

class ServerPrefs(Gtk.Window):
    def __init__(self, app, mainwidth=300):
        self.app = app
        self.resolution = Gdk.Screen.width(), Gdk.Screen.height()
        self.hostname = socket.gethostname()
        super(ServerPrefs, self).__init__(title="pymouseshift Screen Arrangement")
        self.set_size_request(width=1024, height=768)

        self.fixed = Gtk.Fixed()
        self.add(self.fixed)

        aspect = self.resolution[0] / self.resolution[1]
        mheight = mainwidth / aspect
        self.origin = (512-mainwidth/2, 384-mheight/2)

        self.main = ServerScreen(self, 
            self.hostname, 
            '{}x{}'.format(*self.resolution),
            width=mainwidth,
            aspect=aspect,
            resizeable=False)
        
        self._scale = mainwidth / self.resolution[0] 

        self.screens = {self.main:None}
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.show_all()

    def add_client(self, client):
        #rescale position to the visible area
        position = client.xlim[0]*self._scale, client.ylim[0]*self._scale
        width = client.xrange * self._scale
        aspect = client.xrange / client.yrange

        subtext = ''
        if client.resolution is not None:
            subtext = f'{client.resolution[0]}x{client.resolution[1]}'

        screen = ServerScreen(self, client.hostname, 
            subtext, 
            position=position, 
            width=width, 
            aspect=aspect)
        self.show_all()
        self.screens[screen] = client

    def update(self, screen, position, width):
        x = int(position[0] / self._scale)
        y = int(position[1] / self._scale)
        width /= self._scale
        height = width / screen.aspect
        logger.info(f'Updating screen {screen.name} to ({x},{y}), width {width}')

        client = self.screens[screen]
        client.position((x, y), (x+width, y+height))
        self.app.server.update_buffer()
        db.update_client(client)    

    def put(self, client):
        x = client.position[0] + self.origin[0]
        y = client.position[1] + self.origin[1]
        self.fixed.put(client, x, y)

    def move(self, client, x, y):
        self.fixed.move(client, x+self.origin[0], y+self.origin[1])

    def collide_position(self, client, x, y):
        for screen in self.screens.keys():
            if screen != client:
                top, right, bottom, left = screen.collide((x, y), client.width, client.height)
                m = max(top, max(right, max(bottom, left)))
                if m == top and top < 0:
                    y += top
                elif m == right and right < 0:
                    x -= right
                elif m == bottom and bottom < 0:
                    y -= bottom
                elif m == left and left < 0:
                    x += left

        return x, y

    def collide_width(self, client, width, height):
        for screen in self.screens.keys():
            if screen != client:
                top, right, bottom, left = screen.collide(client.position, width, height)
                m = max(top, max(right, max(bottom, left)))
                if m == top and top < 0:
                    height += top
                    width = height * client.aspect
                elif m == right and right < 0:
                    width -= right
                    height = width / client.aspect
                elif m == bottom and bottom < 0:
                    height -= bottom
                    width = height * client.aspect
                elif m == left and left < 0:
                    width += left
                    height = width / client.aspect

        return width, height

    def pan(self, x, y):
        pass
        

class ServerScreen(Gtk.Fixed):
    GtkIconTheme = Gtk.IconTheme.get_default()
    def __init__(self, server, name, subtext, position=(0,0), width=384, aspect=1.5, resizeable=True):
        super(ServerScreen, self).__init__()
        self.server = server
        self.position = position
        self.width = width
        self.aspect = aspect
        self.name = name

        self.screen = Gtk.Button()
        self.screen.set_size_request(self.width, self.height)
        self.put(self.screen, 0, 0)

        vbox = Gtk.VBox()
        name_label = Gtk.Label()
        name_label.set_markup(f"<b>{name}</b>")
        res_label = Gtk.Label(label=subtext)
        vbox.add(name_label)
        vbox.add(res_label)
        self.screen.add(vbox)

        #always connect the mouse button down to 
        self.screen.connect('button-press-event', self.button_down)
        if resizeable:
            self.screen.connect('button-release-event', self.finalize_position)
            self.screen.connect('motion-notify-event', self.drag)

            self.corner = Gtk.Button()
            self.corner.set_size_request(20, 20)
            self.put(self.corner, self.width-10, self.height-10)
            self.corner.connect('button-press-event', self.button_down)
            self.corner.connect('button-release-event', self.finalize_corner)
            self.corner.connect('motion-notify-event', self.drag_corner)
            self.corner.set_tooltip_markup(f'Resize client {self.name}')

            del_icon = self.GtkIconTheme.load_icon("list-remove", 16, 0)
            del_icon = Gtk.Image.new_from_pixbuf(del_icon)
            self.delbtn = Gtk.Button(image=del_icon)
            self.delbtn.set_tooltip_markup(f"Delete client {self.name}")
            self.put(self.delbtn, -12, -12)

            disconn_icon = self.GtkIconTheme.load_icon("window-close", 16, 0)
            disconn_icon = Gtk.Image.new_from_pixbuf(disconn_icon)
            self.disconnect = Gtk.Button(image=disconn_icon)
            self.disconnect.set_size_request(24, 24)
            self.disconnect.set_tooltip_markup(f"Disconnect client {self.name}")
            self.put(self.disconnect, self.width-12, -12)
        else:
            self.screen.connect('motion-notify-event', self.pan_server)

        logger.debug(f'Adding host {name} at ({self.position[0]}, {self.position[1]})')
        self.server.put(self)

    @property
    def height(self):
        return self.width / self.aspect

    def button_down(self, button, event):
        self._start = event.x_root, event.y_root

    def drag(self, widget, event):
        x = self.position[0] + event.x_root - self._start[0]
        y = self.position[1] + event.y_root - self._start[1]
        x, y = self.server.collide_position(self, x, y)
        self.server.move(self, x, y)

    def finalize_position(self, button, event):
        x = self.position[0] + event.x_root - self._start[0]
        y = self.position[1] + event.y_root - self._start[1]
        self.position = self.server.collide_position(self, x, y)
        self.server.update(self, self.position, self.width)

    def drag_corner(self, widget, event):
        width = self.width + event.x_root - self._start[0]
        height = width / self.aspect
        width, height = self.server.collide_width(self, width, height)
        self.screen.set_size_request(width, height)
        self.move(self.corner, width-10, height-10)
        self.move(self.disconnect, width-12, -12)

    def finalize_corner(self, button, event):
        width = self.width + event.x_root - self._start[0]
        height = width / self.aspect
        width, height = self.server.collide_width(self, width, height)
        self.width = width
        self.server.update(self, self.position, self.width)

    def pan_server(self, widget, event):
        x = event.x_root - self._start[0]
        y = event.y_root - self._start[1]
        self.server.pan(x, y)

    def collide(self, position, width, height):
        left = self.position[0] - (position[0] + width)
        right = position[0] - (self.position[0] + self.width)
        top = self.position[1] - (position[1] + height)
        bottom = position[1] - (self.position[1] + self.height)
        return top, right, bottom, left

def connect_dialog(callback):
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.OK_CANCEL,
        text="Connect to server")
    dialog.set_title("pymouseshift client")


    box = dialog.get_content_area()
    entry = Gtk.Entry()
    entry.set_size_request(250,0)
    def submit(widget):
        callback(entry.get_text())
        dialog.destroy()
    entry.connect('activate', submit)
    box.pack_end(entry, False, False, 0)

    def okbutton(widget, resp):
        if resp == Gtk.ResponseType.OK:
            callback(entry.get_text())
        dialog.destroy()
    dialog.connect('response', okbutton)
    dialog.show_all()

def confirm_dialog(msg, confirm, cancel=None, title='New pymouseshift Client'):
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.YES_NO,
        text=title)
    dialog.format_secondary_markup(msg)

    def callback(widget, resp):
        if resp == Gtk.ResponseType.YES:
            confirm()
        elif cancel is not None:
            cancel()
        widget.destroy()
    dialog.connect('response', callback)
    dialog.show()

if __name__ == "__main__":
    server = ServerPrefs()
    server.connect('destroy', Gtk.main_quit)
    server.add_client("corvus", (1920, 1200), position=[300, 0])
    server.add_client("lyra", (1920, 1200), position=[-100, 0])
    Gtk.main()
