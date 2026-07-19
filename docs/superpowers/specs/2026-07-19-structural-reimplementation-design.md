# Structural Re-implementation Design

Date: 2026-07-19
Status: approved (design discussion in session; this document is the written spec)

## Problem

kopsrox works, but its execution model fights standard Python practice:

- Verbs execute top-to-bottom on import (`__import__('verb_' + verb)`); nothing in
  `lib/` is a callable unit.
- Six `from x import *` chains make every module's real dependencies invisible and
  leak ~80 names (validators, stdlib modules, helper internals) into every namespace.
- `lib/kopsrox_config.py` reads `sys.argv[1]` at import time, so no config-dependent
  module can be imported outside the CLI (dev tests resort to faking argv).
- Style predates the rest: 2-space indent, `return(x)`, `if not x in y`, broad
  `try/except` around simple lookups.

## Decisions (made with user)

1. **Structural depth** — verbs become functions, config init becomes explicit,
   star imports become explicit imports. Not a light style pass, not a full
   package/pytest/argparse rewrite.
2. **Explicit names for config access** — config values remain plain module
   attributes; each module imports exactly the names it uses. Call sites keep bare
   names (`cluster_name`, not `cfg.cluster_name`).
3. **PEP 8 4-space indent** — whole-file reformat accepted alongside the structural
   changes.

## Non-goals

- No new dependencies, no pyproject.toml, no pytest, no argparse/click, no logging
  module, no src/ layout, no classes for their own sake.
- No behavior changes: CLI surface, kmsg output, exit codes, ini format, generated
  artifacts, and file/module names are all frozen.
- `kopsrox_schema.py` internals unchanged (already pure); reformat only.

## Execution model

`kopsrox.py` (entrypoint, only file that touches `sys.argv`):

1. Keep the `cmds` dict and the existing argv validation verbatim: same help
   output, help-and-exit-0 for no args / verb only, help-and-exit-1 for unknown
   verb/command/missing arg.
2. `kopsrox_config.init(verb)` — explicit, replaces import-time side effects.
3. `import verb_<verb>` then `verb_<verb>.run(cmd, arg)` where `arg` is
   `sys.argv[3]` or `None`.

`lib/kopsrox_config.py`:

- `init(verb: str, cmd: str) -> None` performs today's staged sequence with identical
  messages and ordering: read+validate `kopsrox.ini` (inject option values as
  module attributes), compute derived values (`masterid`, `vmnames`, `network_*`,
  `region_string`), connect (`ProxmoxAPI` + `cluster.status.get()`), one
  `cluster.resources.get()` discovery (node/storage/vms/image checks), then
  verb-scoped stages (guest-verb power-on; image/cluster agent ping into
  `conf_check_master_up`; image-only dpkg/release/template-desc/bridge checks).
- `passed_cmd` is replaced by the `verb` parameter; `cmd` covers the one place the
  command matters inside init (the image-existence check is skipped for
  `image create`).
- Helper functions (`kopsrox_img`, `list_kopsrox_vm`, `get_k3s_token`, `vmip`,
  `local_exec`, `image_info`) stay in this module, typed.
- Importable without argv: `import kopsrox_config` is side-effect free until
  `init()` is called.

`lib/verb_*.py`:

- Each exposes `run(cmd: str, arg: str | None) -> None`; body decomposed into
  named functions where a command is more than a few lines (e.g.
  `image_create()`, `node_terminal(vmid)`). No `sys.argv`, no import-time work.

Import-order guarantee: `init()` runs before the verb module is imported, so
`from kopsrox_config import cluster_name, ...` in any `lib/` module binds real
values. Attributes set only for the image verb (`cloud_image_desc`,
`microvm_kernel` fallbacks) are only imported by `verb_image`, which is only
imported for image commands.

## Imports

- Zero `import *`. Explicit name lists, one imported module per line for stdlib.
- Import graph unchanged: config ← proxmox ← k3s ← verbs; artifacts ← config;
  kmsg and schema leaf-level. `kopsrox_schema` still must not import
  `kopsrox_config`.

## Style

- PEP 8: 4-space indent, `return x`, `x not in y`, no parenthesized returns,
  comparison idioms fixed. Single quotes kept. Existing comments kept verbatim —
  they document microvm traps and are part of the spec.
- Type hints on all function signatures in `lib/` (parameters and returns);
  no variable annotations, no typing gymnastics.

## Error handling

- `kabort(kname, msg)` = err + exit 1 stays the error convention; success exits 0.
- Broad `try/except` around simple attribute/index access becomes explicit checks
  (`if masterid not in vms:`). No bare `except:` that can catch SystemExit.
  Precise exceptions (`except Exception`) where a real guard is needed
  (API calls, network).

## Acceptance criteria

All verified against pre-change baselines captured before work starts:

1. `./kopsrox.py`, `./kopsrox.py <verb>`, unknown verb/cmd, missing arg: identical
   output and exit codes.
2. Generated default ini: byte-identical (`dev/gen_config.sh` diff).
3. Rendered artifacts for the live config (`kopsrox.sh`, kubevip manifest,
   `config.yaml`): byte-identical.
4. `./kopsrox.py image info` and `./kopsrox.py cluster info`: identical output.
5. `dev/test_config.py` and `dev/test_kmsg.py` pass (updated only where they faked
   argv to import config).
6. Final gate: full `dev/rls_test.sh` live run — destructive, requires explicit
   user go-ahead at that point.

## Docs

CLAUDE.md architecture section rewritten to describe the call-based flow
(`init()` + `run()`), removing the import-side-effect description and the
"cannot be imported outside the CLI entrypoint" caveat.
