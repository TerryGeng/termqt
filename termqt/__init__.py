import platform
from .terminal_widget import Terminal
from .terminal_buffer import TerminalBuffer
from .terminal_io import TerminalIO

platform = platform.system()

if platform in ["Linux", "Darwin"]:
    from .terminal_io_posix import TerminalPOSIXIO, TerminalPOSIXExecIO
elif platform == "Windows":
    from .terminal_io_windows import TerminalWinptyIO
