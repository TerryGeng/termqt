import os
import time
import struct
import select
import signal
import logging
import threading
from abc import ABC, abstractmethod
from .terminal_io import TerminalIO


class TerminalPOSIXIO(TerminalIO, ABC):
    # This class provides io functions that communciate with the Terminal
    # and the pty (pseudo-tty) of a program.

    def __init__(self, cols: int, rows: int, logger=None):
        # Initilize.
        #
        # args: cols: columns
        #       rows: rows
        #       cmd: the command to execute.
        #       env: environment variables, if None, set it to os.environ
        self.logger = logger if logger else logging.getLogger()
        self.cols = cols
        self.rows = rows
        self.pid = -1
        self.fd = -1
        self.running = False

        self._read_buf = b""

        self.terminated_callback = lambda: None
        self.stdout_callback = lambda bs: None

    def spawn(self):
        # Spawn the sub-process in pty.
        import pty
        import fcntl
        import termios

        pid, fd = pty.fork()

        if pid == 0:
            # we are in the sub-process (salve)
            stdin = 0
            stdout = 1
            stderr = 2
            try:
                # This ensures that the child doesn't get the parent's FDs
                os.closerange(3, 256)
            except OSError:
                pass

            attrs = termios.tcgetattr(stdout)
            iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
            oflag |= (termios.OPOST | termios.ONLCR | termios.INLCR)
            attrs = [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
            termios.tcsetattr(stdout, termios.TCSANOW, attrs)

            attrs = termios.tcgetattr(stdin)
            iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
            oflag |= (termios.OPOST | termios.ONLCR | termios.INLCR)
            attrs = [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
            termios.tcsetattr(stdin, termios.TCSANOW, attrs)

            os.dup2(stderr, stdout)
            self.run_slave()

        else:
            # we are still in this process (master)
            self.fd = fd
            self.pid = pid
            self.logger.info(f"Pty spawned, fd: {fd}, pid: {pid}")
            self.running = True
            # set unblocking flag to keep read(size) return even when there
            # the length of available data is less than size.
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

            time.sleep(0.05)
            s = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, s)
            os.kill(self.pid, signal.SIGWINCH)

            threading.Thread(name="TerminalIO Read Loop",
                             target=self._read_loop, daemon=True).start()

    @abstractmethod
    def run_slave(self):
        # This method will be executed in the child process.
        # If you want to invoke an interactive shell or a program,
        # please start it here.
        # See TerminalExecIO for example.
        pass

    def resize(self, rows, cols):
        try:
            import fcntl
            import termios
            self.cols = cols
            self.rows = rows

            self.logger.debug(f"Terminal resize trigger: {cols}x{rows}")

            s = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, s)
            os.kill(self.pid, signal.SIGWINCH)
        except (OSError, AssertionError):
            self.running = False
            self.terminated_callback()

    def write(self, buffer: bytes):
        self.logger.debug("stdin: " + str(buffer))
        if not self.running:
            return

        try:
            assert os.write(self.fd, buffer) == len(buffer)
        except (OSError, AssertionError):
            self.running = False
            self.terminated_callback()

    def _read_loop(self):
        # read loop to be run in a separated thread
        fd = self.fd
        poll = select.poll()
        poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)

        try:
            while self.running:
                fds = poll.poll(50)  # poll for 50ms
                if not fds:
                    continue
                buf = os.read(fd, 1032)
                # 1032 % 4 == 1032 % 3 == 0, avoid truncating utf-8 char

                if len(buf) == 0:
                    break

                self.stdout_callback(buf)
        except OSError:
            pass
        finally:
            self.logger.info("Spawned process has been killed")
            if self.running:
                self.running = False
                self.terminated_callback()
                os.close(fd)

    def terminate(self):
        if self.running:
            os.kill(self.pid, signal.SIGTERM)

            def _check_killed():
                time.sleep(3000)
                if self.is_alive():
                    os.kill(self.pid, signal.SIGKILL)

            threading.Thread(target=_check_killed, daemon=True).start()
            self.running = False

    def is_alive(self):
        try:
            os.kill(self.pif, 0)
            return True
        except OSError:
            self.running = False
            return False


class TerminalPOSIXExecIO(TerminalPOSIXIO):
    def __init__(self, cols: int, rows: int, cmd: str, env=None, logger=None):
        # Initilize.
        #
        # args: cols: columns
        #       rows: rows
        #       cmd: the command to execute.
        #       env: environment variables, if None, set it to os.environ
        super().__init__(cols, rows, logger)
        self.cmd = cmd
        self.env = env if env else os.environ

    def run_slave(self):
        import shlex
        cmd = shlex.split(self.cmd)

        env = self.env
        env["COLUMNS"] = str(self.cols)
        env["LINES"] = str(self.rows)
        env["TERM"] = env.get("TERM", "xterm-256color")
        env["LANG"] = 'en_US.UTF-8'
        env["LC_CTYPE"] = 'en_US.UTF-8'
        env["PYTHONIOENCODING"] = "utf_8"

        os.execvpe(cmd[0], cmd, self.env)
