import platform

from .terminal_widget import Terminal
from .terminal_buffer import TerminalBuffer

if platform.system() == "Windows":
    from .terminal_io_windows import TerminalIO, TerminalExecIO
else:
    from .terminal_io_posix import TerminalIO, TerminalExecIO
