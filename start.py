import logging
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, \
    QPushButton
from PyQt5.QtCore import Qt, QCoreApplication

from terminal import Terminal


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

terminal.write_at_cursor("Hello world Hello world "
                         "Hello world Hello world "
                         "Hello world Hello world\n\n\n\n")

terminal._log_buffer()

app.exec_()
