# pymouseshift
<img align="right" width="150" src="https://github.com/jamesgao/pymouseshift/raw/master/screenshot_tray.png">
pymouseshift is a program that lets you move your mouse cursor across multiple machines. It has similar functionality as [Synergy](https://symless.com/synergy).

pymouseshift works with Wayland on Linux. It does this by hijacking the mouse altogether. It computes where the mouse lies on each screen, and forwards the correct coordinates via [UInput](https://www.kernel.org/doc/html/v4.12/input/uinput.html) and [python-evdev](https://python-evdev.readthedocs.io/en/latest/).

## Features
- SSL encryption of traffic between computers
- GUI configuration of screen sizes and positions
- Remembers clients and their positions
<img width="200" src="https://github.com/jamesgao/pymouseshift/raw/master/screenshot_ssl.png">
<img width="400" src="https://github.com/jamesgao/pymouseshift/raw/master/screenshot_pref.png">

## TODO
- [ ] Copy-paste across desktop and clipboard support
- [ ] Automatic reconnection for clients
- [ ] Force disconnect a client from a server
- [ ] Windows and OSX support via [pynput](https://pynput.readthedocs.io/en/latest/)
- [ ] Installation and packaging
