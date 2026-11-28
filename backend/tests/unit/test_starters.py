"""Starter files (Step 9.15, ADR-0061): which tasks get the web profile's working templates."""

import pytest

from app.projects.profiles import PROFILES
from app.projects.starters import starters_for


@pytest.mark.parametrize(
    ("scope", "paths"),
    [
        (["static/"], ["static/index.html"]),
        (["static/index.html", "static/app.js"], ["static/index.html"]),
        (["src/shop/main.py", "src/shop/db.py"], ["src/shop/main.py"]),
        (["src/shop/"], ["src/shop/main.py"]),
        (["src/"], ["src/<package>/main.py"]),
        (["src/shop/db.py"], []),
        (["static/app.js"], []),
        (["tests/"], []),
    ],
)
def test_web_tasks_get_the_starters_their_scope_covers(scope: list[str], paths: list[str]) -> None:
    assert [f["path"] for f in starters_for("python-3.12-web", scope)] == paths


def test_other_profiles_and_scopeless_tasks_get_none() -> None:
    assert starters_for("python-3.12", ["src/"]) == []
    assert starters_for("python-3.12-web", []) == []
    assert starters_for(None, ["static/"]) == []


def test_the_web_starters_pass_the_structure_the_checks_need() -> None:
    files = dict(PROFILES["python-3.12-web"].starter_files)
    html = files["static/index.html"]
    assert html.startswith("<!DOCTYPE html>") and '<html lang="en">' in html
    assert "<title>" in html and 'name="viewport"' in html and "http" not in html
    main = files["src/<package>/main.py"]
    route, mount = main.index('@app.get("/api/health")'), main.index('app.mount("/"')
    assert route < mount and "html=True" in main and "parents[2]" in main
    compile(main, "main.py", "exec")
