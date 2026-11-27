"""Building a project's deliverable ZIP (ADR-0052). Pure filesystem code: no database.

Only files the platform committed to the workspace (Git-tracked at the packaged commit) are
candidates. Each is checked again before it is packaged:
- path rules (relative, no traversal, no links, inside the project);
- a denylist: real environment files, keys and certificates, local databases, dependency and
  cache folders, VCS metadata;
- a secret scan of the content (private keys, cloud and provider tokens, credentials in URLs);
  any hit refuses the whole package (`SECRET_DETECTED`) and names only the count;
- limits on file count, file size and total size.

The archive is written with fixed timestamps and permissions under one top-level folder, plus a
generated `AGENTFLOW_BUILD_REPORT.md` and `AGENTFLOW_MANIFEST.json` (path, size, SHA-256 of every
file), then re-opened and validated before it is handed to artifact storage.
"""

import hashlib
import json
import re
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

MAX_FILES = 2000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
REPORT_NAME = "AGENTFLOW_BUILD_REPORT.md"
MANIFEST_NAME = "AGENTFLOW_MANIFEST.json"
# A web project's page as the sandbox browser rendered it (ADR-0057); platform bytes, not a
# project file, so it is outside the manifest like the report.
PREVIEW_NAME = "AGENTFLOW_UI_PREVIEW.png"

_DENIED_SEGMENTS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
     ".ruff_cache", "dist", "build", ".next", ".ssh", ".aws", ".azure", ".kube", ".docker",
     ".gnupg", ".agentflow"}
)  # fmt: skip
_DENIED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".der", ".jks",
                    ".sqlite", ".sqlite3", ".db", ".pyc", ".pyo", ".log")  # fmt: skip
_DENIED_NAMES = frozenset({"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc",
                           ".netrc", "credentials", "credentials.json"})  # fmt: skip
_SECRETS = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|AKIA[0-9A-Z]{16}"
    r"|ASIA[0-9A-Z]{16}"
    r"|sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|(?i:[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{3,}@)"
)
# Placeholder credentials in examples (user:password@, change-me) are not secrets.
_PLACEHOLDER = re.compile(r"(?i)(change-?me|example|placeholder|password@|user:pass|<[^>]+>)")


class PackagingError(Exception):
    """A refusal with a fixed code (and counts only; never paths or content)."""

    def __init__(self, code: str, count: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.count = count


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size_bytes: int
    sha256: str


@dataclass
class Selection:
    files: list[tuple[str, bytes]] = field(default_factory=list)
    excluded: int = 0


def denied(path: str) -> bool:
    parts = PurePosixPath(path).parts
    lowered = [part.lower() for part in parts]
    name = lowered[-1]
    if any(part in _DENIED_SEGMENTS for part in lowered):
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    return name in _DENIED_NAMES or name.endswith(_DENIED_SUFFIXES)


def find_secrets(content: bytes) -> int:
    """How many likely secrets the content holds (placeholders excepted)."""
    text = content.decode("utf-8", "replace")
    return sum(1 for match in _SECRETS.finditer(text) if not _PLACEHOLDER.search(match.group(0)))


def select_files(project: Path, tracked: Iterable[str]) -> Selection:
    """Reads the tracked files that may be packaged. Raises PackagingError on unsafe paths,
    links, secrets or limits."""
    root = project.resolve(strict=True)
    selection = Selection()
    total = 0
    secrets = 0
    for relative in sorted(set(tracked)):
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or "\\" in relative or not relative:
            raise PackagingError("PATH_REFUSED")
        if denied(relative) or relative in (REPORT_NAME, MANIFEST_NAME, PREVIEW_NAME):
            selection.excluded += 1
            continue
        path = project / relative
        current = project
        for part in pure.parts:
            current = current / part
            if current.is_symlink() or current.is_junction():
                raise PackagingError("LINK_REFUSED")
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise PackagingError("PATH_REFUSED")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise PackagingError("FILE_TOO_LARGE")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise PackagingError("PACKAGE_TOO_LARGE")
        content = path.read_bytes()
        secrets += find_secrets(content)
        selection.files.append((relative, content))
        if len(selection.files) > MAX_FILES:
            raise PackagingError("TOO_MANY_FILES")
    if secrets:
        raise PackagingError("SECRET_DETECTED", secrets)
    if not selection.files:
        raise PackagingError("NOTHING_TO_PACKAGE")
    return selection


def manifest(files: Iterable[tuple[str, bytes]]) -> list[ManifestEntry]:
    return [
        ManifestEntry(path, len(content), hashlib.sha256(content).hexdigest())
        for path, content in files
    ]


def build_report(
    *,
    project: Mapping[str, Any],
    label: str,
    head_sha: str,
    tasks: Sequence[Mapping[str, Any]],
    checks: Sequence[Mapping[str, Any]],
    setup: list[str],
    entries: list[ManifestEntry],
    excluded: int,
    preview: bool = False,
) -> str:
    """The human-readable build report. Task titles and project text come from the plan (model
    output) and are written as plain Markdown text; check output is not included (it can be
    long and is kept in the platform)."""

    def line(text: object) -> str:
        return str(text).replace("\r", " ").replace("\n", " ").strip()

    out = [
        f"# AgentFlow build report: {line(project.get('name', 'project'))}",
        "",
        f"- Status: **{label}**",
        f"- Stack profile: `{line(project.get('stack_profile', ''))}`",
        f"- Workspace commit: `{head_sha}`",
        f"- Files: {len(entries)} packaged, {excluded} excluded by the packaging rules",
        "",
    ]
    if label != "VERIFIED":
        out += [
            "> This package is PARTIAL: the integration checks did not pass, or did not run.",
            "> It was packaged on explicit request. Review the failed checks below before use.",
            "",
        ]
    out += ["## Architecture", "", line(project.get("architecture", "")), ""]
    assumptions = project.get("assumptions") or []
    if assumptions:
        out += ["## Assumptions", "", *[f"- {line(a)}" for a in assumptions], ""]
    out += ["## Setup and checks", "", "```", *setup, "```", ""]
    out += ["## Tasks", "", "| Task | Status | Attempts | Effort |", "|---|---|---|---|"]
    out += [
        f"| {line(t['title']).replace('|', '/')} | {t['status']} | {t['attempts']} | "
        f"{t.get('effort_points') or '-'} |"
        for t in tasks
    ]
    out += ["", "## Verification", ""]
    if checks:
        out += [f"- `{line(c['name'])}`: {line(c['outcome'])}" for c in checks]
    else:
        out += ["- No integration checks ran."]
    if preview:
        out += [f"- Page screenshot from the headless-browser check: `{PREVIEW_NAME}`"]
    out += [
        "",
        "## Known limitations",
        "",
        "- Generated by AI agents and checked only by the checks listed above.",
        "- Review the code, dependencies and security before using it in production.",
        "",
        f"The full file list with SHA-256 checksums is in `{MANIFEST_NAME}`.",
        "",
    ]
    return "\n".join(out)


def write_archive(
    target: Path,
    folder: str,
    files: list[tuple[str, bytes]],
    extras: list[tuple[str, bytes]],
) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in [*files, *extras]:
            info = zipfile.ZipInfo(f"{folder}/{path}", date_time=FIXED_TIMESTAMP)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)


def verify_archive(target: Path, folder: str, expected: Mapping[str, str]) -> None:
    """Re-opens the archive: CRCs, exactly the expected names under the folder, no unsafe
    names, and every packaged file's SHA-256."""
    try:
        with zipfile.ZipFile(target) as archive:
            if archive.testzip() is not None:
                raise PackagingError("ARCHIVE_CORRUPT")
            names = archive.namelist()
            prefix = f"{folder}/"
            for name in names:
                pure = PurePosixPath(name)
                if not name.startswith(prefix) or pure.is_absolute() or ".." in pure.parts:
                    raise PackagingError("ARCHIVE_INVALID")
            packaged = {name[len(prefix) :] for name in names}
            if not set(expected) <= packaged or len(names) != len(set(names)):
                raise PackagingError("ARCHIVE_INVALID")
            for path, sha in expected.items():
                if hashlib.sha256(archive.read(prefix + path)).hexdigest() != sha:
                    raise PackagingError("ARCHIVE_INVALID")
    except zipfile.BadZipFile:
        raise PackagingError("ARCHIVE_CORRUPT") from None


def package(
    project: Path,
    tracked: Iterable[str],
    target: Path,
    *,
    folder: str,
    report: Callable[[list[ManifestEntry], int], str],
    preview: bytes | None = None,
) -> tuple[list[ManifestEntry], int]:
    """Selects, scans and archives the project into `target`, then verifies the archive.
    Returns the manifest and the number of excluded files."""
    selection = select_files(project, tracked)
    entries = manifest(selection.files)
    manifest_json = json.dumps(
        {"files": [e.__dict__ for e in entries]}, indent=2, sort_keys=True
    ).encode()
    report_text = report(entries, selection.excluded).encode()
    extras = [(REPORT_NAME, report_text), (MANIFEST_NAME, manifest_json)]
    if preview is not None:
        extras.append((PREVIEW_NAME, preview))
    write_archive(target, folder, selection.files, extras)
    verify_archive(target, folder, {e.path: e.sha256 for e in entries})
    return entries, selection.excluded
