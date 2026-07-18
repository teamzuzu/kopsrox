#!/usr/bin/env python3

# checks for lib/kopsrox_kmsg.py
# scripted: runs itself as a child with piped ( non tty ) stdout and asserts on plain output
# visual: run in a terminal for a live spinner / bar / color demo after the asserts pass

import sys, os, time, subprocess

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
