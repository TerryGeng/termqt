import logging
from collections import deque
from typing import NamedTuple
from enum import Enum

from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QPainter, QColor, QPalette, QFontDatabase, QPen, \
    QFont, QFontInfo, QFontMetrics, QPixmap
from PyQt5.QtCore import Qt, QTimer, QMutex

from .colors import colors8, colors16, colors256


DEFAULT_FG_COLOR = Qt.white
DEFAULT_BG_COLOR = Qt.black


class ControlChar(Enum):
    NUL = 0   # Ctrl-@, null
    ETX = 3   # Ctrl-C, end of text
    EOT = 4   # Ctrl-D, end of transmit
    BEL = 7   # Ctrl-G, bell
    BS = 8    # Ctrl-H, backspace
    TAB = 9   # Ctrl-I, tab
    LF = 10   # Ctrl-J, NL line feed, new line
    CR = 13   # Ctrl-M, carriage return
    ESC = 27  # Ctrl-[, escape


class Char(NamedTuple):
    char: str
    cursor: bool = False
    color: QColor = None
    bg_color: QColor = None
    bold: bool = False
    underline: bool = False
    reverse: bool = False


class Position(NamedTuple):
    x: int
    y: int


class CursorState(Enum):
    ON = 1
    OFF = 2
    UNFOCUSED = 3


class EscapeProcessor:
    # A state machine used to process control sequences, etc.

    class State(Enum):
        # state of the state machine
        # initial state, 0
        WAIT_FOR_ESC = 0
        # once entered, reset all buffers
        # if receives a ESC, transfer to 1

        WAIT_FOR_BRAC = 1
        # if receive a [, transfer to 2
        # if receive a letter, save to cmd buffer, transfer to 4
        # otherwise return to 0

        WAIT_FOR_FIRST_ARG = 2
        # if receives a number, append to arg1 buffer, stays at 2
        # or after receive a colon, transfer to 3
        # if receive a letter, save to cmd buffer, transfer to 4
        # otherwise return to 0

        WAIT_FOR_SECOND_ARG = 3
        # if receives a number, append to arg2 buffer, stays at 3
        # or after receive a colon, transfer to 4
        # if receive a letter, save to cmd buffer, transfer to 5

        WAIT_FOR_THIRD_ARG = 4
        # if receives a number, append to arg3 buffer, stays at 4
        # if receive a letter, save to cmd buffer, transfer to 5

        COMPLETE = 5
        # once entered, process the input and return to 0

    def __init__(self, logger):
        self.logger = logger
        self._state = self.State.WAIT_FOR_ESC
        self._args = [''] * 3
        self._cmd = ""
        self._buffer = ""

        self._cmd_func = {}
        self._cmd_func['n'] = self._cmd_n
        self._cmd_func['m'] = self._cmd_m
        self._cmd_func['J'] = self._cmd_J
        self._cmd_func['H'] = self._cmd_H
        self._cmd_func['K'] = self._cmd_K
        self._cmd_func['K'] = self._cmd_K
        self._cmd_func['A'] = self._cmd_A
        self._cmd_func['B'] = self._cmd_B
        self._cmd_func['C'] = self._cmd_C
        self._cmd_func['D'] = self._cmd_D

        # ==== Callbacks ====

        # Erase In Display
        #  mode:
        #      0: from cursor to the end of screen
        #      1: from start of screen to cursor
        #      2: the entire screen
        self.erase_in_display_cb = lambda mode: None

        # Erase In Line
        #  mode:
        #      0: from cursor to the end of line
        #      1: from start of line to cursor
        #      2: the entire line
        self.erase_in_line_cb = lambda mode: None

        # Cursor Position(absolute position)
        #  set the position of the cursor
        #  pos_r: row (begin from 0)
        #  pos_c: column
        self.set_cursor_abs_position_cb = lambda pos_r, pos_c: None

        # Cursor Position(relative position)
        #  set the position of the cursor
        #  offset_r: row (begin from 0)
        #  offset_c: column
        self.set_cursor_rel_position_cb = lambda offset_x, offset_c: None

        # Cursor Position Report
        #  return the position of the cursor in the format of
        #   \x1b[{row}{col}R
        #  NOTE: row and col begin from 1
        self.report_cursor_position_cb = lambda: None

        # Device Status Report
        #   return in the format of \x1b[{ret}n
        #  ret 0: ready
        #      3: malfunction
        self.report_device_status_cb = lambda: None

        # Set Style
        #  set the style for future characters
        # ret: color, bgcolor, bold, underlined, reverse
        #      color, bgcolor is QColor, None means unspecified,
        #      the other three flags: -1 means unspecified,
        #        0 means false, 1 means true
        self.set_style_cb = lambda color, bgcolor, bold, \
            underlined, reverse: None

    def input(self, c: int):
        # process input character c, c is the ASCII code of input.
        #
        # return:
        #   - -1, if input is not part of a control sequence,
        #   - 0, if input is part of a control sequence,
        #   - 1, if the input finishes a control sequence
        # otherwise return True.

        if self._state != self.State.WAIT_FOR_ESC:
            self._buffer += chr(c)

        if self._state == self.State.WAIT_FOR_ESC:
            if c == ControlChar.ESC.value:
                self._enter_state(self.State.WAIT_FOR_BRAC)
            else:
                return -1

        elif self._state == self.State.WAIT_FOR_BRAC:
            if c == 91:  # ord('[')
                self._enter_state(self.State.WAIT_FOR_FIRST_ARG)
            elif 65 <= c <= 90 or 97 <= c <= 122:  # letters, A-Z, a-z
                self._cmd = chr(c)
                self._enter_state(self.State.COMPLETE)
                return 1
            else:
                self.fail()

        elif self._state == self.State.WAIT_FOR_FIRST_ARG:
            if 48 <= c <= 57:  # digits, 0-9
                self._args[0] += chr(c)
            elif c == 59:  # ord(';')
                self._enter_state(self.State.WAIT_FOR_SECOND_ARG)
                return 1
            elif 65 <= c <= 90 or 97 <= c <= 122:  # letters, A-Z, a-z
                self._cmd = chr(c)
                self._enter_state(self.State.COMPLETE)
                return 1
            else:
                self.fail()

        elif self._state == self.State.WAIT_FOR_SECOND_ARG:
            if 48 <= c <= 57:  # digits, 0-9
                self._args[1] += chr(c)
            elif c == 59:  # ord(';')
                self._enter_state(self.State.WAIT_FOR_THIRD_ARG)
            elif 65 <= c <= 90 or 97 <= c <= 122:  # letters, A-Z, a-z
                self._cmd = chr(c)
                self._enter_state(self.State.COMPLETE)
                return 1
            else:
                self.fail()

        elif self._state == self.State.WAIT_FOR_THIRD_ARG:
            if 48 <= c <= 57:  # digits, 0-9
                self._args[2] += chr(c)
            elif 65 <= c <= 90 or 97 <= c <= 122:  # letters, A-Z, a-z
                self._cmd = chr(c)
                self._enter_state(self.State.COMPLETE)
                return 1
            else:
                self.fail()

        elif self._state == self.State.COMPLETE:
            # this branch should never be reached
            self.fail()

        return 0

    def _enter_state(self, _state):
        if _state == self.State.COMPLETE:
            self._state = self.State.COMPLETE
            self._process_command()
            self.reset()
        else:
            self._state = _state

    def _process_command(self):
        assert self._state == self.State.COMPLETE

        if self._cmd in self._cmd_func:
            self.logger.info(f"escape: fired {self._buffer}")
            self._cmd_func[self._cmd]()
            self.reset()
        else:
            self.fail()

    def _get_args(self, ind, default=None):
        return int(self._args[ind]) if self._args[ind] else default

    def reset(self):
        self._args = [''] * 3
        self._cmd = ""
        self._buffer = ""
        self._state = self.State.WAIT_FOR_ESC

    def fail(self):
        raise ValueError("Unable to process escape sequence "
                         f"\\x1b[{self._buffer}.")

    def _cmd_n(self):
        # DSR – Device Status Report
        arg = self._get_args(0, default=0)
        if arg == 6:
            self.report_cursor_position_cb()
        else:
            self.report_device_status_cb()

    def _cmd_J(self):
        # ED – Erase In Display
        self.erase_in_display_cb(self._get_args(0, default=0))

    def _cmd_K(self):
        # EL – Erase In Line
        self.erase_in_line_cb(self._get_args(0, default=0))

    def _cmd_H(self):
        # CUP – Cursor Position
        self.set_cursor_abs_position_cb(
            self._get_args(0, default=1) - 1,  # begin from 1 -> begin from 0
            self._get_args(1, default=1) - 1
        )

    def _cmd_A(self):
        # Cursor Up
        self.set_cursor_rel_position_cb(0, -1 * self._get_args(0, default=1))

    def _cmd_B(self):
        # Cursor Down
        self.set_cursor_rel_position_cb(0, +1 * self._get_args(0, default=1))

    def _cmd_C(self):
        # Cursor Right
        self.set_cursor_rel_position_cb(-1 * self._get_args(0, default=1), 0)

    def _cmd_D(self):
        # Cursor Left
        self.set_cursor_rel_position_cb(+1 * self._get_args(0, default=1), 0)

    def _cmd_m(self):
        # Colors and decorators
        color = None
        bg_color = None
        bold, underline, reverse = -1, -1, -1

        arg0 = self._get_args(0, default=0)
        arg1 = self._get_args(1, default=0)
        arg2 = self._get_args(2, default=0)

        if arg0 == 0:
            bold, underline, reverse = 0, 0, 0
            color = DEFAULT_FG_COLOR
            bg_color = DEFAULT_BG_COLOR
        elif arg0 == 1:
            bold = 1
        elif arg0 == 4:
            underline = 1
        elif arg0 == 7:
            reverse = 1

        elif 30 <= arg0 <= 37:
            if arg1 == 0:  # foreground 8 colors
                color = colors8[arg0]
            elif arg1 == 1:  # foreground 16 colors
                color = colors16[arg0]

        elif 40 <= arg0 <= 47:
            if arg1 == 0:  # background 8 colors
                bg_color = colors8[arg0 - 10]
            elif arg1 == 1:  # background 16 colors
                bg_color = colors16[arg0 - 10]

        elif arg0 == 38 and arg1 == 5 and 0 <= arg2 <= 255:  # xterm 256 colors
            color = colors256[arg2]

        elif arg0 == 48 and arg1 == 5 and 0 <= arg2 <= 255:  # xterm 256 colors
            bg_color = colors256[arg2]

        self.set_style_cb(color, bg_color, bold, underline, reverse)


class Terminal(QWidget):
    def __init__(self, width, height, logger=None):
        super().__init__()

        self.logger = logger if logger else logging.getLogger()
        self.logger.info("Initializing Terminal...")

        # we paint everything to the pixmap first then paint this pixmap
        # on paint event. This allows us to partially update the canvas.
        self._canvas = QPixmap(width, height)
        self._canvas_lock = QMutex()

        # initialize a buffer to store all characters to display
        # define in _resize()_ as a deque
        self._buffer = None
        self._buffer_lock = QMutex()
        # used to store the line number of lines that are wrapped automatically
        # in order to behave correctly when resizing the widget.
        self._line_wrapped_flags = None
        # used to store which part of the buffer is visible.
        self._buffer_display_offset = None
        self._cursor_position = Position(0, 0)

        # stores user's input when terminal is put in canonical mode
        self._input_buffer = ""
        self._input_buffer_cursor = 0

        # initialize basic styling and geometry
        self.fg_color = None
        self.bg_color = None
        # three terminal char styles
        self.bold = False
        self.underline = False
        self.reverse = False

        self.font = None
        self.char_width = None
        self.char_height = None
        self.line_height = None
        self.row_len = None
        self.col_len = None
        self.dpr = self.devicePixelRatioF()

        self.set_bg(DEFAULT_BG_COLOR)
        self.set_fg(DEFAULT_FG_COLOR)
        self.set_font()
        self.setAutoFillBackground(True)
        self.setMinimumSize(width, height)

        # intializing blinking cursor
        self._cursor_blinking_lock = QMutex()
        self._cursor_blinking_state = CursorState.ON
        self._cursor_blinking_elapse = 0
        self._cursor_blinking_timer = QTimer()
        self._cursor_blinking_timer.timeout.connect(self._blink_cursor)
        self._switch_cursor_blink(state=CursorState.ON, blink=True)

        self.setFocusPolicy(Qt.StrongFocus)

        # terminal options
        self.echo = True
        self.canonical_mode = True

        # escape sequence processor
        self.escape_processor = EscapeProcessor(logger)
        self._register_escape_callbacks()

        # callbacks
        self.stdin_callback = lambda t: print(t)

    def _register_escape_callbacks(self):
        ep = self.escape_processor
        ep.erase_in_display_cb = self.cb_erase_in_display
        ep.erase_in_line_cb = self.cb_erase_in_line
        ep.set_cursor_abs_position_cb = self.cb_set_cursor_abs_pos
        ep.set_cursor_rel_position_cb = self.cb_set_cursor_rel_pos
        ep.report_device_status_cb = lambda: self.stdin_callback("\x1b[0n")
        ep.report_cursor_position_cb = self.cb_report_cursor_pos
        ep.set_style_cb = self.cb_set_style

    def set_bg(self, color: QColor):
        self.bg_color = color

        pal = self.palette()
        pal.setColor(QPalette.Background, color)
        self.setPalette(pal)

    def set_fg(self, color: QColor):
        self.fg_color = color
        pal = self.palette()
        pal.setColor(QPalette.Foreground, color)
        self.setPalette(pal)

    def set_font(self, font: QFont = None):
        qfd = QFontDatabase()

        if font:
            info = QFontInfo(font)
            if info.styleHint() != QFont.Monospace:
                self.logger.warning("font: Please use monospaced font! "
                                    f"Unsupported font {info.family}.")
                font = qfd.systemFont(QFontDatabase.FixedFont)
        elif "Menlo" in qfd.families():
            font = QFont("Menlo")
            info = QFontInfo(font)
        else:
            font = qfd.systemFont(QFontDatabase.FixedFont)
            info = QFontInfo(font)

        font.setPointSize(12)
        self.font = font
        metrics = QFontMetrics(font)
        self.char_width = metrics.horizontalAdvance("A")
        self.char_height = metrics.height()
        self.line_height = int(self.char_height * 1.2)

        self.logger.info(f"font: Font {info.family()} selected, character "
                         f"size {self.char_width}x{self.char_height}.")

    def resizeEvent(self, event):
        self.resize(event.size().width(), event.size().height())

    # ==========================
    #      PAINT FUNCTIONS
    # ==========================

    def paintEvent(self, event):
        _qp = QPainter(self)
        _qp.setRenderHint(QPainter.Antialiasing)
        _qp.drawPixmap(0, 0, self._canvas)
        super().paintEvent(event)

    def _paint_buffer(self, invoke_repaint_evt=True):
        self._canvas_lock.lock()

        self._canvas = QPixmap(self.row_len * self.char_width * self.dpr,
                               int((self.col_len + 0.2)
                                   * self.line_height * self.dpr))
        self._canvas.setDevicePixelRatio(self.dpr)

        qp = QPainter(self._canvas)
        qp.fillRect(self.rect(), self.bg_color)
        if not self._buffer:
            return

        cw = self.char_width
        ch = self.char_height
        lh = self.line_height
        ft = self.font
        fg_color = self.fg_color

        ht = 0

        offset = self._buffer_display_offset

        qp.fillRect(self.rect(), DEFAULT_BG_COLOR)

        for ln in range(self.col_len):
            row = self._buffer[ln + offset]

            ht += lh
            for cn, c in enumerate(row):
                if c and not c.cursor:
                    ft.setBold(c.bold)
                    ft.setUnderline(c.underline)
                    qp.setFont(ft)
                    if not c.reverse:
                        qp.fillRect(cn*cw, int(ht - 0.8*ch), cw, lh,
                                    c.bg_color)
                        qp.setPen(c.color)
                        qp.drawText(cn*cw, ht, c.char)
                    else:
                        qp.fillRect(cn*cw, int(ht - 0.8*ch), cw, lh, c.color)
                        qp.setPen(c.bg_color)
                        qp.drawText(cn*cw, ht, c.char)
                else:
                    qp.setPen(fg_color)
                    ft.setBold(False)
                    ft.setUnderline(False)
                    qp.setFont(ft)
                    qp.drawText(ht, cn*cw, " ")

        self._canvas_lock.unlock()
        if invoke_repaint_evt:
            self.repaint()

    def _paint_cursor(self, invoke_repaint_evt=True):
        if not self._buffer:
            return

        self._canvas_lock.lock()
        ind_x = self._cursor_position.x
        ind_y = self._cursor_position.y
        x = self._cursor_position.x * self.char_width
        y = (self._cursor_position.y - self._buffer_display_offset) \
            * self.line_height + (self.line_height - self.char_height) \
            + int(0.2 * self.line_height)

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
                bg = self.fg_color
                fg = self.bg_color

            qp.fillRect(x, y, cw, ch, bg)
        qp.setPen(fg)
        qp.setFont(self.font)

        cy = (self._cursor_position.y - self._buffer_display_offset + 1) \
            * self.line_height
        if self._buffer[ind_y][ind_x]:
            qp.drawText(x, cy, self._buffer[ind_y][ind_x].char)
        else:
            qp.drawText(x, cy, " ")

        self._canvas_lock.unlock()

        if invoke_repaint_evt:
            self.repaint()

    def _canvas_repaint(self):
        self._paint_buffer(False)
        self._paint_cursor()

    def cb_set_style(self, color, bg_color, bold, underline, reverse):
        self.fg_color = color if color else self.fg_color
        self.bg_color = bg_color if bg_color else self.bg_color
        self.bold = bool(bold) if bold != -1 else self.bold
        self.underline = bool(underline) if underline != -1 else self.underline
        self.reverse = bool(reverse) if reverse != -1 else self.reverse

    # ==========================
    #  SCREEN BUFFER FUNCTIONS
    # ==========================

    def clear_buffer(self):
        _new_buffer = deque([[None for x in range(self.row_len)]
                             for i in range(self.col_len)])
        _new_wrap = deque([False for i in range(self.col_len)])

        self._buffer = _new_buffer
        self._line_wrapped_flags = _new_wrap

    def resize(self, width, height):
        super().resize(width, height)

        row_len = int(width / self.char_width)    # Avoid "." inside the loop
        col_len = int(height / self.line_height)
        cur_x = self._cursor_position.x
        cur_y = self._cursor_position.y

        if self._buffer:
            old_row_len = self.row_len
            old_buf_col_len = len(self._buffer)

            if old_row_len == row_len:
                filler = col_len - len(self._buffer)
                if filler > 0:
                    for i in range(filler):
                        self._buffer.appendleft([None for x in range(row_len)])
                        self._line_wrapped_flags.appendleft(False)
                return

            self.logger.info(f"screen: resize triggered, new size ({row_len}x"
                             f"{col_len})")

            _new_buffer = deque([[None for x in range(row_len)]])
            _new_wrap = deque([False])

            new_y = 0
            new_x = 0
            breaked = False

            # linebreaks SHOULD be insert when:
            #  1. reaching the end of a not auto-breaked line
            #  2. an auto-breaked line is about to overflow
            # SHOULD NOT be inserted when:
            #  3. reaching the end of an auto-breaked line
            #  4. one empty line is breaked into two empty lines i.e. breaking
            #    whitespaces into the next line
            #
            # When the new row length is the integer multiple of the length of
            # the old row, criteria 1 and 2 will be satisfied simultaneously,
            # we must be careful not to create two linebreaks but only one.

            for y, old_row in enumerate(self._buffer):
                if y > 0:
                    # if last line was unfinished and was automantically
                    # wrapped into the next line in the old screen, this flag
                    # will be True, which means we don't need to wrap it again
                    if not self._line_wrapped_flags[y-1]:
                        if not breaked:
                            # The _breaked_ flag is used to avoid
                            # breaking the same line twice
                            # under the case that the new row length is the
                            # integer multiple of the length of the old row
                            _new_buffer.append([None for x in range(row_len)])
                            _new_wrap.append(False)
                            new_y += 1
                            new_x = 0

                for x, c in enumerate(old_row):
                    # clear _breaked_ flag
                    # note that it should only be set when the new row length
                    # is the integer multiple of the length of the old row
                    # under which we should avoid an extra line break being
                    # inserted
                    breaked = False
                    _new_buffer[new_y][new_x] = c
                    if c and c.cursor:
                        cur_x, cur_y = new_x, new_y
                    new_x += 1

                    if new_x >= row_len:
                        empty_ahead = all(map(lambda c: not c, old_row[x+1:]))

                        if y == old_buf_col_len - 1 and empty_ahead:
                            # avoid creating extra new lines after last line
                            break

                        _new_buffer.append([None for x in range(row_len)])
                        _new_wrap.append(False)
                        new_y += 1
                        new_x = 0
                        breaked = True

                        if empty_ahead and \
                                not self._line_wrapped_flags[y]:
                            # avoid wrapping a bunch of spaces into next line
                            break
                        else:
                            # set the flag for a new auto-line wrap.
                            _new_wrap[new_y-1] = True

            filler = col_len - len(_new_buffer)
            if filler > 0:
                cur_y += filler
                for i in range(filler):
                    _new_buffer.appendleft([None for x in range(row_len)])
                    _new_wrap.appendleft(False)
        else:
            self.logger.info(f"screen: resize triggered, buffer created, "
                             f"new size ({row_len}x{col_len})")
            _new_buffer = deque([[None for x in range(row_len)]
                                 for i in range(col_len)])
            _new_wrap = deque([False for i in range(col_len)])

        self._buffer_lock.lock()

        self._save_cursor_state_stop_blinking()
        self.row_len = row_len
        self.col_len = col_len
        self._buffer = _new_buffer
        self._buffer_display_offset = len(self._buffer) - self.col_len
        self._line_wrapped_flags = _new_wrap
        # self.logger.info(f"cursor: ({cur_x}, {cur_y})")
        self._cursor_position = Position(cur_x, cur_y)

        self._buffer_lock.unlock()

        self._paint_buffer(invoke_repaint_evt=False)
        self._restore_cursor_state()
        # self._log_buffer()

    def write(self, text, pos: Position = None, set_cursor=False,
              reset_offset=True):
        # _pos_ is position on the screen, not position on the buffer

        self._save_cursor_state_stop_blinking()
        buf = self._buffer
        old_cur_pos = None

        offset = len(self._buffer) - self.col_len

        if not pos:
            pos = self._cursor_position
            pos_x = pos.x
            pos_y = pos.y
        else:
            pos_x = pos.x
            pos_y = pos.y + offset

        if not set_cursor:
            old_cur_pos = self._cursor_position
            buf[old_cur_pos.y][old_cur_pos.x] = None

        color, bgcolor = self.fg_color, self.bg_color
        bold, underline, reverse = self.bold, self.underline, self.reverse

        # all chars + the cursor char
        char_list = [Char(t, False, color, bgcolor,
                          bold, underline, reverse) for t in text] \
            + [Char(' ', True, color, bgcolor, bold, underline, reverse)]

        for i, t in enumerate(char_list):
            if t.char == '\n':
                pos_x = 0
                pos_y += 1
                if pos_y == len(buf):
                    buf.append([None for x in range(self.row_len)])
                    self._line_wrapped_flags.append(False)
                continue

            buf[pos_y][pos_x] = t
            if not i == len(char_list) - 1:
                # this if-statement is for currectly setting the cursor
                # position
                pos_x += 1

            if pos_x >= self.row_len:
                pos_x = 0
                pos_y += 1
                if pos_y == len(buf):
                    buf.append([None for x in range(self.row_len)])
                    self._line_wrapped_flags.append(False)
                self._line_wrapped_flags[pos_y - 1] = True

        if set_cursor:
            # self.logger.info(f"cursor: ({pos_x}, {pos_y})")
            self._cursor_position = Position(pos_x, pos_y)
        else:
            buf[old_cur_pos.y][old_cur_pos.x] = Char(char=' ', cursor=True)

        if reset_offset:
            self._buffer_display_offset = min(len(self._buffer) - self.col_len,
                                              self._cursor_position.y)
        # self._log_buffer()
        self._paint_buffer(invoke_repaint_evt=False)
        # (leave repaint event to cursor)
        self._restore_cursor_state()

    def write_at_cursor(self, text):
        self.write(text, pos=None, set_cursor=True, reset_offset=False)
        if self._cursor_position.y - self._buffer_display_offset > \
                self.col_len - 1:
            self._buffer_display_offset = len(self._buffer) - self.col_len
            self._paint_buffer(invoke_repaint_evt=False)
            self._paint_cursor()

    def _log_buffer(self):
        self.logger.info(f"buffer: length: {len(self._buffer)}")
        self.logger.info("buffer(00): |" +
                         "-" * self.row_len +
                         "|")

        for ln in range(len(self._buffer)):
            line = self._buffer[ln]
            s = ""
            for char in line:
                if char:
                    s += char.char
                else:
                    s += " "

            self.logger.info(f"buffer({ln:2d}): |{s}|" +
                             ("x" if self._line_wrapped_flags[ln] else ""))
        self.logger.info(f"buffer({ln:2d}): |" +
                         "-" * self.row_len +
                         "|")

    def _log_screen(self):
        self.logger.info(f"screen({self.row_len}x{self.col_len}): |" +
                         "-" * self.row_len +
                         "|")

        offset = len(self._buffer) - self.col_len

        for ln in range(self.col_len):
            line = self._buffer[ln + offset]
            s = ""
            for char in line:
                if char:
                    s += char.char
                else:
                    s += " "

            self.logger.info(f"screen({self.row_len}x{self.col_len}): |{s}|" +
                             ("x" if self._line_wrapped_flags[ln + offset]
                              else ""))
        self.logger.info(f"screen({self.row_len}x{self.col_len}): |" +
                         "-"*self.row_len +
                         "|")

    def delete_at_cursor(self):
        self._save_cursor_state_stop_blinking()

        pos = self._cursor_position
        self._buffer_lock.lock()
        pos_x = pos.x
        pos_y = pos.y
        self._buffer[pos_y][pos_x] = None  # remove current cursor

        # locate character to delete
        pos_x -= 1
        if pos_x < 0:
            pos_x = self.row_len - 1
            pos_y -= 1

        if pos_y < 0:
            pos_x, pos_y = 0, 0
            self._buffer[0][0] = Char(char=' ', cursor=True)
        else:
            while pos_x > 0 and not self._buffer[pos_y][pos_x]:
                pos_x -= 1
            self._buffer[pos_y][pos_x] = Char(char=' ', cursor=True)

        if pos_y < self._buffer_display_offset:
            self._buffer_display_offset = pos_y

        self._cursor_position = Position(pos_x, pos_y)
        self._buffer_lock.unlock()

        self._restore_cursor_state()
        self._paint_buffer(invoke_repaint_evt=False)
        self._paint_cursor()

    def cb_erase_in_display(self, mode):
        buf = self._buffer
        cur_pos = self._cursor_position
        offset = self._buffer_display_offset

        if mode == 0:
            for x in range(cur_pos.x, self.row_len):
                buf[offset + cur_pos.y][x] = None

            for y in range(cur_pos.y + 1, offset + self.col_len):
                for x in range(self.row_len):
                    buf[offset + y][x] = None
        elif mode == 1:
            for y in range(offset, cur_pos.y):
                for x in range(self.row_len):
                    buf[offset + y][x] = None

            for x in range(cur_pos.x):
                buf[offset + cur_pos.y][x] = None
        else:
            for y in range(offset, offset + self.col_len):
                for x in range(self.row_len):
                    buf[offset + y][x] = None

    def cb_erase_in_line(self, mode):
        buf = self._buffer
        cur_pos = self._cursor_position
        offset = self._buffer_display_offset

        if mode == 0:
            for x in range(cur_pos.x, self.row_len):
                buf[offset + cur_pos.y][x] = None
        elif mode == 1:
            for x in range(cur_pos.x):
                buf[offset + cur_pos.y][x] = None
        else:
            for x in range(self.row_len):
                buf[offset + cur_pos.y][x] = None

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

    def _switch_cursor_blink(self, state, blink=True):
        self._cursor_blinking_lock.lock()

        if state != CursorState.UNFOCUSED and blink:
            self._cursor_blinking_timer.start(50)
        else:
            self._cursor_blinking_timer.stop()
        self._cursor_blinking_state = state

        self._cursor_blinking_lock.unlock()
        self._paint_cursor()

    def _save_cursor_state_stop_blinking(self):
        self._saved_cursor_state = self._cursor_blinking_state
        self._switch_cursor_blink(state=CursorState.ON, blink=False)

    def _restore_cursor_state(self):
        self._cursor_blinking_state = self._saved_cursor_state
        self._switch_cursor_blink(state=self._cursor_blinking_state,
                                  blink=True)

    def _keep_pos_in_screen(self, x, y):
        if y < self._buffer_display_offset:
            y = self._buffer_display_offset
        elif y >= self._buffer_display_offset + self.col_len:
            y = self._buffer_display_offset + self.col_len - 1

        if x < 0:
            x = 0
        elif x >= self.row_len:
            x = self.row_len - 1

        return x, y

    def cb_set_cursor_rel_pos(self, offset_x, offset_y):
        x = self._cursor_position.x + offset_x
        y = self._cursor_position.y + offset_y

        self._cursor_position = Position(*self._keep_pos_in_screen(x, y))

    def cb_set_cursor_abs_pos(self, x, y):
        self._cursor_position = Position(*self._keep_pos_in_screen(x, y))

    def cb_report_cursor_pos(self):
        self.stdin_callback(f"\x1b[{self._cursor_position.x + 1};"
                            f"{self._cursor_position.y + 1}")

    # ==========================
    #      USER INPUT EVENT
    # ==========================

    def clear_input_buffer(self):
        self._input_buffer_cursor = 0
        self._input_buffer = ''

    def stdout(self, string):
        for char in string:
            self._stdout_char(ord(char))

    def _stdout_char(self, char):
        try:
            self.clear_input_buffer()
            ret = self.escape_processor.input(char)
            if ret == 1:
                self._canvas_repaint()
                return
            elif ret == -1:
                if self.echo:
                    self.write_at_cursor(chr(char))

        except ValueError as e:
            self.logger.exception(e)

    def input(self, char):
        if self.echo:
            if 32 <= char <= 126 or char == ControlChar.LF.value:
                self.write_at_cursor(chr(char))

        if self.canonical_mode:
            if 32 <= char <= 126:  # oridinary characters, or LF
                self._input_buffer += chr(char)
                self._input_buffer_cursor += 1
            elif char == ControlChar.LF.value:
                self._input_buffer += chr(char)
                self._input_buffer_cursor += 1
                self.stdin_callback(self._input_buffer)
                self.clear_input_buffer()
            elif char == ControlChar.BS.value:
                self.delete_at_cursor()
                if self._input_buffer_cursor > 0:
                    self._input_buffer = self._input_buffer[0:-1]
                    self._input_buffer_cursor -= 1

    def focusInEvent(self, event):
        self._switch_cursor_blink(state=CursorState.ON, blink=True)

    def focusOutEvent(self, event):
        self._switch_cursor_blink(state=CursorState.UNFOCUSED, blink=False)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        text = event.text()

        if not modifiers:
            if key == Qt.Key_Enter or key == Qt.Key_Return:
                self.input(ControlChar.LF.value)
                return
            elif key == Qt.Key_Delete or key == Qt.Key_Backspace:
                self.input(ControlChar.BS.value)
                return
            elif key == Qt.Key_Escape:
                self.input(ControlChar.ESC.value)
                return
        elif modifiers == Qt.ControlModifier:
            if text == 'c':
                self.input(ControlChar.ETX.value)
            elif text == 'd':
                self.input(ControlChar.EOT.value)
            elif text == 'g':
                self.input(ControlChar.BEL.value)
            elif text == 'h':
                self.input(ControlChar.BS.value)
            elif text == 'i':
                self.input(ControlChar.TAB.value)
            elif text == 'j':
                self.input(ControlChar.LF.value)
            elif text == 'm':
                self.input(ControlChar.CR.value)
            elif text == '[':
                self.input(ControlChar.ESC.value)
            return

        if text:
            self.input(ord(text))
