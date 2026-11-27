"""Project workspaces (ADR-0049): one local Git repository per software project, written only
by the platform.

Agents never touch it. After a task is accepted, the apply job makes the files under the task's
`file_scope` match its accepted proposal exactly (writing proposed files, removing earlier files
of that scope that are no longer proposed) and commits the change as the platform.

Safety rules:
- The root (`PROJECT_WORKSPACE_ROOT`) is outside the repository; a project's folder is
  `<root>/<workspace_id>/<workflow_id>`, from UUIDs only.
- Every path is checked again here: the Developer path rules (`safe_relative_path`), inside the
  task's scope, no Git control files, and no symlink or junction anywhere between the project
  folder and the file. Files are written atomically.
- Git runs with a fixed, minimal environment: no system or global configuration, hooks
  disabled, no signing, no pager, no credential helpers or prompts, and a platform identity. Git
  only reads and writes files here; it never fetches, pushes or runs project content.
"""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from app.agents.outputs import safe_relative_path
from app.projects.scope import in_scope

AUTHOR_NAME = "AgentFlow"
AUTHOR_EMAIL = "agentflow@localhost"
GIT_TIMEOUT_SECONDS = 60
MAX_FILE_BYTES = 200_000
# Git reads these from the work tree; the platform never lets a project define them.
_GIT_CONTROL_NAMES = frozenset({".git", ".gitattributes", ".gitmodules"})
_SHA = re.compile(r"^[0-9a-f]{40}$")


class WorkspaceError(Exception):
    """A workspace failure with a fixed, safe code (never a path or Git output)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AppliedChange:
    commit_sha: str | None  # None when the scope already matched (nothing to commit)
    head_sha: str
    files_written: int
    files_removed: int
    tree_sha256: str  # hash of the scope's paths and contents after the change


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _git_environment(empty_hooks: Path) -> dict[str, str]:
    keep = {
        k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "PATH", "TEMP", "TMP"}
    }
    return {
        **keep,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_AUTHOR_NAME": AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
        "HOME": str(empty_hooks),
        "LC_ALL": "C",
    }


class ProjectWorkspaces:
    def __init__(self, root: Path) -> None:
        self._configured_root = root

    def _root(self) -> Path:
        self._configured_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _is_link(self._configured_root):
            raise WorkspaceError("WORKSPACE_PATH_REFUSED")
        return self._configured_root.resolve(strict=True)

    def project_path(self, workspace_id: uuid.UUID, workflow_id: uuid.UUID) -> Path:
        root = self._root()
        folder = root / str(workspace_id) / str(workflow_id)
        for part in (root / str(workspace_id), folder):
            if part.exists() and (_is_link(part) or not part.is_dir()):
                raise WorkspaceError("WORKSPACE_PATH_REFUSED")
        return folder

    def _hooks_dir(self) -> Path:
        hooks = self._root() / ".empty-hooks"
        hooks.mkdir(exist_ok=True)
        return hooks

    def _git(self, project: Path, *args: str) -> str:
        executable = shutil.which("git")
        if executable is None:
            raise WorkspaceError("GIT_UNAVAILABLE")
        hooks = self._hooks_dir()
        command = [
            executable,
            "-c", f"core.hooksPath={hooks}",
            "-c", "commit.gpgsign=false",
            "-c", "core.autocrlf=false",
            "-c", "core.symlinks=false",
            "-c", "core.fsmonitor=false",
            "-c", "credential.helper=",
            "-C", str(project),
            *args,
        ]  # fmt: skip
        try:
            # Fixed executable and platform-built arguments; no shell.
            completed = subprocess.run(  # noqa: S603
                command,
                env=_git_environment(hooks),
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise WorkspaceError("GIT_FAILED") from None
        if completed.returncode != 0:
            raise WorkspaceError("GIT_FAILED")
        return completed.stdout.strip()

    def ensure_repository(self, workspace_id: uuid.UUID, workflow_id: uuid.UUID) -> Path:
        """Creates the project folder and its repository (branch `main`) if needed."""
        project = self.project_path(workspace_id, workflow_id)
        project.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not (project / ".git").is_dir():
            self._git(project, "init", "--quiet", "--initial-branch=main")
            self._git(project, "commit", "--quiet", "--allow-empty", "-m", "Start project")
        return project

    def tracked_files(self, project: Path) -> list[str]:
        """The files committed at HEAD (relative, `/`-separated)."""
        output = self._git(project, "ls-files", "-z")
        return [path for path in output.split("\0") if path]

    def read_scope(
        self, project: Path, scope: list[str] | None, *, max_files: int, max_chars: int
    ) -> dict[str, str]:
        """The committed files under a scope (every committed file with None), read with the
        same path checks as writes, within
        the given limits (in path order; what does not fit is left out)."""
        files: dict[str, str] = {}
        used = 0
        for relative in sorted(self.tracked_files(project)):
            if scope is not None and not in_scope(scope, relative):
                continue
            target = self._target(project, relative)
            if not target.is_file():
                continue
            content = target.read_bytes().decode("utf-8", "replace")
            if len(files) >= max_files or used + len(content) > max_chars:
                break
            files[relative] = content
            used += len(content)
        return files

    def head(self, project: Path) -> str:
        sha = self._git(project, "rev-parse", "HEAD")
        if not _SHA.fullmatch(sha):
            raise WorkspaceError("GIT_FAILED")
        return sha

    def _target(self, project: Path, relative: str) -> Path:
        safe_relative_path(relative)
        parts = relative.split("/")
        if any(part.lower() in _GIT_CONTROL_NAMES for part in parts):
            raise WorkspaceError("WORKSPACE_PATH_REFUSED")
        current = project
        for part in parts:
            current = current / part
            if _is_link(current):
                raise WorkspaceError("WORKSPACE_PATH_REFUSED")
        resolved = current.resolve(strict=False)
        if not resolved.is_relative_to(project.resolve(strict=True)):
            raise WorkspaceError("WORKSPACE_PATH_REFUSED")
        return current

    def _scope_files(self, project: Path, scope: Iterable[str]) -> list[str]:
        """Files currently tracked or present under the scope (relative, `/`-separated)."""
        found = []
        for path in project.rglob("*"):
            relative = path.relative_to(project).as_posix()
            if relative.split("/", 1)[0] == ".git" or not path.is_file() or _is_link(path):
                continue
            if in_scope(scope, relative):
                found.append(relative)
        return sorted(found)

    def apply(
        self,
        workspace_id: uuid.UUID,
        workflow_id: uuid.UUID,
        *,
        scope: list[str],
        files: Mapping[str, str],
        message: str,
    ) -> AppliedChange:
        """Makes the scope's files match `files` exactly and commits. Idempotent: applying the
        same files again changes nothing."""
        if not scope:
            raise WorkspaceError("SCOPE_EMPTY")
        for relative, content in files.items():
            if not in_scope(scope, relative):
                raise WorkspaceError("FILE_OUTSIDE_SCOPE")
            if len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise WorkspaceError("FILE_TOO_LARGE")
        project = self.ensure_repository(workspace_id, workflow_id)
        written = removed = 0
        wanted = {path.casefold(): path for path in files}
        for existing in self._scope_files(project, scope):
            if existing.casefold() not in wanted:
                self._target(project, existing).unlink()
                removed += 1
        for relative, content in sorted(files.items()):
            target = self._target(project, relative)
            data = content.encode("utf-8")
            if target.is_file() and target.read_bytes() == data:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Every folder between the project and the file is checked again after creation.
            self._target(project, relative)
            descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                os.replace(temporary, target)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
            written += 1
        # --force: a project's own .gitignore must not hide files the platform wrote.
        self._git(project, "add", "--all", "--force", "--", ".")
        commit_sha = None
        if self._git(project, "status", "--porcelain"):
            self._git(project, "commit", "--quiet", "-m", message[:200] or "Apply task")
            commit_sha = self.head(project)
        return AppliedChange(
            commit_sha=commit_sha,
            head_sha=self.head(project),
            files_written=written,
            files_removed=removed,
            tree_sha256=_tree_hash(files),
        )


def _tree_hash(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(files[path].encode("utf-8")).digest())
    return digest.hexdigest()
