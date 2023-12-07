import sys
import platform
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QTabWidget, QVBoxLayout, QPushButton, QScrollBar
from PyQt5.QtCore import Qt, QCoreApplication

from termqt.termqt import Terminal
from termqt.termqt.terminal_io_windows import TerminalWinptyIO
from termqt.termqt.terminal_io_posix import TerminalPOSIXExecIO


class TerminalTabWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.closeTab)
        self.layout.addWidget(self.tabs)

        self.addButton = QPushButton('New Terminal')
        self.addButton.clicked.connect(self.addTerminalTab_button)
        self.layout.addWidget(self.addButton)

    def addTerminalTab_button(self):
        if platform.system() == "Windows":
            self.addTerminalTab('cmd', 'bash')
        else:
            self.addTerminalTab('/bin/bash', 'bash')

    def addTerminalTab(self, cmd, title=None):
        terminal, scroll, terminal_io = self.createTerminal(cmd)
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.addWidget(terminal)
        layout.addWidget(scroll)
        layout.setSpacing(0)
        tab.setLayout(layout)

        index = self.tabs.addTab(tab, title or "Terminal")
        self.tabs.setCurrentIndex(index)

        # Start the terminal IO
        terminal_io.spawn()

    def closeTab(self, index):
        if self.tabs.count() > index:
            widget = self.tabs.widget(index)
            terminal_io = widget.findChild(TerminalWinptyIO)
            if terminal_io and terminal_io.is_alive():
                terminal_io.terminate()
            widget.deleteLater()
            self.tabs.removeTab(index)

    def createTerminal(self, cmd):
        terminal = Terminal(
            400, 300,  # min size
            font_size=7)
        terminal.set_font()
        terminal.maximum_line_history = 2000
        scroll = QScrollBar(Qt.Vertical, terminal)
        terminal.connect_scroll_bar(scroll)

        # Terminal IO setup
        if platform.system() == "Windows":
            terminal_io = TerminalWinptyIO(
                terminal.row_len,
                terminal.col_len,
                cmd
            )
        else:
            terminal_io = TerminalPOSIXExecIO(
                terminal.row_len,
                terminal.col_len,
                cmd
            )

        terminal_io.stdout_callback = terminal.stdout
        terminal.stdin_callback = terminal_io.write
        terminal.resize_callback = terminal_io.resize

        return terminal, scroll, terminal_io


if __name__ == "__main__":
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication([])
    main_window = TerminalTabWidget()
    main_window.setWindowTitle("termqt on {}".format(platform.system()))
    main_window.show()
    sys.exit(app.exec())
