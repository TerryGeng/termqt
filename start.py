import sys
import logging
import platform
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QScrollBar
from PyQt5.QtCore import Qt, QCoreApplication
from PyQt5.QtGui import QFont

import termqt
from termqt import Terminal

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

    auto_wrap_enabled = True

    platform = platform.system()

    if platform in ["Linux", "Darwin"]:
        bin = "/bin/bash"

        from termqt import TerminalPOSIXExecIO
        terminal_io = TerminalPOSIXExecIO(
                terminal.row_len,
                terminal.col_len,
                bin,
                logger=logger
                )
    elif platform == "Windows":
        bin = "cmd"

        from termqt import TerminalWinptyIO
        terminal_io = TerminalWinptyIO(
                terminal.row_len,
                terminal.col_len,
                bin,
                logger=logger
                )

        # it turned out that cmd prefers to handle resize by itself
        # see https://github.com/TerryGeng/termqt/issues/7
        auto_wrap_enabled = False
    else:
        logger.error(f"Not supported platform: {platform}")
        sys.exit(-1)

    terminal.enable_auto_wrap(auto_wrap_enabled)

    terminal_io.stdout_callback = terminal.stdout
    terminal.stdin_callback = terminal_io.write
    terminal.resize_callback = terminal_io.resize
    terminal_io.spawn()

    sys.exit(app.exec())
