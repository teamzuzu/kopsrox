#!/usr/bin/env python3

# the only module that emits ansi. degrades to plain sequential lines when
# stdout is not a tty ( NO_COLOR kills color )

import sys
import os
import time
import threading
import atexit
import shutil

RESET = '\x1b[0m'
BOLD = '\x1b[1m'
COLOR = {
    'green':  '\x1b[32m',
    'yellow': '\x1b[33m',
    'red':    '\x1b[31m',
    'cyan':   '\x1b[36m',
    'blue':   '\x1b[34m',
}

SEV = {
    'info': ('·', 'cyan',   False),
    'sys':  ('!', 'yellow', True),
    'err':  ('✗', 'red',    True),
    'done': ('✓', 'green',  False),
}

SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
KNAME_PAD = 22
BAR_WIDTH = 12

TTY = sys.stdout.isatty()
COLOR_ON = TTY and not os.environ.get('NO_COLOR')

LOCK = threading.RLock()
STEPS = []      # stack of live ksteps - innermost last
PLAN = None
LIVE = 0
THREAD = None

def paint(text: str, color: str, bold: bool = False) -> str:
    if not COLOR_ON:
        return text
    return (BOLD if bold else '') + COLOR[color] + text + RESET

# scope_action -> scope:action - split on the first _
def fmt_kname(kname: str) -> str:
    scope, _, action = kname.partition('_')
    return f'{scope}:{action}' if action else scope

def fmt_secs(secs: float) -> str:
    if secs < 60:
        return f'{secs:.1f}s'
    return f'{int(secs // 60)}m{int(secs % 60):02d}s'

def fmt_line(sev: str, kname: str, msg: str) -> str:
    glyph, color, bold = SEV[sev]
    name = fmt_kname(kname)
    pad = ' ' * max(1, KNAME_PAD - len(name) - 2)
    head = paint(glyph, color, bold) + ' ' + paint(name, color, bold) + pad
    lines = str(msg).split('\n')
    out = head + lines[0]
    for line in lines[1:]:
        out += '\n  ' + line
    return out

# a live line must never wrap or clear_live cannot count the rows to clear
def clip(line: str, width: int) -> str:
    out = ''
    vis = 0
    i = 0
    while i < len(line):
        if line[i] == '\x1b':
            end = line.find('m', i)
            out += line[i:end + 1]
            i = end + 1
            continue
        if vis == width:
            return out + (RESET if COLOR_ON else '')
        out += line[i]
        vis += 1
        i += 1
    return out

def clear_live() -> None:
    global LIVE
    if LIVE:
        sys.stdout.write('\r' + (f'\x1b[{LIVE - 1}A' if LIVE > 1 else '') + '\x1b[0J')
        LIVE = 0

def draw_live() -> None:
    global LIVE
    lines = []
    if PLAN:
        done = min(PLAN['done'], PLAN['total'])
        fill = round(BAR_WIDTH * done / PLAN['total']) if PLAN['total'] else BAR_WIDTH
        bar = paint('█' * fill, 'green') + paint('░' * (BAR_WIDTH - fill), 'cyan')
        lines.append(f"{paint('kopsrox', 'blue', True)} {PLAN['title']} {paint('──', 'cyan')} {bar} {done}/{PLAN['total']}")
    if STEPS:
        step = STEPS[-1]
        frame = SPINNER[int(time.monotonic() * 10) % len(SPINNER)]
        name = fmt_kname(step.kname)
        pad = ' ' * max(1, KNAME_PAD - len(name) - 2)
        elapsed = paint(f'({fmt_secs(time.monotonic() - step.t0)})', 'cyan')
        lines.append(f"{paint(frame, 'cyan', True)} {paint(name, 'cyan')}{pad}{step.msg} {elapsed}")
    if lines:
        width = shutil.get_terminal_size().columns
        # explicit \r\n - sudo use_pty flips the tty to raw during image builds,
        # where a bare \n no longer implies a carriage return
        sys.stdout.write('\r\n'.join(clip(line, width - 1) for line in lines))
        LIVE = len(lines)
    sys.stdout.flush()

def emit(text: str) -> None:
    with LOCK:
        if TTY:
            clear_live()
            sys.stdout.write(text.replace('\n', '\r\n') + '\r\n')
            draw_live()
        else:
            sys.stdout.write(text + '\n')
        sys.stdout.flush()

def render_loop():
    while True:
        time.sleep(0.1)
        with LOCK:
            if STEPS or PLAN:
                clear_live()
                draw_live()

def ensure_thread():
    global THREAD
    if TTY and THREAD is None:
        sys.stdout.write('\x1b[?25l')
        THREAD = threading.Thread(target = render_loop, daemon = True)
        THREAD.start()

@atexit.register
def cleanup():
    with LOCK:
        STEPS.clear()
        if TTY:
            clear_live()
            if THREAD:
                sys.stdout.write('\x1b[?25h')
            sys.stdout.flush()

def kmsg(kname: str = 'kopsrox', msg: str = 'no msg', sev: str = 'info') -> None:
    emit(fmt_line(sev, kname, msg))

def kabort(kname: str, msg: str) -> None:
    with LOCK:
        # live steps are dead - stop the render thread redrawing them
        STEPS.clear()
        kmsg(kname, msg, 'err')
    exit(1)

def kplan(add: int, title: str | None = None) -> None:
    global PLAN
    with LOCK:
        if PLAN is None:
            PLAN = {'title': title or '', 'total': 0, 'done': 0}
        if title:
            PLAN['title'] = title
        PLAN['total'] += add

def kplan_tick() -> None:
    with LOCK:
        if PLAN:
            PLAN['done'] += 1

# live spinner while a slow operation runs. quiet steps never print on success
class kstep:

    def __init__(self, kname: str, msg: str, quiet: bool = False) -> None:
        self.kname = kname
        self.msg = msg
        self.quiet = quiet
        self.t0 = time.monotonic()

    def __enter__(self):
        with LOCK:
            STEPS.append(self)
            if not TTY and not self.quiet:
                sys.stdout.write(fmt_line('info', self.kname, self.msg) + '\n')
                sys.stdout.flush()
            ensure_thread()
        return self

    def __exit__(self, exc_type, exc, tb):
        with LOCK:
            if self in STEPS:
                STEPS.remove(self)
            if exc_type is None and not self.quiet:
                emit(fmt_line('done', self.kname, f'{self.msg} ({fmt_secs(time.monotonic() - self.t0)})'))
            elif TTY:
                clear_live()
                draw_live()
        return False
