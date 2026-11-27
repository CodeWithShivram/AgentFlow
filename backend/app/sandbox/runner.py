"""Docker sandbox for generated code (ADR-0050). The only place AgentFlow executes a project.

Each run is one short-lived container from a locally built stack profile image, never pulled:
- a throwaway copy of the project tree mounted at /work (the workspace repository is never
  mounted, so generated code cannot change it or its history);
- `--network none`, `--read-only` root filesystem with a size-limited /tmp, `--cap-drop ALL`,
  `no-new-privileges`, a non-root user, and CPU, memory, process and wall-clock limits;
- no environment variables but the image's own, no secrets, no Docker socket, no host folders
  other than the copy;
- output capped and redacted; on timeout the container is killed and removed.

If Docker or the image is unavailable the sandbox raises `SandboxUnavailableError`: callers
fail closed (a check that cannot run is never reported as passed). There is no host fallback.

Docker is one isolation layer, not a guarantee; see ADR-0050 for the threat model.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

DOCKER_TIMEOUT_SECONDS = 30
_IMAGE = re.compile(r"^agentflow-sandbox/[a-z0-9.-]+:[0-9]+$")
# Likely secrets in tool output (tokens, keys, URLs with credentials). Output is still treated
# as untrusted text and shown as plain text only.
_REDACT = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?<=://)[^/\s:@]+:[^/\s@]+(?=@))"
)
_SKIP_COPY = frozenset({".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"})


class SandboxUnavailableError(Exception):
    """Docker or the image is missing: the check cannot run (fail closed)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SandboxLimits:
    cpus: float = 1.0
    memory_mb: int = 1024
    pids: int = 256
    timeout_seconds: int = 300
    tmpfs_mb: int = 256
    output_chars: int = 20_000


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int | None  # None when the run timed out
    timed_out: bool
    output: str  # stdout and stderr, capped and redacted
    duration_ms: int
    # Files the run left in the copy that the caller asked for (ADR-0057), by relative path.
    files: dict[str, bytes] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def redact(text: str) -> str:
    return _REDACT.sub("[redacted]", text)


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters omitted] ...\n{text[-half:]}"


def copy_tree(source: Path, target: Path) -> None:
    """Copies regular files only (no links, no VCS or dependency folders) into `target`."""
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in _SKIP_COPY for part in relative.parts):
            continue
        if path.is_symlink() or path.is_junction():
            continue
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)


IMAGE_USER = "10001:10001"


def container_user() -> str:
    """The user the container runs as: never root. On Linux and macOS it is the calling user,
    so files the run creates in the throwaway copy (e.g. __pycache__ folders) belong to the
    caller and can be removed afterwards; a root caller, and Windows (no uids; Docker Desktop
    maps ownership itself), use the image's unprivileged user."""
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None or getuid() == 0:
        return IMAGE_USER
    return f"{getuid()}:{getgid()}"


def _make_writable(root: Path) -> None:
    # The copy is throwaway; the container's non-root user must be able to write caches.
    for path in [root, *root.rglob("*")]:
        path.chmod(0o777 if path.is_dir() else 0o666)


class DockerSandbox:
    def __init__(self, limits: SandboxLimits | None = None) -> None:
        self.limits = limits or SandboxLimits()

    def _docker(self) -> str:
        executable = shutil.which("docker")
        if executable is None:
            raise SandboxUnavailableError("DOCKER_UNAVAILABLE")
        return executable

    def _quick(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603 (fixed executable, platform-built arguments)
                [self._docker(), *args],
                capture_output=True,
                text=True,
                timeout=DOCKER_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SandboxUnavailableError("DOCKER_UNAVAILABLE") from None

    def ensure_image(self, image: str) -> None:
        if not _IMAGE.fullmatch(image):
            raise SandboxUnavailableError("IMAGE_REFUSED")
        if self._quick("info", "--format", "{{.ServerVersion}}").returncode != 0:
            raise SandboxUnavailableError("DOCKER_UNAVAILABLE")
        if self._quick("image", "inspect", image).returncode != 0:
            raise SandboxUnavailableError("IMAGE_MISSING")

    def run(
        self,
        image: str,
        project: Path,
        command: Sequence[str],
        collect: Sequence[str] = (),
    ) -> SandboxResult:
        """Runs `command` (a fixed platform command) in a fresh container on a copy of
        `project`. Raises SandboxUnavailableError when it cannot run at all."""
        self.ensure_image(image)
        limits = self.limits
        name = f"agentflow-sbx-{uuid.uuid4().hex[:16]}"
        # Best effort: a leftover copy must never fail a run whose results are known.
        with tempfile.TemporaryDirectory(
            prefix="agentflow-sbx-", ignore_cleanup_errors=True
        ) as scratch:
            work = Path(scratch) / "work"
            work.mkdir()
            copy_tree(project, work)
            _make_writable(work)
            arguments = [
                self._docker(), "run", "--rm", "--name", name,
                "--network", "none",
                "--read-only",
                "--tmpfs", f"/tmp:rw,nosuid,nodev,size={limits.tmpfs_mb}m",  # noqa: S108
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--user", container_user(),
                "--pids-limit", str(limits.pids),
                "--memory", f"{limits.memory_mb}m",
                "--memory-swap", f"{limits.memory_mb}m",
                "--cpus", str(limits.cpus),
                "--env", "HOME=/tmp",
                "--volume", f"{work.resolve()}:/work:rw",
                "--workdir", "/work",
                "--pull", "never",
                image,
                *command,
            ]  # fmt: skip
            started = time.monotonic()
            timed_out = False
            try:
                completed = subprocess.run(  # noqa: S603 (fixed executable, platform arguments)
                    arguments,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=limits.timeout_seconds,
                    check=False,
                    env={k: v for k, v in os.environ.items() if not k.startswith("AGENTFLOW")},
                )
                exit_code: int | None = completed.returncode
                output = (completed.stdout or "") + (completed.stderr or "")
            except subprocess.TimeoutExpired as expired:
                timed_out, exit_code = True, None
                stdout = expired.stdout or b""
                output = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout
                # Best effort: a slow Docker must not hide that the run timed out.
                for cleanup in (("kill", name), ("rm", "--force", name)):
                    try:
                        self._quick(*cleanup)
                    except SandboxUnavailableError:
                        pass
            except OSError:
                raise SandboxUnavailableError("DOCKER_UNAVAILABLE") from None
            duration = int((time.monotonic() - started) * 1000)
            files = _collect(work, collect)
        return SandboxResult(
            exit_code=exit_code,
            timed_out=timed_out,
            output=_cap(redact(output), limits.output_chars),
            duration_ms=duration,
            files=files,
        )


MAX_COLLECTED_BYTES = 5 * 1024 * 1024


def _collect(work: Path, paths: Sequence[str]) -> dict[str, bytes]:
    """Reads files a run left in its copy: regular files only, inside the copy, size-capped.
    Anything else is silently left out (the run's own output says what happened)."""
    collected: dict[str, bytes] = {}
    root = work.resolve()
    for relative in paths:
        target = work / relative
        if target.is_symlink() or not target.is_file():
            continue
        if not target.resolve().is_relative_to(root):
            continue
        if target.stat().st_size > MAX_COLLECTED_BYTES:
            continue
        collected[relative] = target.read_bytes()
    return collected
