from copy import deepcopy
from typing import NamedTuple
from enum import Enum
from functools import partial
from collections import deque

from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt, QMutex

from .colors import colors8, colors16, colors256

DEFAULT_FG_COLOR = Qt.white
DEFAULT_BG_COLOR = Qt.black


class ControlChar(Enum):
    NUL = 0   # Ctrl-@, null
    SOH = 1   # Ctrl-A, start of heading
    STX = 2   # Ctrl-B, start of text, bash shortcut for left arrow key
    ETX = 3   # Ctrl-C, end of text
    EOT = 4   # Ctrl-D, end of transmit, bash shortcut for delete
    ENQ = 5   # Ctrl-E, enquiry, bash shortcut for end of line
    ACK = 6   # Ctrl-F, acknowledge, bash shortcut for right arrow key
    BEL = 7   # Ctrl-G, bell, bash shortcut for leave history search
    BS = 8    # Ctrl-H, backspace
    TAB = 9   # Ctrl-I, tab
    LF = 10   # Ctrl-J, NL line feed, new line
    VT = 11   # Ctrl-K, vertical tab, bash shortcut for cut after cursor
    FF = 12   # Ctrl-L, from feed, bash shortcut for clear
    CR = 13   # Ctrl-M, carriage return
    SO = 14   # Ctrl-N, shift out, bash shortcut for go down in history
    SI = 15   # Ctrl-O, shift in, bash shortcut for run command
    DLE = 16  # Ctrl-P, data line escape, bash shortcut for go up in history
    DC1 = 17  # Ctrl-Q, device control 1, bash shortcut for resume output
    DC2 = 18  # Ctrl-R, device control 2, bash shortcut for search history
    DC3 = 19  # Ctrl-S, device control 3, bash shortcut for suspend output
    DC4 = 20  # Ctrl-T, device control 4, bash shortcut for swap character
    NAK = 21  # Ctrl-U, negative acknowledge, bash shortcut for cut before cursor
    SYN = 22  # Ctrl-V
    ETB = 23  # Ctrl-W, end of xmit block, bash shortcut for cut the word before cursor
    CAN = 24  # Ctrl-X, cancel
    EM = 25,  # Ctrl-Y, end of medium, bash shortcut for paste
    SUB = 26  # Ctrl-Z, substitute
    ESC = 27  # Ctrl-[, escape



class Placeholder(Enum):
    # Placeholder for correctly display double-width characters

    # This character isn't a placeholder
    NON = 0

    # Placeholder before the double-width character
    # In the case that current line has no enough space, LEAD
    # placeholder is inserted to push this character to the next line
    LEAD = 1

    # Placeholder after the double-width character
    TAIL = 2


class Char(NamedTuple):
    char: str
    char_width: int
    placeholder: Placeholder

    # style
    color: QColor = None
    bg_color: QColor = None
    bold: bool = False
    underline: bool = False
    reverse: bool = False


class Position(NamedTuple):
    x: int
    y: int


class EscapeProcessor:
    # A state machine used to process control sequences, etc.

    class State(Enum):
        # state of the state machine
        # initial state, 0
        WAIT_FOR_ESC = 0
        # once entered, reset all buffers
        # if receives a ESC, transfer to WAIT_FOR_BRAC_OR_CHAR

        WAIT_FOR_BRAC_OR_CHAR = 1
        # if receive a [, transfer to CSI_WAIT_FOR_MARKS
        # if receive a ], transfer to OSC_WAIT_FOR_NEXT_ARG
        # if receive a letter/digit, save to cmd buffer, transfer to ESC_COMPLETE
        # otherwise return to WAIT_FOR_ESC

        CSI_WAIT_FOR_MARKS = 2
        # if receive ?, =, <, >, #, save to marks, transfer to CSI_WAIT_FOR_NEXT_ARG
        # if receive a number, append to arg buf, transfer to CSI_WAIT_FOR_ARG_FINISH
        # if receive a letter, save to cmd buffer, transfer to CSI_COMPLETE
        # otherwise return to 0

        CSI_WAIT_FOR_NEXT_ARG = 3
        # if receives a number, append to arg buf, transfer to CSI_WAIT_FOR_ARG_FINISH
        # if receive a letter, save to cmd buffer, transfer to CSI_COMPLETE
        # otherwise return to 0

        CSI_WAIT_FOR_ARG_FINISH = 4
        # if receives a number, append to arg buf, stays at CSI_WAIT_FOR_ARG_FINISH
        # if receive a colon, append arg buf to arg list, transfer to CSI_WAIT_FOR_NEXT_ARG
        # if receive a letter, save to cmd buffer, transfer to CSI_COMPLETE
        # otherwise return to WAIT_FOR_ESC

        ESC_COMPLETE = 5
        # once entered, process the input and return to WAIT_FOR_ESC

        CSI_COMPLETE = 6
        # once entered, process the input and return to WAIT_FOR_ESC

        OSC_WAIT_FOR_NEXT_ARG = 7
        # if receives a number/letter, append to osc arg buf, transfer to OSC_WAIT_FOR_ARG_FINISH
        # otherwise return to WAIT_FOR_ESC

        OSC_WAIT_FOR_ARG_FINISH = 8
        # if receives a number/letter, append to osc arg buf, stay at OSC_WAIT_FOR_ARG_FINISH
        # if receive a colon, append arg buf to arg list, transfer to OSC_WAIT_FOR_NEXT_ARG
        # if receive a BEL or ESC, transfer to OSC_COMPLETE
        # otherwise return to WAIT_FOR_ESC

        OSC_COMPLETE = 9

    def __init__(self, logger):
        self.logger = logger
        self._state = self.State.WAIT_FOR_ESC
        self._args = []
        self._arg_buf = ""
        self._cmd = ""
        self._mark = ""
        self._buffer = ""

        self._esc_func = {
            'M': self._esc_m,
        }

        self._csi_func = {
            'n': self._csi_n,
            'n?': self._csi_n,
            'm': self._csi_m,
            'P': self._csi_P,
            'A': self._csi_A,
            'B': self._csi_B,
            'C': self._csi_C,
            'D': self._csi_D,
            'G': self._csi_G,
            'H': self._csi_H,
            'J': self._csi_J,
            'K': self._csi_K,
            'M': self._csi_M,
            'h?': partial(self._csi_h_l_ext, True),
            'l?': partial(self._csi_h_l_ext, False)
        }

        # ==== Callbacks ====

        # Reverse Index
        # - if the cursor is inside the display region:
        #   - if cursor is on top-most line: scroll up by one line
        #   - else: move cursor one line up
        # - else: move cursor one line up if the resulted cursor lies inside the buffer
        self.reverse_index_cb = lambda: None

        # Erase In Display
        #  mode:
        #      0: from cursor to the end of screen
        #      1: from start of screen to cursor
        #      2: the entire screen
        self.erase_display_cb = lambda mode: None

        # Erase In Line
        #  mode:
        #      0: from cursor to the end of line
        #      1: from start of line to cursor
        #      2: the entire line
        self.erase_line_cb = lambda mode: None

        # Delete Line(s)
        #  lines: number of lines to delete
        self.delete_line_cb = lambda lines: None

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

        # Cursor Horizontal Absolute
        self.set_cursor_x_position_cb = lambda x: None

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
        self.set_style_cb = lambda color, bgcolor, bold, underlined, reverse: None

        # Save Cursor Position
        #  save cursor position and current style
        self.save_cursor_cb = lambda: None

        # Restore Cursor Position
        #  restore cursor positio and current style
        self.restore_cursor_cb = lambda: None

        # Set Window Title
        #  set the title of the terminal
        self.set_window_title_cb = lambda title: None

        # Use Alternate Screen Buffer
        #  on: bool, on/off
        self.use_alt_buffer = lambda on: None

        # Save Cursor and Use Alternate Screen Buffer
        #  on: bool, on/off
        self.save_cursor_use_alt_buffer = lambda on: None

        # Enable/Disable wraparound mode
        # https://terminalguide.namepad.de/mode/p7/
        #  on: bool, enable/disable
        self.enable_auto_wrap = lambda on: None

        # Auto-wrap
        #  on: bool, on/off

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
                self._enter_state(self.State.WAIT_FOR_BRAC_OR_CHAR)
            else:
                return -1

        elif self._state == self.State.WAIT_FOR_BRAC_OR_CHAR:
            if c == 91:  # ord('[')
                self._enter_state(self.State.CSI_WAIT_FOR_MARKS)
            elif c == 93:  # ord(']')
                self._enter_state(self.State.OSC_WAIT_FOR_NEXT_ARG)
            elif 48 <= c <= 57 or \
                    65 <= c <= 90 or 97 <= c <= 122:  # 0-9, A-Z, a-z
                self._cmd = chr(c)
                self._enter_state(self.State.ESC_COMPLETE)
                return 1
            else:
                self.fail()

        elif self._state == self.State.CSI_WAIT_FOR_MARKS:
            if c in [ord('?'), ord('#'), ord('<'), ord('>'), ord('=')]:
                self._mark = chr(c)
                self._enter_state(self.State.CSI_WAIT_FOR_NEXT_ARG)
            elif 48 <= c <= 57:  # digits, 0-9
                self._arg_buf += chr(c)
                self._enter_state(self.State.CSI_WAIT_FOR_ARG_FINISH)
            elif 65 <= c <= 90 or 97 <= c <= 122:  # letters, A-Z, a-z
                self._cmd = chr(c)
                self._enter_state(self.State.CSI_COMPLETE)
                return 1
            else:
                self.fail()

        elif self._state == self.State.CSI_WAIT_FOR_NEXT_ARG:
            if 48 <= c <= 57:  # digits, 0-9
                self._arg_buf += chr(c)
                self._enter_state(self.State.CSI_WAIT_FOR_ARG_FINISH)
            elif 65 <= c <= 90 or 97 <= c <= 122:  # letters, A-Z, a-z
                self._cmd = chr(c)
                self._enter_state(self.State.CSI_COMPLETE)
                return 1
            else:
                self.fail()

        elif self._state == self.State.CSI_WAIT_FOR_ARG_FINISH:
            if 48 <= c <= 57:  # digits, 0-9
                self._arg_buf += chr(c)
            elif c == 59:  # ord(';')
                self._args.append(int(self._arg_buf))
                self._arg_buf = ""
                self._enter_state(self.State.CSI_WAIT_FOR_NEXT_ARG)
            elif 65 <= c <= 90 or 97 <= c <= 122:  # letters, A-Z, a-z
                self._args.append(int(self._arg_buf))
                self._cmd = chr(c)
                self._enter_state(self.State.CSI_COMPLETE)
                return 1
            else:
                self.fail()

        elif self._state == self.State.CSI_COMPLETE:
            # this branch should never be reached
            self.fail()

        # === OSC ===
        elif self._state == self.State.OSC_WAIT_FOR_NEXT_ARG:
            if 20 <= c <= 126 and c != 59:  # every visible thing except ;
                self._arg_buf += chr(c)
                self._enter_state(self.State.OSC_WAIT_FOR_ARG_FINISH)
                return 1
            else:
                self.fail()

        elif self._state == self.State.OSC_WAIT_FOR_ARG_FINISH:
            if 20 <= c <= 126 and c != 59:  # every visible thing except ;
                self._arg_buf += chr(c)
                return 1
            elif c == 59:  # ord(';')
                self._args.append(self._arg_buf)
                self._arg_buf = ""
                self._enter_state(self.State.OSC_WAIT_FOR_NEXT_ARG)
                return 1
            elif c == 7 or c == 27:  # BEL, \x07 or ESC, \x1b
                self._args.append(self._arg_buf)
                self._enter_state(self.State.OSC_COMPLETE)
                return 1
            else:
                self.fail()

        elif self._state == self.State.OSC_COMPLETE:
            # this branch should never be reached
            self.fail()

        else:
            raise ValueError(f"Unhandle state {self._state}")

        return 0

    def _enter_state(self, _state):
        if _state == self.State.ESC_COMPLETE:
            self._state = self.State.ESC_COMPLETE
            self._process_esc_command()
            self.reset()
        elif _state == self.State.CSI_COMPLETE:
            self._state = self.State.CSI_COMPLETE
            self._process_csi_command()
            self.reset()
        elif _state == self.State.OSC_COMPLETE:
            self._state = self.State.OSC_COMPLETE
            self._process_osc_command()
            self.reset()
        else:
            self._state = _state

    def _process_esc_command(self):
        assert self._state == self.State.ESC_COMPLETE

        cmd = self._cmd

        if cmd in self._esc_func:
            # self.logger.debug(f"escape: fired {self._buffer}")
            self._esc_func[cmd]()
            self.reset()
        else:
            self.fail()

    def _process_csi_command(self):
        assert self._state == self.State.CSI_COMPLETE

        cmd = self._cmd if not self._mark else (self._cmd + self._mark)

        if cmd in self._csi_func:
            # self.logger.debug(f"escape: fired {self._buffer}")
            self._csi_func[cmd]()
            self.reset()
        else:
            self.fail()

    def _process_osc_command(self):
        assert self._state == self.State.OSC_COMPLETE
        try:
            op = int(self._args[0])
            if op in [0, 1, 2]:
                text = self._args[1]
                self.set_window_title_cb(text)
        except (ValueError, IndexError):
            self.fail()

    def _get_args(self, ind, default=None):
        if ind < len(self._args):
            return self._args[ind]
        else:
            return default

    def reset(self):
        self._args = []
        self._cmd = ""
        self._buffer = ""
        self._arg_buf = ""
        self._mark = ""
        self._state = self.State.WAIT_FOR_ESC

    def fail(self):
        buf = self._buffer.encode('utf-8')
        self.reset()
        raise ValueError("Unable to process escape sequence "
                         f"{buf}.")

    def _esc_m(self):
        # RI - Reverse Index
        # Move the cursor to the previous line in the scrolling region, possibly scrolling.

        self.reverse_index_cb()

    def _csi_n(self):
        # DSR – Device Status Report
        arg = self._get_args(0, default=0)
        if arg == 6:
            self.report_cursor_position_cb()
        else:
            self.report_device_status_cb()

    def _csi_J(self):
        # ED – Erase In Display
        self.erase_display_cb(self._get_args(0, default=0))

    def _csi_K(self):
        # EL – Erase In Line
        self.erase_line_cb(self._get_args(0, default=0))

    def _csi_P(self):
        self.erase_line_cb(0)

    def _csi_M(self):
        self.delete_line_cb(self._get_args(0, default=1))

    def _csi_H(self):
        # CUP – Cursor On-Screen Position
        self.set_cursor_abs_position_cb(
            self._get_args(1, default=1) - 1,
            self._get_args(0, default=1) - 1  # begin from 1 -> begin from 0
        )

    def _csi_A(self):
        # Cursor Up
        self.set_cursor_rel_position_cb(0, -1 * self._get_args(0, default=1))

    def _csi_B(self):
        # Cursor Down
        self.set_cursor_rel_position_cb(0, +1 * self._get_args(0, default=1))

    def _csi_C(self):
        # Cursor Right
        self.set_cursor_rel_position_cb(+1 * self._get_args(0, default=1), 0)

    def _csi_D(self):
        # Cursor Left
        self.set_cursor_rel_position_cb(-1 * self._get_args(0, default=1), 0)

    def _csi_G(self):
        # Cursor Horizontal Absolute
        index_in_line = self._get_args(0, default=0) - 1
        self.set_cursor_x_position_cb(index_in_line)

    def _csi_m(self):
        # Colors and decorators
        color = None
        bg_color = None
        bold, underline, reverse = -1, -1, -1

        i = 0
        while i < len(self._args):
            arg = self._get_args(i, default=0)
            if 0 <= arg <= 29:
                i += 1
                if arg == 0:
                    bold, underline, reverse = 0, 0, 0
                    color = DEFAULT_FG_COLOR
                    bg_color = DEFAULT_BG_COLOR
                elif arg == 1:
                    bold = 1
                elif arg == 4:
                    underline = 1
                elif arg == 7:
                    reverse = 1
                elif arg == 22:
                    bold = 0
                elif arg == 24:
                    underline = 0
                elif arg == 27:
                    reverse = 0
                continue

            elif 30 <= arg <= 37 or 40 <= arg <= 47 or \
                    90 <= arg <= 97 or 100 <= arg <= 107:
                if arg >= 90:
                    arg -= 60
                    arg1 = 1
                    i += 1
                else:
                    if i + 1 < len(self._args):
                        arg1 = self._get_args(i+1, default=0)
                    else:
                        arg1 = 0

                    if arg1 == 0 or arg1 == 1:
                        i += 2
                    else:
                        i += 1

                if arg <= 37:
                    if arg1 == 0:  # foreground 8 colors
                        color = colors8[arg]
                    elif arg1 == 1:  # foreground 16 colors
                        color = colors16[arg]
                elif 40 <= arg < 50:
                    if arg1 == 0:  # background 8 colors
                        bg_color = colors8[arg - 10]
                    elif arg1 == 1:  # background 16 colors
                        bg_color = colors16[arg - 10]
                continue

            elif arg == 39:
                i += 1
                color = DEFAULT_FG_COLOR
                continue

            elif arg == 49:
                i += 1
                bg_color = DEFAULT_BG_COLOR
                continue

            else:
                if i + 2 < len(self._args):  # need two consecuted args
                    arg1 = self._get_args(i+1, default=0)
                    arg2 = self._get_args(i+2, default=0)
                    i += 3
                    if arg == 38 and arg1 == 5 and 0 <= arg2 <= 255:
                        # xterm 256 colors
                        color = colors256[arg2]

                    elif arg == 48 and arg1 == 5 and 0 <= arg2 <= 255:
                        # xterm 256 colors
                        bg_color = colors256[arg2]
                    continue
                else:
                    break

        self.set_style_cb(color, bg_color, bold, underline, reverse)

    def _csi_h_l_ext(self, on):
        arg = self._get_args(0, default=0)
        if arg == 0:
            self.fail()
        elif arg == 7:
            self.enable_auto_wrap(on)
        elif arg == 47:
            self.use_alt_buffer(on)
        elif arg == 1049:
            self.save_cursor_use_alt_buffer(on)
        # else:
        #     self.logger.debug(f"escape: unknown mode {arg}")


class TerminalBuffer:
    def __init__(self,
                 row_len,
                 col_len,
                 *,
                 logger=None,
                 auto_wrap_enabled=True
                 ):
        self.logger = logger

        # initialize a buffer to store all characters to display
        # define in _resize()_ as a deque
        self._buffer = None
        self._buffer_lock = QMutex(QMutex.Recursive)

        self.auto_wrap_enabled = auto_wrap_enabled
        # used to store the line number of lines that are wrapped automatically
        # in order to behave correctly when resizing the widget.
        self._line_wrapped_flags = None

        # used to store which part of the buffer is visible.
        self._buffer_display_offset = None
        self._cursor_position = Position(0, 0)

        # stores user's input when terminal is put in canonical mode
        # in case you don't want to use system's buffer
        # self._input_buffer = ""
        # self._input_buffer_cursor = 0

        # initialize basic styling and geometry
        self._fg_color = None
        self._bg_color = None
        # three terminal char styles
        self._bold = False
        self._underline = False
        self._reversed = False

        self.row_len = row_len
        self.col_len = col_len

        self._bg_color = DEFAULT_BG_COLOR
        self._fg_color = DEFAULT_FG_COLOR

        # initialize alternative screen buffer, which is a xterm feature.
        # when activating alternative buffer, normal buffer will be saved in
        # these variables, and when toggle back, these variables will be put
        # back to self._buffer, etc.
        self._alt_buffer = None
        self._alt_line_wrapped_flags = None
        self._alt_buffer_display_offset = None
        self._alt_cursor_position = Position(0, 0)

        # scroll bar
        self._postpone_scroll_update = False
        self._scroll_update_pending = False

        # terminal options, in case you don't want pty to handle it
        # self.echo = True
        # self.canonical_mode = True

        # escape sequence processor
        self.escape_processor = EscapeProcessor(logger)
        self._register_escape_callbacks()

        # callbacks
        self.stdin_callback = lambda t: print(t)
        self.resize_callback = lambda rows, cols: None

        self.maximum_line_history = 5000

        self.create_buffer(row_len, col_len)

    def _register_escape_callbacks(self):
        ep = self.escape_processor
        ep.erase_display_cb = self.erase_display
        ep.erase_line_cb = self.erase_line
        ep.delete_line_cb = self.delete_line
        ep.reverse_index_cb = lambda: self.set_cursor_rel_pos(0, -1, False)
        ep.set_cursor_abs_position_cb = self.set_cursor_on_screen_position
        ep.set_cursor_rel_position_cb = self.set_cursor_rel_pos
        ep.set_cursor_x_position_cb = self.set_cursor_x_pos
        ep.report_device_status_cb = lambda: self.stdin_callback(
            "\x1b[0n".encode("utf-8"))
        ep.report_cursor_position_cb = self.report_cursor_pos
        ep.set_style_cb = self.set_style
        ep.use_alt_buffer = self.toggle_alt_screen
        ep.save_cursor_use_alt_buffer = self.toggle_alt_screen_save_cursor
        ep.enable_auto_wrap = self.enable_auto_wrap

    def set_bg(self, color: QColor):
        self._bg_color = color

    def set_fg(self, color: QColor):
        self._fg_color = color

    def set_style(self, color, bg_color, bold, underline, reverse):
        self._fg_color = color if color else self._fg_color
        self._bg_color = bg_color if bg_color else self._bg_color
        self._bold = bool(bold) if bold != -1 else self._bold
        self._underline = bool(underline) if underline != -1 else \
            self._underline
        self._reversed = bool(reverse) if reverse != -1 else self._reversed

    # ==========================
    #  SCREEN BUFFER FUNCTIONS
    # ==========================

    def clear_buffer(self):
        _new_buffer = deque([[None for x in range(self.row_len)]
                             for i in range(self.col_len)])
        _new_wrap = deque([False for i in range(self.col_len)])

        self._buffer = _new_buffer
        self._line_wrapped_flags = _new_wrap

    def create_buffer(self, row_len, col_len):
        _new_buffer = deque([[None for x in range(row_len)]
                             for i in range(col_len)])
        _new_wrap = deque([False for i in range(col_len)])

        self.logger.info(f"screen: buffer created, size ({row_len}x"
                         f"{col_len})")

        self.row_len = row_len
        self.col_len = col_len
        self._buffer = _new_buffer
        self._buffer_display_offset = len(self._buffer) - self.col_len
        self.update_scroll_position()
        self._line_wrapped_flags = _new_wrap
        self._cursor_position = Position(0, 0)

    def resize(self, row_len, col_len):
        cur_x = self._cursor_position.x
        cur_y = self._cursor_position.y

        do_auto_wrap = self.auto_wrap_enabled

        assert col_len <= self.maximum_line_history

        if not self._buffer:
            self.create_buffer(row_len, col_len)
            return

        self._buffer_lock.lock()

        old_row_len = self.row_len
        old_buf_col_len = len(self._buffer)

        if old_row_len == row_len:
            filler = col_len - old_buf_col_len
            if filler > 0:
                for i in range(filler):
                    self._buffer.appendleft([None for x in range(row_len)])
                    self._line_wrapped_flags.appendleft(False)
                cur_y += filler
                self._cursor_position = Position(cur_x, cur_y)

            self.col_len = col_len
            self._buffer_display_offset = len(self._buffer) - self.col_len
            self.update_scroll_position()

            self._buffer_lock.unlock()
            self.resize_callback(col_len, row_len)
            return

        old_auto_breaks_before_cursor = 0
        for i in range(cur_y):
            if self._line_wrapped_flags[i]:
                old_auto_breaks_before_cursor += 1

        self.logger.info(f"screen: resize triggered, new size ({row_len}x"
                         f"{col_len})")

        _new_buffer = deque([[None for x in range(row_len)]])
        _new_wrap = deque([False])

        new_y = 0
        new_x = 0
        new_auto_breaks_before_cursor = 0
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

            x = -1
            while x + 1 < len(old_row):
                x += 1
                c = old_row[x]

                # clear _breaked_ flag
                # note that it should only be set when the new row length
                # is the integer multiple of the length of the old row
                # under which we should avoid an extra line break being
                # inserted
                breaked = False

                if c and c.placeholder == Placeholder.LEAD:
                    continue

                width = c.char_width if c else 1

                if new_x + width > row_len:
                    # char too wide to fit into this row
                    while new_x < row_len:
                        _new_buffer[new_y][new_x] = Char("", 0, Placeholder.LEAD)
                        new_x += 1
                    x -= 1
                else:
                    _new_buffer[new_y][new_x] = c
                    new_x += 1

                if new_x >= row_len:
                    if not do_auto_wrap:
                        new_x = row_len - 1
                        continue

                    empty_ahead = all(map(lambda c: not c or c.placeholder != Placeholder.NON,
                                          old_row[x+1:]))

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
                        new_auto_break_y = new_y - 1
                        _new_wrap[new_auto_break_y] = True

                        if new_auto_break_y < cur_y:
                            new_auto_breaks_before_cursor += 1
                            if new_auto_breaks_before_cursor > old_auto_breaks_before_cursor:
                                cur_y += 1

        cur_y -= max(0, old_auto_breaks_before_cursor - new_auto_breaks_before_cursor)

        filler = old_buf_col_len - len(_new_buffer)
        if filler > 0:
            cur_y += filler

        for i in range(filler):
            _new_buffer.appendleft([None for x in range(row_len)])
            _new_wrap.appendleft(False)

        while len(self._buffer) > self.maximum_line_history:
            _new_buffer.popleft()
            self._line_wrapped_flags.popleft()
            cur_y -= 1

        self.row_len = row_len
        self.col_len = col_len
        self._buffer = _new_buffer
        self._buffer_display_offset = len(self._buffer) - self.col_len
        self.update_scroll_position()

        self._line_wrapped_flags = _new_wrap
        # self.logger.info(f"cursor: ({cur_x}, {cur_y})")
        self._cursor_position = Position(min(cur_x, row_len), cur_y)

        self._buffer_lock.unlock()

        self.resize_callback(col_len, row_len)
        # self._log_buffer()

    def write(self, text, pos: Position = None, set_cursor=False,
              reset_offset=True):
        # _pos_ is position on the screen, not position on the buffer

        self._buffer_lock.lock()

        buf = self._buffer

        offset = len(self._buffer) - self.col_len
        row_len = self.row_len

        if not pos:
            pos = self._cursor_position
            pos_x = pos.x
            pos_y = pos.y
        else:
            pos_x = pos.x
            pos_y = pos.y + offset

        color, bgcolor = self._fg_color, self._bg_color
        bold, underline, reverse = self._bold, self._underline, self._reversed
        do_auto_wrap = self.auto_wrap_enabled

        char_list = [Char(t, self.get_char_width(t), Placeholder.NON, color, bgcolor,
                          bold, underline, reverse) for t in text]

        i = -1
        while i + 1 < len(char_list):
            i += 1
            t = char_list[i]

            if t.char == '\n':
                pos_x = 0
                pos_y += 1
                if pos_y == len(buf):
                    buf.append([None for x in range(self.row_len)])
                    self._line_wrapped_flags.append(False)
                continue

            if pos_x + t.char_width > row_len:
                if do_auto_wrap:
                    for j in range(row_len - pos_x):
                        buf[pos_y][pos_x + j] = Char("", 0, Placeholder.LEAD)

                    pos_x = 0
                    pos_y += 1
                    self._line_wrapped_flags[pos_y - 1] = True
                    if pos_y == len(buf):
                        buf.append([None for x in range(self.row_len)])
                        self._line_wrapped_flags.append(False)
                else:
                    pos_x = row_len - t.char_width

            buf[pos_y][pos_x] = t
            for j in range(1, t.char_width):
                buf[pos_y][pos_x + j] = Char("", 0, Placeholder.TAIL)

            pos_x += t.char_width  # could result in pos_x == row_len when exiting loop

        while len(self._buffer) > self.maximum_line_history:
            buf.popleft()
            self._line_wrapped_flags.popleft()
            pos_y -= 1

        if set_cursor:
            # assert pos_x <= row_len
            self._cursor_position = Position(min(pos_x, row_len-1), pos_y)

        if reset_offset:
            self._buffer_display_offset = min(len(self._buffer) - self.col_len,
                                              self._cursor_position.y)
            self.update_scroll_position_postponed()

        self._buffer_lock.unlock()
        # self._log_buffer()

    def write_at_cursor(self, text):
        self.write(text, pos=None, set_cursor=True, reset_offset=False)

        y_from_screen_top = self._cursor_position.y - self._buffer_display_offset

        if y_from_screen_top > self.col_len - 1:
            self._buffer_display_offset = min(
                    self._buffer_display_offset + y_from_screen_top - self.col_len + 1,
                    len(self._buffer) - self.col_len)
            self.update_scroll_position_postponed()

    def get_char_width(self, char):
        return 1

    def _log_buffer(self):
        self.logger.info(f"buffer: length: {len(self._buffer)}")
        self.logger.info("buffer(00): |" +
                         "-" * self.row_len +
                         "|")

        cp = self._cursor_position
        for ln in range(len(self._buffer)):
            line = self._buffer[ln]
            s = ""
            for x, char in enumerate(line):
                if cp.x == x and cp.y == ln:
                    s += "█"
                elif char:
                    s += char.char
                else:
                    s += " "

            if cp.y == ln and cp.x == self.row_len:
                self.logger.info(f"buffer({ln:2d}): |{s}█" +
                                 ("x" if self._line_wrapped_flags[ln] else ""))
            else:
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

        cp = self._cursor_position
        for ln in range(self.col_len):
            line = self._buffer[ln + offset]
            s = ""
            for x, char in enumerate(line):
                if cp.x == x and cp.y == ln:
                    s += "█"
                elif char:
                    s += char.char
                else:
                    s += " "

            if cp.y == ln and cp.x == self.row_len:
                self.logger.info(f"screen({self.row_len}x{self.col_len}): "
                                 f"|{s}█" +
                                 ("x" if self._line_wrapped_flags[ln + offset]
                                  else ""))
            else:
                self.logger.info(f"screen({self.row_len}x{self.col_len}): "
                                 f"|{s}|" +
                                 ("x" if self._line_wrapped_flags[ln + offset]
                                  else ""))
        self.logger.info(f"screen({self.row_len}x{self.col_len}): |" +
                         "-"*self.row_len +
                         "|")

    def delete_at_cursor(self):
        pos = self._cursor_position
        # self._log_buffer()
        self._buffer_lock.lock()
        pos_x = pos.x
        pos_y = pos.y

        self._buffer[pos_y][pos_x] = None
        self._buffer_lock.unlock()

        # self._log_buffer()

    def erase_display(self, mode=2):
        buf = self._buffer
        cur_pos = self._cursor_position
        offset = self._buffer_display_offset

        if mode == 0:
            for x in range(cur_pos.x, self.row_len):
                buf[cur_pos.y][x] = None

            for y in range(cur_pos.y + 1, offset + self.col_len):
                for x in range(self.row_len):
                    buf[y][x] = None
        elif mode == 1:
            for y in range(offset, cur_pos.y):
                for x in range(self.row_len):
                    buf[y][x] = None

            for x in range(cur_pos.x):
                buf[cur_pos.y][x] = None
        else:
            for y in range(offset, offset + self.col_len):
                for x in range(self.row_len):
                    buf[y][x] = None

    def erase_line(self, mode=3):
        buf = self._buffer
        cur_pos = self._cursor_position

        if mode == 0:
            for x in range(cur_pos.x, self.row_len):
                buf[cur_pos.y][x] = None
        elif mode == 1:
            for x in range(cur_pos.x):
                buf[cur_pos.y][x] = None
        else:
            for x in range(self.row_len):
                buf[cur_pos.y][x] = None

    def delete_line(self, lines=1):
        buf = self._buffer
        self._buffer_lock.lock()
        cur_pos = self._cursor_position

        pos_x = 0
        pos_y = min(cur_pos.y - lines, 0)

        self._buffer_display_offset = min(self._buffer_display_offset - lines, 0)

        for y in range(min(cur_pos.y - lines + 1, 0), cur_pos.y + 1):
            del buf[y]

        if self._buffer_display_offset > len(self._buffer):
            self._buffer_display_offset = max(len(self._buffer) - 1, 0)

            self.update_scroll_position_postponed()

        self._cursor_position = Position(pos_x, pos_y)

        self._buffer_lock.unlock()

    def toggle_alt_screen(self, on=True):
        if on:
            # save current buffer
            self._alt_buffer = deepcopy(self._buffer)
            self._alt_line_wrapped_flags = deepcopy(self._line_wrapped_flags)
            self._alt_buffer_display_offset = self._buffer_display_offset

            self.erase_display()
            self.set_cursor_position(0, 0)
        else:
            if not self._alt_buffer:
                return

            self._buffer = self._alt_buffer
            self._line_wrapped_flags = self._alt_line_wrapped_flags
            self._buffer_display_offset = self._alt_buffer_display_offset
            self.update_scroll_position()

            self._alt_buffer = None
            self._alt_line_wrapped_flags = None
            self._alt_buffer_display_offset = 0

        self._bold = False
        self._underline = False
        self._reversed = False

        self._fg_color = DEFAULT_FG_COLOR
        self._bg_color = DEFAULT_BG_COLOR

    def toggle_alt_screen_save_cursor(self, on=True):
        if on:
            # save current buffer
            self._alt_cursor_position = self._cursor_position
        else:
            if not self._alt_buffer:
                return
            self._cursor_position = self._alt_cursor_position

        self.toggle_alt_screen(on)

    def enable_auto_wrap(self, on=True):
        self.auto_wrap_enabled = on

    # ==========================
    #       CURSOR CONTROL
    # ==========================

    def set_cursor_position(self, x, y):
        self._cursor_position = Position(
            *self._move_screen_with_pos(x, y)
        )

    def set_cursor_on_screen_position(self, x, y):
        pos_y = self._buffer_display_offset + y
        pos_x = x
        self.set_cursor_position(pos_x, pos_y)

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

    def _move_screen_with_pos(self, x, y):
        while x < 0:
            x = self.row_len + x
            y -= 1

        while x >= self.row_len:
            x = x - self.row_len
            y += 1

        y = max(y, 0)

        if y < self._buffer_display_offset:
            self._buffer_display_offset = y
            self.update_scroll_position_postponed()

        while y >= len(self._buffer):
            self._buffer.append([None for x in range(self.row_len)])
            self._line_wrapped_flags.append(False)

        if y >= self._buffer_display_offset + self.col_len:
            self._buffer_display_offset = y - self.col_len + 1
            self.update_scroll_position_postponed()

        return x, y

    def backspace(self, count=1):
        x = self._cursor_position.x - count
        self.set_cursor_position(
            *self._move_screen_with_pos(x, self._cursor_position.y)
        )

    def linefeed(self):
        y = self._cursor_position.y + 1
        x = 0

        self.set_cursor_position(
            *self._move_screen_with_pos(x, y)
        )

    def carriage_feed(self):
        y = self._cursor_position.y
        x = 0

        self.set_cursor_position(x, y)

    def set_cursor_rel_pos(self, offset_x, offset_y, keep_pos_in_screen=True):
        x = self._cursor_position.x + offset_x
        y = self._cursor_position.y + offset_y

        if keep_pos_in_screen:
            self.set_cursor_position(*self._keep_pos_in_screen(x, y))
        else:
            self.set_cursor_position(x, y)

    def set_cursor_x_pos(self, x: int):
        y = self._cursor_position.y

        self.set_cursor_position(x, y)

    def report_cursor_pos(self):
        x = self._cursor_position.x + 1
        y = self._cursor_position.y - self._buffer_display_offset + 1
        self.stdin_callback(f"\x1b[{y};{x}R".encode("utf-8"))

    # ==========================
    #      USER INPUT EVENT
    # ==========================

    # def clear_input_buffer(self):
    #    self._input_buffer_cursor = 0
    #    self._input_buffer = ''

    def stdout(self, string: bytes):
        # Note that this function accepts UTF-8 only (since python use utf-8).
        # Normally modern programs will determine the encoding of its stdout
        # from env variable LC_CTYPE and for most systems, it is set to utf-8.
        self._stdout_string(string)

    def _stdout_string(self, string: bytes):
        # ret: need_draw
        need_draw = False
        tst_buf = ""

        i = -1

        while i + 1 < len(string):
            i += 1
            char = string[i]

            try:
                # self.clear_input_buffer()
                ret = self.escape_processor.input(char)
                if ret == 0:
                    if tst_buf:
                        self.write_at_cursor(tst_buf)
                        tst_buf = ""
                    continue

                if ret == 1:
                    need_draw = True
                    continue

                if ret == -1:
                    if char == ControlChar.BS.value:
                        if tst_buf:
                            self.write_at_cursor(tst_buf)
                            tst_buf = ""
                        self.backspace()
                    elif char == ControlChar.CR.value:
                        if tst_buf:
                            self.write_at_cursor(tst_buf)
                            tst_buf = ""
                        self.carriage_feed()

                    elif char == ControlChar.LF.value:
                        if tst_buf:
                            self.write_at_cursor(tst_buf)
                            tst_buf = ""
                        self.write_at_cursor("\n")
                    elif char == ControlChar.TAB.value:
                        tst_buf += "        "
                    elif char == ControlChar.BEL.value:
                        # TODO: visual bell
                        pass

                    # determing utf-8 char width
                    elif char & 0xc0 == 0xc0:  # 2-byte, 3-byte, or 4-byte
                        if char & 0xe0 == 0xe0:  # 3-byte or 4-byte
                            if char & 0xf0 == 0xf0:  # 4-byte
                                tst_buf += string[i:i+4].decode("utf-8")
                                i += 3
                            else:  # 3-byte
                                tst_buf += string[i:i+3].decode("utf-8")
                                i += 2
                        else:  # 2-byte
                                tst_buf += string[i:i+2].decode("utf-8")
                                i += 1
                    else:
                        tst_buf += chr(char)

                    need_draw = True
            except ValueError as e:
                self.logger.debug(e)

        if tst_buf:
            self.write_at_cursor(tst_buf)

        return need_draw

    def input(self, char):
        if isinstance(char, bytes):
            self.stdin_callback(char)
        elif isinstance(char, int):
            self.stdin_callback(bytes([char]))

        # naive implementation for cooked mode of the terminal
        # use it if you don't want to use system's cooked mode
        #
        # if self.echo:
        #     if 32 <= char <= 126 or char == ControlChar.LF.value:
        #         self.write_at_cursor(chr(char))

        # if self.canonical_mode:
        #     if 32 <= char <= 126:  # oridinary characters, or LF
        #         self._input_buffer += chr(char)
        #         self._input_buffer_cursor += 1
        #     elif char == ControlChar.LF.value or \
        #         char == ControlChar.CR.value:
        #         self._input_buffer += chr(char)
        #         self._input_buffer_cursor += 1
        #         self.stdin_callback(self._input_buffer)
        #         self.clear_input_buffer()
        #     elif char == ControlChar.BS.value:
        #         self.delete_at_cursor()
        #         if self._input_buffer_cursor > 0:
        #             self._input_buffer = self._input_buffer[0:-1]
        #             self._input_buffer_cursor -= 1

    # ==========================
    #        SCROLLING
    # ==========================

    def scroll_down(self, lines):
        if self._buffer_display_offset + self.col_len + lines <= len(self._buffer):
            self._buffer_display_offset += lines
        else:
            self._buffer_display_offset = len(self._buffer) - self.col_len
        self.update_scroll_position()

    def scroll_up(self, lines):
        if self._buffer_display_offset - lines > 0:
            self._buffer_display_offset -= lines
        else:
            self._buffer_display_offset = 0
        self.update_scroll_position()

    def update_scroll_position(self):
        pass

    def update_scroll_position_postponed(self):
        if self._postpone_scroll_update:
            self._scroll_update_pending = True
        else:
            self.update_scroll_position()

