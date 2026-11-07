# ADR-0003: Python 3.12 baseline managed via uv

- Status: Accepted
- Date: 2026-11-07

## Context
The handoff sets Python 3.12 as the compatibility baseline. The developer machine only had Python 3.14.7 via the `py` launcher; `python` is not on PATH. uv 0.12.9 is installed.

## Decision
Keep Python 3.12. Python 3.12.14 was installed with `uv python install 3.12` (user-scoped, system Python untouched). The backend virtual environment is created with `uv venv --python 3.12`. `requires-python` will be pinned to `>=3.12,<3.13` in Step 1.1. The dependency locking tool is decided in Step 1.1 (OD-7).

## Alternatives considered
- Python 3.14: no install needed, but deviates from the documented baseline and some dependencies may lag.

## Consequences
- Contributors need uv (or another Python 3.12 install). README must document this.
- CI uses Python 3.12.
