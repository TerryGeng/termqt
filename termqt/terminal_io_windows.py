import os
import logging
import threading
import winpty
from .terminal_io import TerminalIO


class TerminalWinptyIO(TerminalIO):
    # This class provides io functions that communciate with the Terminal
    # and the pty (pseudo-tty) of a program.

    def __init__(self, cols: int, rows: int, cmd: str, env=None, logger=None):
        # Initilize.
        #
        # args: cols: columns
        #       rows: rows
        #       cmd: the command to execute.
        #       env: environment variables, if None, set it to os.environ
        self.env = env if env else os.environ
        self.cmd = cmd
        self.logger = logger if logger else logging.getLogger()
        self.cols = cols
        self.rows = rows
        self.running = False

        self._read_buf = b""

        self.terminated_callback = lambda: None
        self.stdout_callback = lambda bs: None

    def spawn(self):
        # Spawn the sub-process in pty.
        try:
            self.running = True
            self.pty_process = winpty.PtyProcess.spawn(
                self.cmd,
                dimensions=(self.rows, self.cols),
                backend=1,
            )
            self.pty_process.setwinsize(self.cols, self.rows)

            self._read_thread = threading.Thread(
                name="TerminalIO Read Loop",
                target=self._read_loop,
                daemon=True
            )
            self._read_thread.start()
        except:
            self.running = False
            self.terminated_callback()

    def resize(self, rows, cols):
        try:
            self.cols = cols
            self.rows = rows
            if self.running:
                self.pty_process.setwinsize(self.rows, self.cols)
        except (OSError, AssertionError):
            self.running = False
            self.terminated_callback()

    def write(self, buffer: bytes):
        self.logger.debug("stdin: " + str(buffer))
        if not self.running:
            return
        try:
            self.pty_process.write(buffer.decode("utf-8"))
        except (OSError, AssertionError):
            self.running = False
            self.terminated_callback()

    def _read_loop(self):
        # read loop to be run in a separated thread
        try:
            while self.running:
                buf = self.pty_process.read()
                if not buf:
                    continue
                elif len(buf) == 0:
                    continue
                if isinstance(buf, str):
                    self.stdout_callback(bytes(buf, 'utf-8'))
                else:
                    self.stdout_callback(buf)
        finally:
            self.logger.info("Spawned process has been killed")
            if self.running:
                self.running = False
                self.terminated_callback()

    def terminate(self):
        if self.running:
            self.pty_process.terminate(force=True)
            self.running = False

    def is_alive(self):
        try:
            return self.pty_process.isalive()
        except OSError:
            self.running = False
            return False


class TerminalExecIO(TerminalWinptyIO):
    def __init__(self, cols: int, rows: int, cmd: str, env=None, logger=None):
        # Initilize.
        #
        # args: cols: columns
        #       rows: rows
        #       cmd: the command to execute.
        #       env: environment variables, if None, set it to os.environ
        super().__init__(cmd, cols, rows, logger)
        self.env = env if env else os.environ
