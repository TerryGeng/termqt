import logging
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QScrollBar
from PyQt5.QtCore import Qt, QCoreApplication

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
    layout = QHBoxLayout()
    terminal = Terminal(400, 300, logger=logger)
    scroll = QScrollBar(Qt.Vertical, terminal)
    terminal.connect_scroll_bar(scroll)

    layout.addWidget(terminal)
    layout.addWidget(scroll)
    layout.setSpacing(0)
    window.setLayout(layout)

    window.show()

    terminal_io = TerminalExecIO(terminal.row_len, terminal.col_len,
                                 "/bin/bash", logger=logger)
    terminal_io.stdout_callback = terminal.stdout
    terminal.stdin_callback = terminal_io.write
    terminal.resize_callback = terminal_io.resize
    terminal_io.spawn()

    # def test():
    #     terminal.stdout(f.read())
    #
    # import cProfile
    # cProfile.run('test()')
    app.exec_()
