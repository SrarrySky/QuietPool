# Contributing to Indicators as Constraints

This repository is a reproducible experiment harness for *Indicators
as Constraints: Red-Teaming EU Market-Manipulation Indicators on AMM-Based
Crypto Markets*. Contributions are welcome, subject to the reproducibility and
honesty constraints below.

## Scope

- Bug fixes and clarifications to the harness, documentation, or configuration.
- New documentation, figures, or worked examples.
- Test coverage that strengthens the reproducibility guarantees.

The historical cohort, preregistered configuration, and frozen protocol are not
open to ad-hoc edits. Any change to `experiments/config/`,
`experiments/vendor/`, or a frozen history invalidates the protocol lock and
must go through a new preregistration. See
`experiments/docs/REPLACEMENT_PROTOCOL.md`.

## Prerequisites

Python 3.11 or newer (3.12 is the validated environment). The harness requires no database, node, wallet, or API key.

## Development workflow

```bash
python -m venv .venv
source .venv/bin/activate
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
python -m pip install -e '.[test]'
```

Run the checks before submitting:

```bash
python -m pytest -q
python -m ruff check experiments
python -m ruff format --check experiments
```

Tests forbid socket connections and HTTP requests. The downloader is the only
network-using component; it allows only read methods.

## Submission checklist

- [ ] `python -m pytest -q` passes.
- [ ] `python -m ruff check experiments` and `ruff format --check` pass.
- [ ] No changes under `experiments/vendor/` or `experiments/config/` unless
      explicitly justified and re-frozen.
- [ ] New code has concise NumPy/Google-style docstrings and type annotations.
- [ ] Commit messages are imperative and describe intent.

## Reproducibility and integrity

- All randomness is fixed by seed 42; do not introduce non-determinism.
- Do not prune settings or pools based on test results.
- Do not alter outcomes, thresholds, or the eligibility rule to reach a result.
- Report limitations honestly; software validation does not establish the
  paper's hypotheses or regulatory conclusions.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
