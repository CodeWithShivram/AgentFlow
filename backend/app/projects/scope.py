"""File ownership scopes (ADR-0048). Pure; importable by the API and workers.

A scope entry is a safe relative path (`app.agents.outputs.safe_relative_path`): an exact file
(`pyproject.toml`) or a folder ending in `/` (`src/app/`), which covers everything below it.
Comparisons ignore case, because Windows and macOS filesystems do, so two tasks can never own
`src/App.py` and `src/app.py` at once.
"""

from collections.abc import Iterable

from app.agents.outputs import safe_relative_path


def normalize_entry(entry: str) -> str:
    """Validates one scope entry and returns it unchanged."""
    path = entry[:-1] if entry.endswith("/") else entry
    safe_relative_path(path)
    return entry


def _key(entry: str) -> str:
    return entry.casefold()


def covers(entry: str, path: str) -> bool:
    """Whether a scope entry covers a file path."""
    entry, path = _key(entry), _key(path)
    return path == entry or (entry.endswith("/") and path.startswith(entry))


def overlaps(a: str, b: str) -> bool:
    """Whether two scope entries can cover the same file."""
    a, b = _key(a), _key(b)
    if a == b:
        return True
    return (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b))


def in_scope(scope: Iterable[str], path: str) -> bool:
    return any(covers(entry, path) for entry in scope)
