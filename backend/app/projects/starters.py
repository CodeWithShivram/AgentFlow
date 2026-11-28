"""Starter files (Step 9.15, ADR-0061): a stack profile's minimal working versions of key files,
given to the tasks whose file scope covers them so new code starts from a structure the
profile's checks accept (for the web profile: a doctype, lang, title and viewport, and every API
route defined before the static mount).

Pure and deterministic: platform templates only, never agent or model text.
"""

from typing import Any

from app.projects.profiles import PROFILES
from app.projects.scope import in_scope

PACKAGE = "<package>"


def _package_path(path: str, scope: list[str]) -> str | None:
    """The concrete path of a `src/<package>/...` starter for this scope, if the scope covers
    one: the package comes from a scope entry under src/ (`src/shop/` or `src/shop/main.py`);
    a scope owning all of src/ keeps the placeholder."""
    prefix, rest = path.split(PACKAGE, 1)
    for entry in scope:
        if entry.lower() == prefix.lower():  # owns the whole parent folder (src/)
            return path
        if entry.lower().startswith(prefix.lower()):
            package = entry[len(prefix) :].split("/", 1)[0]
            if package and package != rest.lstrip("/"):
                candidate = f"{prefix}{package}{rest}"
                if in_scope(scope, candidate):
                    return candidate
    return None


def starters_for(stack_profile: str | None, scope: list[str]) -> list[dict[str, Any]]:
    """The starter files of the profile that fall in `scope`, as contract file entries."""
    profile = PROFILES.get(stack_profile or "")
    if profile is None or not scope:
        return []
    selected = []
    for path, content in profile.starter_files:
        target = _package_path(path, scope) if PACKAGE in path else path
        if target is not None and (PACKAGE in target or in_scope(scope, target)):
            selected.append({"path": target, "content": content})
    return selected
