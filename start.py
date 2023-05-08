import sys
import logging
import platform
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QScrollBar
from PyQt5.QtCore import Qt, QCoreApplication
from PyQt5.QtGui import QFont

from termqt import Terminal, TerminalExecIO

if __name__ == "__main__":
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] > "
        "[%(filename)s:%(lineno)d] %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication([])
    window = QWidget()
    window.setWindowTitle("termqt on {}".format(platform.system()))
    layout = QHBoxLayout()
    terminal = Terminal(800, 600, logger=logger)
    terminal.set_font()
    terminal.maximum_line_history = 2000
    scroll = QScrollBar(Qt.Vertical, terminal)
    terminal.connect_scroll_bar(scroll)

    layout.addWidget(terminal)
    layout.addWidget(scroll)
    layout.setSpacing(0)
    window.setLayout(layout)

    window.show()
    
    command = "/bin/bash"
    if platform.system() == "Windows":
        command = "cmd"
    
    terminal_io = TerminalExecIO(
        terminal.row_len,
        terminal.col_len,
        command,
        logger=logger
    )
    terminal_io.stdout_callback = terminal.stdout
    terminal.stdin_callback = terminal_io.write
    terminal.resize_callback = terminal_io.resize    
    terminal_io.spawn()
    
    sys.exit(app.exec())
