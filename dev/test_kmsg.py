#!/usr/bin/env python3

# checks for lib/kopsrox_kmsg.py
# scripted: runs itself as a child with piped ( non tty ) stdout and asserts on plain output
# visual: run in a terminal for a live spinner / bar / color demo after the asserts pass

import sys, os, time, subprocess

# wrap child mode - long live line in a narrow pty ( see scroll regression below )
if os.environ.get('KMSG_CHILD') == 'wrap':
  sys.path[0:0] = ['lib/']
  os.environ.pop('COLUMNS', None)
  os.environ.pop('LINES', None)
  from kopsrox_kmsg import kstep, kplan
  time.sleep(0.2)
  kplan(4, 'wrap test plan')
  with kstep('wrap_step', 'x' * 120):
    time.sleep(0.6)
  exit(0)

# child mode - emit through the real module with piped stdout
if os.environ.get('KMSG_CHILD'):
  sys.path[0:0] = ['lib/']
  from kopsrox_kmsg import kmsg, kabort, kstep, kplan, kplan_tick
  kmsg('test_info', 'plain info')
  kmsg('test_sys', 'warning line', 'sys')
  kmsg('test_err', 'error line', 'err')
  kmsg('test_multi', 'first\nsecond')
  kplan(2, 'test plan')
  with kstep('test_step', 'visible step'):
    time.sleep(0.2)
  kplan_tick()
  with kstep('test_quiet', 'invisible step', quiet = True) as step:
    step.msg = 'updated'
    time.sleep(0.1)
  kplan_tick()
  if sys.argv[1:] == ['abort']:
    kabort('test_abort', 'aborting')
  exit(0)

env = dict(os.environ, KMSG_CHILD = '1')

# clean run
run = subprocess.run([sys.executable, __file__], env = env, capture_output = True, text = True)
out = run.stdout
assert run.returncode == 0, f'expected exit 0 got {run.returncode}\n{out}{run.stderr}'
assert '\x1b[' not in out, 'ansi codes leaked into non-tty output'
assert '· test:info' in out, out
assert '! test:sys' in out, out
assert '✗ test:err' in out, out
assert '\n  second' in out, 'multiline continuation not indented: ' + out
assert '· test:step' in out, 'non-quiet step missing start line: ' + out
assert '✓ test:step' in out and 'visible step (' in out, 'non-quiet step missing done line: ' + out
assert 'test:quiet' not in out, 'quiet step printed in non-tty: ' + out

# abort run
run = subprocess.run([sys.executable, __file__, 'abort'], env = env, capture_output = True, text = True)
assert run.returncode == 1, f'kabort should exit 1 - got {run.returncode}'
assert '✗ test:abort' in run.stdout, run.stdout

# scroll regression - a live line wider than the terminal must not creep down
# the screen ( clear_live counts logical lines so live lines must never wrap )
import pty, fcntl, termios, struct
WIDTH = 60
pid, fd = pty.fork()
if pid == 0:
  os.environ['KMSG_CHILD'] = 'wrap'
  os.execvp(sys.executable, [sys.executable, __file__])
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, WIDTH, 0, 0))
data = b''
while True:
  try:
    chunk = os.read(fd, 65536)
  except OSError:
    break
  if not chunk:
    break
  data += chunk
os.waitpid(pid, 0)

# minimal terminal sim - track how far down the cursor travels
row = col = maxrow = i = 0
text = data.decode(errors = 'replace')
while i < len(text):
  ch = text[i]
  if ch == '\x1b' and text[i + 1:i + 2] == '[':
    j = i + 2
    while j < len(text) and not text[j].isalpha():
      j += 1
    if text[j] == 'A':
      row = max(0, row - int(text[i + 2:j] or 1))
    i = j + 1
    continue
  if ch == '\r':
    col = 0
  elif ch == '\n':
    row += 1
    col = 0
  elif ch != '\x1b':
    col += 1
    if col >= WIDTH:
      row += 1
      col = 0
  maxrow = max(maxrow, row)
  i += 1
assert maxrow <= 3, f'live region scrolled down {maxrow} rows in a {WIDTH} col pty'

print('kmsg tests OK')

# visual demo when run interactively
if sys.stdout.isatty():
  sys.path[0:0] = ['lib/']
  from kopsrox_kmsg import kmsg, kstep, kplan, kplan_tick
  kplan(3, 'demo plan')
  for n in range(3):
    with kstep('demo_step', f'step {n + 1} of 3') as step:
      time.sleep(1)
      step.msg = f'step {n + 1} of 3 - nearly there'
      time.sleep(1)
    kplan_tick()
  kmsg('demo_done', 'demo finished', 'sys')
