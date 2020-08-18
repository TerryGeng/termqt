# pyqterm

_pyqterm_ is a python implementation of a terminal emulator, based on Qt. It is designed to be embedded
as a widget in other programs. It implements a subset of the functions described in
[VT100 User Guide](https://vt100.net/docs/vt100-ug/chapter3.html) and
[XTerm Control Sequence](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html).
While can't be considered as a fully functional terminal emulator like those most used by people,
it can run IPython and perhaps more usual applications.

<img src="screenshots/screenshot-1.jpg" alt="Screenshot" style="zoom:50%;" />

It is worth noting that is project is still work-in-progress and more necessary features shall be added
in coming weeks.

## Dependencies

- python 3.5+ (to get PyQt5 running),
- PyQt5.

That's it.

## Get Started

_pyqterm_ is divided into a `Terminal` widget that can be embedded in any Qt program, and a `TerminalIO`
that is used to underlying process like `bash` or `ipython`. One has to connect all callbacks inside
`Terminal` and `TerminalIO` in order to get it running. One may also build his own IO backend instead of
`TerminalIO`.

An example of getting things working is the `start.py`. It creates a `Terminal` and a `TerminalIO` instance
and connects them together.
