#!/usr/bin/env python3

# generate the default kopsrox.ini from the schema
def init_kopsrox_ini():

  from kopsrox_schema import render_ini

  # write config
  # file should not already exist...
  with open('kopsrox.ini', 'w') as cfile:
    render_ini().write(cfile)
  print('created kopsrox.ini please edit for your setup')
  return
