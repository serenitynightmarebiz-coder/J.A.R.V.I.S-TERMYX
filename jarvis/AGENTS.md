# Project notes — J.A.R.V.I.S. (Termux edition)

Pure-stdlib Python, no dependencies. The whole app lives in `jarvis/` (package)
with the entry point `jarvis.py` at the repo root.

## Run & verify (no phone needed)

```bash
python3 -m unittest discover -s tests      # full suite, offline
JARVIS_MOCK=1 python3 jarvis.py --selftest # tools against the fake device
JARVIS_MOCK=1 python3 jarvis.py --diag     # health report (JSON)
python3 -m compileall -q jarvis jarvis.py tests
```

`JARVIS_MOCK=1` is required for `--selftest`/`--diag` off-device, otherwise the
Termux:API probes fail.

## Pitfalls

- `config.json` is gitignored and not committed; the code falls back to built-in
  defaults, and `install.sh` copies `config.example.json`. Never commit it — it
  can hold an API key.
- `data/` and `logs/` are gitignored runtime state, auto-created via `mkdir(parents=True)`
  at runtime. `--selftest` recreates `data/backups` and `data/selftest`; delete
  them before committing if they show up.
- `.revyl/` is PolyDev session data — gitignored, never commit it.
- The sandbox shell is busybox `ash`: no heredocs, no `grep --include`/`--exclude-dir`.
  Use `search_grep` for searching and `write_file` for multi-line files.
- README numbers are asserted by hand: 83 tools, 102 tests, 13 providers. Update
  them together if the registry or suite grows.

## Layout

`jarvis/tools/` holds the 83 registered capabilities (registry decorator →
JSON schema). `agent.py` is the model→tool→model loop; `llm.py` the provider
abstraction; `gate.py` the wake-word rules; `service.py` the background daemon.
