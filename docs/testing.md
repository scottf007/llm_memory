# Testing

The default suite is hermetic and does not read local conversation stores:

```bash
flock -w 1800 ~/.am-host/test.lock bash -c 'export TMUX_TMPDIR=$(mktemp -d); .venv/bin/python3 -m pytest tests/'
```

To re-verify the 35 mutable local-corpus rows on the owner machine, opt in:

```bash
flock -w 1800 ~/.am-host/test.lock bash -c 'export TMUX_TMPDIR=$(mktemp -d); .venv/bin/python3 -m pytest -m live_corpus tests/'
```

The 17 ledger-pinned rows intentionally fail loudly when their source has
drifted, until independently re-verified. To run one live row explicitly,
clear the default marker filter:

```bash
.venv/bin/python3 -m pytest -m "" tests/test_archive_class.py::test_unclassified_trio_exact
```

## Self-running extraction

The automatic worker is tested only with the frozen fake Claude backend and
fake systemctl command. It never calls a real model or user service manager:

```bash
flock -w 1800 ~/.am-host/test-llm.lock bash -c 'export TMUX_TMPDIR=$(mktemp -d); HOME=$(mktemp -d) LLM_MEMORY_HOME=$(mktemp -d) .venv/bin/python3 -m pytest tests/test_selfrun_*.py -p no:cacheprovider --basetemp=$(mktemp -d)'
```
