"""Deliverable packaging (ADR-0052): denylist, secret scan, limits, links, reproducible and
verified archives."""

import hashlib
import json
import os
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from app.projects import packaging
from app.projects.packaging import PackagingError, denied, find_secrets, select_files

FILES = {
    "pyproject.toml": "[project]\nname = 'x'\n",
    "README.md": "# X\n",
    ".env.example": "API_KEY=change-me\nDATABASE_URL=postgresql://user:password@localhost/db\n",
    "src/app.py": "print('hi')\n",
    "tests/test_app.py": "def test(): pass\n",
}


def _project(tmp_path: Path, files: Mapping[str, str | bytes]) -> Path:
    root = tmp_path / "project"
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
    return root


def _report(entries: list[packaging.ManifestEntry], excluded: int) -> str:
    return f"report {len(entries)} {excluded}\n"


def test_a_clean_project_is_packaged_reproducibly_and_verified(tmp_path: Path) -> None:
    project = _project(tmp_path, FILES)
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"
    entries, excluded = packaging.package(project, FILES, first, folder="tool", report=_report)
    packaging.package(project, FILES, second, folder="tool", report=_report)

    assert excluded == 0 and sorted(e.path for e in entries) == sorted(FILES)
    assert first.read_bytes() == second.read_bytes()  # fixed timestamps and order
    with zipfile.ZipFile(first) as archive:
        names = set(archive.namelist())
        assert names == {f"tool/{p}" for p in FILES} | {
            "tool/AGENTFLOW_BUILD_REPORT.md",
            "tool/AGENTFLOW_MANIFEST.json",
        }
        manifest = json.loads(archive.read("tool/AGENTFLOW_MANIFEST.json"))
    by_path = {f["path"]: f for f in manifest["files"]}
    assert by_path["src/app.py"]["sha256"] == hashlib.sha256(b"print('hi')\n").hexdigest()


@pytest.mark.parametrize(
    "path",
    [".env", ".env.local", "config/.env.production", "key.pem", "certs/server.key",
     "data.sqlite3", "node_modules/x/index.js", ".venv/lib/x.py", "src/__pycache__/a.pyc",
     ".git/config", "home/.ssh/id_rsa", "id_ed25519", ".npmrc", "app.log", "dist/out.js"],
)  # fmt: skip
def test_denied_files_are_excluded(path: str) -> None:
    assert denied(path)


@pytest.mark.parametrize("path", [".env.example", "src/env.py", "README.md", "src/keys.py"])
def test_ordinary_files_are_kept(path: str) -> None:
    assert not denied(path)


def test_excluded_files_are_counted_not_packaged(tmp_path: Path) -> None:
    files = {**FILES, ".env": "SECRET=x\n", "AGENTFLOW_MANIFEST.json": "{}"}
    project = _project(tmp_path, files)
    selection = select_files(project, files)
    assert selection.excluded == 2
    assert ".env" not in [p for p, _ in selection.files]


@pytest.mark.parametrize(
    "secret",
    [
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n",
        "aws = 'AKIAABCDEFGHIJKLMNOP'",
        "OPENAI = 'sk-proj-abcdefghijklmnopqrstuvwxyz012345'",
        "token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'",
        "url = 'postgresql://admin:s3cr3tvalue@db.internal/prod'",
    ],
)
def test_secrets_refuse_the_whole_package(tmp_path: Path, secret: str) -> None:
    files = {**FILES, "src/config.py": secret}
    with pytest.raises(PackagingError) as refused:
        select_files(_project(tmp_path, files), files)
    assert refused.value.code == "SECRET_DETECTED" and refused.value.count == 1
    assert secret not in str(refused.value)


def test_placeholders_are_not_secrets() -> None:
    assert find_secrets(FILES[".env.example"].encode()) == 0
    assert find_secrets(b"postgresql://<user>:<password>@localhost/db") == 0


@pytest.mark.parametrize("path", ["../escape.py", "/abs.py", "a\\b.py", ""])
def test_unsafe_tracked_paths_are_refused(tmp_path: Path, path: str) -> None:
    with pytest.raises(PackagingError, match="PATH_REFUSED"):
        select_files(_project(tmp_path, FILES), [*FILES, path])


def test_links_are_refused(tmp_path: Path) -> None:
    project = _project(tmp_path, FILES)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    try:
        os.symlink(outside, project / "linked", target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            pytest.skip("cannot create links here")
        import _winapi

        _winapi.CreateJunction(str(outside), str(project / "linked"))
    with pytest.raises(PackagingError, match="LINK_REFUSED"):
        select_files(project, [*FILES, "linked/secret.txt"])


def test_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path, FILES)
    monkeypatch.setattr(packaging, "MAX_FILE_BYTES", 10)
    with pytest.raises(PackagingError, match="FILE_TOO_LARGE"):
        select_files(project, FILES)
    monkeypatch.setattr(packaging, "MAX_FILE_BYTES", 10_000)
    monkeypatch.setattr(packaging, "MAX_TOTAL_BYTES", 50)
    with pytest.raises(PackagingError, match="PACKAGE_TOO_LARGE"):
        select_files(project, FILES)
    monkeypatch.setattr(packaging, "MAX_TOTAL_BYTES", 10_000)
    monkeypatch.setattr(packaging, "MAX_FILES", 2)
    with pytest.raises(PackagingError, match="TOO_MANY_FILES"):
        select_files(project, FILES)


def test_nothing_to_package(tmp_path: Path) -> None:
    files = {".env": "X=1\n"}
    with pytest.raises(PackagingError, match="NOTHING_TO_PACKAGE"):
        select_files(_project(tmp_path, files), files)


def test_tampered_or_corrupt_archives_fail_verification(tmp_path: Path) -> None:
    project = _project(tmp_path, FILES)
    target = tmp_path / "p.zip"
    entries, _ = packaging.package(project, FILES, target, folder="tool", report=_report)
    expected = {e.path: e.sha256 for e in entries}
    with pytest.raises(PackagingError, match="ARCHIVE_INVALID"):
        packaging.verify_archive(target, "tool", {**expected, "src/app.py": "0" * 64})
    target.write_bytes(b"not a zip")
    with pytest.raises(PackagingError, match="ARCHIVE_CORRUPT"):
        packaging.verify_archive(target, "tool", expected)


def test_report_marks_partial_packages_and_keeps_text_on_one_line() -> None:
    report = packaging.build_report(
        project={"name": "tool", "stack_profile": "python-3.12", "architecture": "a\nb"},
        label="PARTIAL",
        head_sha="a" * 40,
        tasks=[{"title": "x|y\nz", "status": "COMPLETED", "attempts": 1, "effort_points": 3}],
        checks=[{"name": "tests", "outcome": "failed"}],
        setup=["pip install -e ."],
        entries=[],
        excluded=0,
    )
    assert "**PARTIAL**" in report and "This package is PARTIAL" in report
    assert "| x/y z | COMPLETED | 1 | 3 |" in report
    assert "- `tests`: failed" in report
