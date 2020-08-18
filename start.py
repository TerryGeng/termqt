import logging
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, \
    QPushButton
from PyQt5.QtCore import Qt, QCoreApplication

from terminal import Terminal, TerminalIO


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
    layout = QVBoxLayout()
    terminal = Terminal(400, 300, logger=logger)
    button = QPushButton("Input \\x1b + text")
    edit = QLineEdit()

    layout.addWidget(terminal)
    layout.addWidget(edit)
    layout.addWidget(button)
    window.setLayout(layout)

    button.clicked.connect(lambda evt: terminal.stdout("\x1b" + edit.text()))

    window.show()

    terminal_io = TerminalIO(terminal.row_len, terminal.col_len,
                             "/bin/bash", logger=logger)
    terminal_io.stdout_callback = terminal.stdout
    terminal.stdin_callback = terminal_io.write
    terminal.resize_callback = terminal_io.resize
    terminal_io.spawn()
    # f = open("typescript", "r")
    # def test():
    #     terminal.stdout(f.read())
    #     terminal._log_screen()
    # import cProfile
    # cProfile.run('test()')
    app.exec_()
