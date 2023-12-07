import logging
import math
from enum import Enum

from Qt.QtWidgets import QWidget, QScrollBar, QMenu, QAction, QApplication
from Qt.QtGui import (QPainter, QColor, QPalette, QFontDatabase,
                      QPen, QFont, QFontInfo, QFontMetrics, QPixmap)
from Qt.QtCore import Qt, QTimer, QMutex, Signal

from . import colors

from .terminal_buffer import Position, TerminalBuffer, DEFAULT_BG_COLOR, \
    DEFAULT_FG_COLOR, ControlChar, Placeholder


class CursorState(Enum):
    ON = 1
    OFF = 2
    UNFOCUSED = 3


class Terminal(TerminalBuffer, QWidget):

    # Terminal widget.
    # Note: One should not call functions that begin with _, especially those
    #       linking with painting things.
    #       It is DANGEROUS to call internal painting function outside the main
    #       thread, Qt will crash immediately. Just don't do that.

    # signal for triggering a on-canvas buffer repaint
    buffer_repaint_sig = Signal()

    # signal for triggering a on-canvas cursor repaint
    cursor_repaint_sig = Signal()

    # signal for triggering a repaint for both the canvas and the widget
    total_repaint_sig = Signal()

    # internal signal for triggering stdout routine for buffering and
    # painting. Note: Use stdout() method.
    _stdout_sig = Signal(bytes)

    # update scroll bar
    update_scroll_sig = Signal()

    def __init__(self,
                 width,
                 height,
                 *,
                 logger=None,
                 padding=4,
                 font_size=12,
                 line_height_factor=1.2,
                 font=None,
                 **kwargs
                 ):

        QWidget.__init__(self)

        self.scroll_bar: QScrollBar = None

        self.logger = logger if logger else logging.getLogger()
        self.logger.info("Initializing Terminal...")

        TerminalBuffer.__init__(self, 0, 0, logger=self.logger, **kwargs)

        # we paint everything to the pixmap first then paint this pixmap
        # on paint event. This allows us to partially update the canvas.
        self._canvas = QPixmap(width, height)
        self._painter_lock = QMutex(QMutex.Recursive)

        self._width = width
        self._height = height
        self._padding = padding
        self._line_height_factor = line_height_factor

        self.font_size = font_size
        self.font = None
        self.char_width = None
        self.char_height = None
        self.line_height = None
        self.row_len = None
        self.col_len = None

        self.dpr = int(self.devicePixelRatioF())

        self.set_bg(DEFAULT_BG_COLOR)
        self.set_fg(DEFAULT_FG_COLOR)
        self.metrics = None
        self.set_font(font)
        self.setAutoFillBackground(True)
        self.setMinimumSize(width, height)

        # connect reapint signals
        self.buffer_repaint_sig.connect(self._paint_buffer)
        self.cursor_repaint_sig.connect(self._paint_cursor)
        self.total_repaint_sig.connect(self._canvas_repaint)

        # intializing blinking cursor
        self._cursor_blinking_lock = QMutex()
        self._cursor_blinking_state = CursorState.ON
        self._cursor_blinking_elapse = 0
        self._cursor_blinking_timer = QTimer()
        self._cursor_blinking_timer.timeout.connect(self._blink_cursor)
        self._switch_cursor_blink(state=CursorState.ON, blink=True)

        # scroll bar

        self.update_scroll_sig.connect(self._update_scroll_position)

        self.setFocusPolicy(Qt.StrongFocus)

        # terminal options, in case you don't want pty to handle it
        # self.echo = True
        # self.canonical_mode = True

        self._stdout_sig.connect(self._stdout)
        self.resize(width, height)

    def wheelEvent(self, event):
        # Number of lines to scroll per wheel step
        lines_per_step = 3

        # Calculate the scroll amount (positive for scroll up, negative for down)
        scroll_amount = event.angleDelta().y() // 120 * lines_per_step

        # Update buffer display offset
        self._buffer_display_offset = max(0, min(
            self._buffer_display_offset - scroll_amount, len(self._buffer) - self.col_len))

        # Update the terminal display
        self._paint_buffer()
        self.repaint()

        # Update scroll bar position if it exists
        if self.scroll_bar:
            self.update_scroll_position()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = self._map_pixel_to_cell(event.pos())
            self.set_selection_start(pos)
            self.set_selection_end(pos)
            self._paint_buffer()
            self._restore_cursor_state()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            pos = self._map_pixel_to_cell(event.pos())
            self.set_selection_end(pos)
            self._paint_buffer()
            self._restore_cursor_state()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self._show_context_menu(event.pos())

        if event.buttons() & Qt.LeftButton:
            self._paint_buffer()
            self._restore_cursor_state()

    def _show_context_menu(self, position):
        menu = QMenu(self)

        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(self._copy_selection)
        menu.addAction(copy_action)

        copy_all_action = QAction("Copy All", self)
        copy_all_action.triggered.connect(self._copy_all)
        menu.addAction(copy_all_action)

        menu.exec_(self.mapToGlobal(position))

    def _copy_all(self):
        all_text = self._get_all_text()
        clipboard = QApplication.clipboard()
        clipboard.setText(all_text)

    def _copy_selection(self):
        selected_text = self._get_selected_text()
        clipboard = QApplication.clipboard()
        clipboard.setText(selected_text)

    def _map_pixel_to_cell(self, pos):
        col = int((pos.x() - self._padding / 2) / self.char_width)
        row = int((pos.y() - self._padding / 2) / self.line_height)
        return Position(col, row + self._buffer_display_offset)

    def set_bg(self, color: QColor):
        TerminalBuffer.set_bg(self, color)

        pal = self.palette()
        pal.setColor(QPalette.Background, color)
        self.setPalette(pal)

    def set_fg(self, color: QColor):
        TerminalBuffer.set_fg(self, color)

        pal = self.palette()
        pal.setColor(QPalette.Foreground, color)
        self.setPalette(pal)

    def set_font(self, font: QFont = None):
        qfd = QFontDatabase()

        if font:
            info = QFontInfo(font)
            if info.styleHint() != QFont.Monospace:
                self.logger.warning("font: Please use monospaced font! "
                                    f"Unsupported font {info.family()}.")
                font = qfd.systemFont(QFontDatabase.FixedFont)
        elif "Menlo" in qfd.families():
            font = QFont("Menlo")
            info = QFontInfo(font)
        elif "Consolas" in qfd.families():
            font = QFont("Consolas")
            info = QFontInfo(font)
        else:
            font = qfd.systemFont(QFontDatabase.FixedFont)
            info = QFontInfo(font)

        font.setPointSize(self.font_size)
        self.font = font
        self.metrics = QFontMetrics(font)
        self.char_width = self.metrics.horizontalAdvance("A")
        self.char_height = self.metrics.height()
        self.line_height = int(self.char_height * self._line_height_factor)

        self.logger.info(f"font: Font {info.family()} selected, character "
                         f"size {self.char_width}x{self.char_height}.")

        self.row_len = int(self._width / self.char_width)
        self.col_len = int(self._height / self.line_height)

    def resizeEvent(self, event):
        self.resize(event.size().width(), event.size().height())

    # ==========================
    #      PAINT FUNCTIONS
    # ==========================

    def paintEvent(self, event):
        self._painter_lock.lock()
        _qp = QPainter(self)
        _qp.setRenderHint(QPainter.Antialiasing)
        _qp.drawPixmap(
            int(self._padding/2),
            int(self._padding/2),
            self._canvas
        )
        QWidget.paintEvent(self, event)
        self._painter_lock.unlock()

    def _paint_buffer(self):
        self._painter_lock.lock()

        self._canvas = QPixmap(self.row_len * self.char_width * self.dpr,
                               int((self.col_len + 0.2)
                                   * self.line_height * self.dpr))
        self._canvas.setDevicePixelRatio(self.dpr)

        qp = QPainter(self._canvas)
        qp.fillRect(self.rect(), self._bg_color)
        if not self._buffer:
            return

        cw = self.char_width
        ch = self.char_height
        lh = self.line_height
        ft = self.font
        fg_color = self._fg_color

        ht = 0

        offset = self._buffer_display_offset

        qp.fillRect(self.rect(), DEFAULT_BG_COLOR)

        for ln in range(self.col_len):
            real_ln = ln + offset
            if real_ln < 0 or real_ln >= len(self._buffer):
                break

            row = self._buffer[real_ln]

            ht += lh
            for cn, c in enumerate(row):
                if c:
                    is_selected = self._is_selected(cn, real_ln)
                    alt_bgcolor = None
                    if is_selected:
                        alt_bgcolor = colors.colors8[36]

                    if c.placeholder == Placeholder.NON:
                        ft.setBold(c.bold)
                        ft.setUnderline(c.underline)
                        qp.setFont(ft)
                        if not c.reverse:
                            qp.fillRect(cn*cw, int(ht - 0.8*ch), cw*c.char_width, lh,
                                        alt_bgcolor or c.bg_color)
                            qp.setPen(c.color)
                            qp.drawText(cn*cw, ht, c.char)
                        else:
                            qp.fillRect(cn*cw, int(ht - 0.8*ch), cw*c.char_width, lh,
                                        alt_bgcolor or c.color)
                            qp.setPen(c.bg_color)
                            qp.drawText(cn*cw, ht, c.char)
                else:
                    qp.setPen(fg_color)
                    ft.setBold(False)
                    ft.setUnderline(False)
                    qp.setFont(ft)
                    qp.drawText(ht, cn*cw, " ")
        qp.end()

        self._painter_lock.unlock()

    def _is_selected(self, col, row):
        if not self._selection_start or not self._selection_end:
            return False
        start_col, start_row = self._selection_start
        end_col, end_row = self._selection_end
        if start_row <= row <= end_row:
            if row == start_row and row == end_row:
                return start_col <= col <= end_col
            elif row == start_row:
                return col >= start_col
            elif row == end_row:
                return col <= end_col
            return True
        return False

    def _paint_cursor(self):
        if not self._buffer:
            return

        self._painter_lock.lock()
        ind_x = self._cursor_position.x
        ind_y = self._cursor_position.y
        # if cursor is at the right edge of screen, display half of it
        x = int((ind_x if ind_x < self.row_len else (self.row_len - 0.5)) \
                * self.char_width)
        y = int((ind_y - self._buffer_display_offset) \
                * self.line_height + (self.line_height - self.char_height) \
                + 0.2 * self.line_height)

        cw = self.char_width
        ch = self.char_height

        qp = QPainter(self._canvas)
        fg = DEFAULT_FG_COLOR
        bg = DEFAULT_BG_COLOR

        if self._cursor_blinking_state == CursorState.UNFOCUSED:
            outline = QPen(fg)
            outline.setWidth(1)
            qp.setPen(outline)
            qp.fillRect(x, y, cw, ch, bg)
            qp.drawRect(x + 1, y + 1, cw - 2, ch - 2)
        else:
            if self._cursor_blinking_state == CursorState.ON:
                bg = self._fg_color
                fg = self._bg_color

            qp.fillRect(x, y, cw, ch, bg)
        qp.setPen(fg)
        qp.setFont(self.font)

        cy = (self._cursor_position.y - self._buffer_display_offset + 1) \
            * self.line_height
        if ind_x == self.row_len:  # cursor sitting at the edge of screen
            pass
        else:
            chr_x = ind_x
            c = self._buffer[ind_y][chr_x]

            if not c:
                qp.drawText(x, cy, " ")
            elif self._cursor_blinking_state == CursorState.OFF or \
                    c.char_width == 1:
                while c and c.placeholder != Placeholder.NON:
                    chr_x -= 1
                    x -= self.char_width
                    c = self._buffer[ind_y][chr_x]

                if c:
                    qp.drawText(x, cy, c.char)
                else:
                    qp.drawText(x, cy, " ")

        qp.end()
        self._painter_lock.unlock()

    def _canvas_repaint(self):
        self._paint_buffer()
        self._paint_cursor()
        self.repaint()

    def get_char_width(self, t):
        if len(t.encode("utf-8")) == 1:
            return 1
        else:
            return math.ceil(self.metrics.horizontalAdvance(t) / self.char_width)

    # ==========================
    #  SCREEN BUFFER FUNCTIONS
    # ==========================

    def resize(self, width, height):
        self._save_cursor_state_stop_blinking()

        QWidget.resize(self, width, height)

        row_len = int((width - self._padding) / self.char_width)
        col_len = min(
            int((height - self._padding) / self.line_height),
            self.maximum_line_history
        )

        TerminalBuffer.resize(self, row_len, col_len)

        self._paint_buffer()
        self._restore_cursor_state()
        # self._log_buffer()

    def toggle_alt_screen(self, on=True):
        TerminalBuffer.toggle_alt_screen(self, on)
        self._canvas_repaint()

    def toggle_alt_screen_save_cursor(self, on=True):
        if on:
            # save current buffer
            self._alt_cursor_position = self._cursor_position
        else:
            if not self._alt_buffer:
                return
            self._cursor_position = self._alt_cursor_position

        self.toggle_alt_screen(on)

    # ==========================
    #       CURSOR CONTROL
    # ==========================

    def _blink_cursor(self):
        self._cursor_blinking_lock.lock()

        if self._cursor_blinking_state == CursorState.ON:  # On
            if self._cursor_blinking_elapse < 400:
                # 50 is the period of the timer
                self._cursor_blinking_elapse += 50
                self._cursor_blinking_lock.unlock()
                return
            else:
                self._cursor_blinking_state = CursorState.OFF
        elif self._cursor_blinking_state == CursorState.OFF:  # Off
            if self._cursor_blinking_elapse < 250:
                # 50 is the period of the timer
                self._cursor_blinking_elapse += 50
                self._cursor_blinking_lock.unlock()
                return
            else:
                self._cursor_blinking_state = CursorState.ON

        self._cursor_blinking_elapse = 0
        self._cursor_blinking_lock.unlock()

        self._paint_cursor()
        self.repaint()

    def _switch_cursor_blink(self, state, blink=True):
        self._cursor_blinking_lock.lock()

        if state != CursorState.UNFOCUSED and blink:
            self._cursor_blinking_timer.start(50)
        else:
            self._cursor_blinking_timer.stop()
        self._cursor_blinking_state = state

        self._cursor_blinking_lock.unlock()
        self._paint_cursor()
        self.repaint()

    def _save_cursor_state_stop_blinking(self):
        self._saved_cursor_state = self._cursor_blinking_state
        self._switch_cursor_blink(CursorState.ON, False)

    def _restore_cursor_state(self):
        self._cursor_blinking_state = self._saved_cursor_state
        self._switch_cursor_blink(self._cursor_blinking_state, True)

    def stdout(self, string: bytes):
        # Note that this function accepts UTF-8 only (since python use utf-8).
        # Normally modern programs will determine the encoding of its stdout
        # from env variable LC_CTYPE and for most systems, it is set to utf-8.
        self._stdout_sig.emit(string)

    def _stdout(self, string: bytes):
        # Note that this function accepts UTF-8 only (since python use utf-8).
        # Normally modern programs will determine the encoding of its stdout
        # from env variable LC_CTYPE and for most systems, it is set to utf-8.
        self._postpone_scroll_update = True
        self._buffer_lock.lock()
        need_draw = self._stdout_string(string)
        self._buffer_lock.unlock()
        if need_draw:
            self._postpone_scroll_update = False
            if self._scroll_update_pending:
                self.update_scroll_position()
            self._paint_buffer()
            self.repaint()

    def focusInEvent(self, event):
        self._switch_cursor_blink(CursorState.ON, True)

    def focusOutEvent(self, event):
        self._switch_cursor_blink(CursorState.UNFOCUSED, False)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        text = event.text()

        while True:
            # This is a one-shot loop, because I want to use 'break'
            # to jump out of this block
            if key == Qt.Key_Up:
                self.input(b'\x1b[A')
            elif key == Qt.Key_Down:
                self.input(b'\x1b[B')
            elif key == Qt.Key_Right:
                self.input(b'\x1b[C')
            elif key == Qt.Key_Left:
                self.input(b'\x1b[D')
            else:
                break  # avoid the execution of 'return'
            return

        if not modifiers:
            while True:
                # This is a one-shot loop, because I want to use 'break'
                # to jump out of this block
                if key == Qt.Key_Enter or key == Qt.Key_Return:
                    self.input(ControlChar.CR.value)
                elif key == Qt.Key_Delete or key == Qt.Key_Backspace:
                    self.input(ControlChar.BS.value)
                elif key == Qt.Key_Escape:
                    self.input(ControlChar.ESC.value)
                else:
                    break  # avoid the execution of 'return'
                return
        elif modifiers == Qt.ControlModifier or modifiers == Qt.MetaModifier:
            if key == Qt.Key_A:
                self.input(ControlChar.SOH.value)
            elif key == Qt.Key_B:
                self.input(ControlChar.STX.value)
            elif key == Qt.Key_C:
                self.input(ControlChar.ETX.value)
            elif key == Qt.Key_D:
                self.input(ControlChar.EOT.value)
            elif key == Qt.Key_E:
                self.input(ControlChar.ENQ.value)
            elif key == Qt.Key_F:
                self.input(ControlChar.ACK.value)
            elif key == Qt.Key_G:
                self.input(ControlChar.BEL.value)
            elif key == Qt.Key_H:
                self.input(ControlChar.BS.value)
            elif key == Qt.Key_I:
                self.input(ControlChar.TAB.value)
            elif key == Qt.Key_J:
                self.input(ControlChar.LF.value)
            elif key == Qt.Key_K:
                self.input(ControlChar.VT.value)
            elif key == Qt.Key_L:
                self.input(ControlChar.FF.value)
            elif key == Qt.Key_M:
                self.input(ControlChar.CR.value)
            elif key == Qt.Key_N:
                self.input(ControlChar.SO.value)
            elif key == Qt.Key_O:
                self.input(ControlChar.SI.value)
            elif key == Qt.Key_P:
                self.input(ControlChar.DLE.value)
            elif key == Qt.Key_Q:
                self.input(ControlChar.DC1.value)
            elif key == Qt.Key_R:
                self.input(ControlChar.DC2.value)
            elif key == Qt.Key_S:
                self.input(ControlChar.DC3.value)
            elif key == Qt.Key_T:
                self.input(ControlChar.DC4.value)
            elif key == Qt.Key_U:
                self.input(ControlChar.NAK.value)
            elif key == Qt.Key_V:
                self.input(ControlChar.SYN.value)
            elif key == Qt.Key_W:
                self.input(ControlChar.ETB.value)
            elif key == Qt.Key_X:
                self.input(ControlChar.CAN.value)
            elif key == Qt.Key_Y:
                self.input(ControlChar.EM.value)
            elif key == Qt.Key_Z:
                self.input(ControlChar.SUB.value)
            elif key == Qt.Key_BracketLeft:
                self.input(ControlChar.ESC.value)
            return

        if text:
            self.input(text.encode('utf-8'))

    def showEvent(self, event):
        super().showEvent(event)
        def resize(*args):
            self.resize(self.size().width(), self.size().height())
        QTimer.singleShot(0, resize)

    # ==========================
    #        SCROLL BAR
    # ==========================

    def _update_scroll_position(self):
        self.scroll_bar.setMinimum(0)
        self.scroll_bar.setMaximum(len(self._buffer) - self.col_len)
        self.scroll_bar.setSliderPosition(self._buffer_display_offset)

    def update_scroll_position(self):
        if self.scroll_bar:
            self.update_scroll_sig.emit()

    def connect_scroll_bar(self, scroll_bar: QScrollBar):
        self.scroll_bar = scroll_bar
        self.update_scroll_position()
        self.scroll_bar.valueChanged.connect(self.scroll_bar_changed)

    def scroll_bar_changed(self, pos):
        if 0 <= pos <= len(self._buffer) - self.col_len:
            self._buffer_display_offset = pos
            self._paint_buffer()
            self.repaint()
