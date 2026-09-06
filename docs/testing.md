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

### Extraction cost accounting

The worker requests Claude JSON output and records any reported USD cost and
input/output token counts in both session provenance and
`runtime/extraction-spend.json`. If the backend supplies token counts but no
cost, it estimates from a pinned USD-per-million-token table: sonnet
input/output `$3.00/$15.00`, opus `$15.00/$75.00`, and haiku `$0.80/$4.00`.
Installations may replace that table without a network lookup using
`LLM_MEMORY_EXTRACT_COST_TABLE`, for example
`{"sonnet":{"input_per_million_usd":3,"output_per_million_usd":15}}`.

If neither a cost nor usable token counts exist, the call is marked
`cost_source: unknown` and reserves the full session cap in the daily ledger;
it is never treated as a zero-cost call. This fail-closed behaviour means the
daily cap still binds even if a backend's accounting fields disappear.
