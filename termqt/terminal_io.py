import os
import time
import struct
import select
import signal
import logging
import threading
from abc import ABC, abstractmethod


class TerminalIO(ABC):
    # This class provides io functions that communciate with the Terminal
    # and the pty (pseudo-tty) of a program.

    @abstractmethod
    def spawn(self):
        pass

    @abstractmethod
    def resize(self, rows, cols):
        pass

    @abstractmethod
    def write(self, buffer: bytes):
        pass

    @abstractmethod
    def terminate(self):
        pass

    @abstractmethod
    def is_alive(self):
        pass
