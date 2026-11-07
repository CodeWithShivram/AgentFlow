"""Detection of credential-like fields in structured data that must never be persisted."""

import re
from typing import Any

_SENSITIVE_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "privatekey",
)
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]")


def is_sensitive_key(key: str) -> bool:
    """True for keys such as `password`, `apiKey`, `refresh_token` or `Authorization`.

    Keys ending in "token" are sensitive; plural counters such as `input_tokens` are not.
    """
    compact = _NON_ALPHANUMERIC.sub("", key.lower())
    return compact.endswith("token") or any(f in compact for f in _SENSITIVE_FRAGMENTS)


def find_sensitive_keys(value: Any, path: str = "$") -> list[str]:
    """JSON paths of every sensitive key found anywhere in `value`."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if is_sensitive_key(str(key)):
                found.append(child_path)
            found.extend(find_sensitive_keys(child, child_path))
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            found.extend(find_sensitive_keys(child, f"{path}[{index}]"))
    return found
