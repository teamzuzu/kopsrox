#!/usr/bin/env python3

# kopsrox output module - the only module that emits ansi
# kmsg() severity lines, kabort() error + exit 1, kstep() live spinner steps,
# kplan()/kplan_tick() overall progress bar for compound verbs
# degrades to plain sequential lines when stdout is not a tty ( NO_COLOR kills color )

import sys, os, time, threading, atexit, shutil

# ansi bits
RESET = '\x1b[0m'
BOLD = '\x1b[1m'
COLOR = {
  'green':  '\x1b[32m',
  'yellow': '\x1b[33m',
  'red':    '\x1b[31m',
  'cyan':   '\x1b[36m',
  'blue':   '\x1b[34m',
}

# severity -> glyph, color, bold
SEV = {
  'info': ('·', 'cyan',   False),
  'sys':  ('!', 'yellow', True),
  'err':  ('✗', 'red',    True),
  'done': ('✓', 'green',  False),
}

SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
KNAME_PAD = 22
BAR_WIDTH = 12

# animation needs a tty - color additionally honours NO_COLOR
TTY = sys.stdout.isatty()
COLOR_ON = TTY and not os.environ.get('NO_COLOR')

# shared state - guarded by LOCK
LOCK = threading.RLock()
STEPS = []      # stack of live ksteps - innermost last
PLAN = None     # {'title': str, 'total': int, 'done': int}
LIVE = 0        # lines the live region currently occupies
THREAD = None   # render thread

def paint(text, color, bold = False):
  if not COLOR_ON:
    return text
  return (BOLD if bold else '') + COLOR[color] + text + RESET

# scope_action -> scope:action - split on the first _
def fmt_kname(kname):
  scope, _, action = kname.partition('_')
  return f'{scope}:{action}' if action else scope

def fmt_secs(secs):
  if secs < 60:
    return f'{secs:.1f}s'
  return f'{int(secs // 60)}m{int(secs % 60):02d}s'

# render one message - pad computed on plain text before color is added
def fmt_line(sev, kname, msg):
  glyph, color, bold = SEV[sev]
  name = fmt_kname(kname)
  pad = ' ' * max(1, KNAME_PAD - len(name) - 2)
  head = paint(glyph, color, bold) + ' ' + paint(name, color, bold) + pad
  lines = str(msg).split('\n')
  out = head + lines[0]
  for line in lines[1:]:
    out += '\n  ' + line
  return out

# clip a painted line to width visible chars - ansi sequences pass through
# live lines must never wrap or clear_live cannot count the rows to clear
def clip(line, width):
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

# wipe the live region - cursor ends at column 0 of its first line
def clear_live():
  global LIVE
  if LIVE:
    sys.stdout.write('\r' + (f'\x1b[{LIVE - 1}A' if LIVE > 1 else '') + '\x1b[0J')
    LIVE = 0

# draw the live region - plan bar line then innermost step spinner line
def draw_live():
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
    # explicit \r\n - sudo use_pty flips the shared tty to raw mode while image
    # builds run and a bare \n stops implying carriage return ( no ONLCR )
    sys.stdout.write('\r\n'.join(clip(line, width - 1) for line in lines))
    LIVE = len(lines)
  sys.stdout.flush()

# print a permanent line without tearing the live region
def emit(text):
  with LOCK:
    if TTY:
      clear_live()
      sys.stdout.write(text.replace('\n', '\r\n') + '\r\n')
      draw_live()
    else:
      sys.stdout.write(text + '\n')
    sys.stdout.flush()

# render thread - animates spinner + elapsed while anything is live
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

# wipe live output and restore the cursor on exit
@atexit.register
def cleanup():
  with LOCK:
    STEPS.clear()
    if TTY:
      clear_live()
      if THREAD:
        sys.stdout.write('\x1b[?25h')
      sys.stdout.flush()

# kmsg - severity message line - same signature as always
def kmsg(kname = 'kopsrox', msg = 'no msg', sev = 'info'):
  emit(fmt_line(sev, kname, msg))

# kabort - error message then exit non zero
def kabort(kname, msg):
  with LOCK:
    # live steps are dead - stop the render thread redrawing them
    STEPS.clear()
    kmsg(kname, msg, 'err')
  exit(1)

# kplan - start or extend the overall step plan
def kplan(add, title = None):
  global PLAN
  with LOCK:
    if PLAN is None:
      PLAN = {'title': title or '', 'total': 0, 'done': 0}
    if title:
      PLAN['title'] = title
    PLAN['total'] += add

# kplan_tick - one plan unit done
def kplan_tick():
  with LOCK:
    if PLAN:
      PLAN['done'] += 1

# kstep - live spinner line while a slow operation runs
# quiet steps never print on success - for polling internals like qa_exec
class kstep:

  def __init__(self, kname, msg, quiet = False):
    self.kname = kname
    self.msg = msg
    self.quiet = quiet
    self.t0 = time.monotonic()

  def __enter__(self):
    with LOCK:
      STEPS.append(self)
      # non tty gets a plain start line instead of the spinner
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
