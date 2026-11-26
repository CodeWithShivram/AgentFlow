"""The Docker sandbox (ADR-0050) against real containers: offline, read-only, non-root,
limited, isolated from the source tree and the host environment, and fail-closed.

Needs Docker and the images from `scripts/sandbox.ps1 build`; skipped otherwise (CI builds them).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.sandbox.runner import (
    DockerSandbox,
    SandboxLimits,
    SandboxUnavailableError,
    redact,
)

pytestmark = pytest.mark.integration

PYTHON = "agentflow-sandbox/python-3.12:1"
NODE = "agentflow-sandbox/node-24-typescript:1"


def _image_ready(image: str) -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    found = subprocess.run(  # noqa: S603 (fixed executable)
        [docker, "image", "inspect", image], capture_output=True, check=False
    )
    return found.returncode == 0


needs_python = pytest.mark.skipif(not _image_ready(PYTHON), reason="python sandbox image missing")
needs_node = pytest.mark.skipif(not _image_ready(NODE), reason="node sandbox image missing")


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "project"
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return root


PY_PROJECT = {
    "pyproject.toml": '[project]\nname = "x"\nversion = "0"\n\n[tool.pytest.ini_options]\n'
    'pythonpath = ["src"]\n',
    "src/calc.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
    "tests/test_calc.py": "from calc import add\n\n\ndef test_add() -> None:\n"
    "    assert add(1, 2) == 3\n",
}
PYTEST = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]


def _python(
    tmp_path: Path, code: str, limits: SandboxLimits | None = None
) -> tuple[int | None, str]:
    project = _project(tmp_path, {"main.py": code})
    result = DockerSandbox(limits).run(PYTHON, project, ["python", "main.py"])
    return result.exit_code, result.output


@needs_python
def test_a_passing_and_a_failing_python_project(tmp_path: Path) -> None:
    passing = DockerSandbox().run(PYTHON, _project(tmp_path / "a", PY_PROJECT), PYTEST)
    assert passing.succeeded and "1 passed" in passing.output

    broken = {**PY_PROJECT, "src/calc.py": "def add(a, b):\n    return a - b\n"}
    failing = DockerSandbox().run(PYTHON, _project(tmp_path / "b", broken), PYTEST)
    assert not failing.succeeded and failing.exit_code == 1 and "1 failed" in failing.output


@needs_python
def test_there_is_no_network(tmp_path: Path) -> None:
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    print('CONNECTED')\n"
        "except OSError:\n"
        "    print('BLOCKED')\n"
    )
    assert _python(tmp_path, code) == (0, "BLOCKED\n")


@needs_python
def test_root_filesystem_is_read_only_and_the_user_is_not_root(tmp_path: Path) -> None:
    code = (
        "import os\n"
        "print(os.getuid())\n"
        "try:\n"
        "    open('/usr/evil', 'w')\n"
        "    print('WROTE')\n"
        "except OSError:\n"
        "    print('READ-ONLY')\n"
        "open('/work/scratch.txt', 'w').write('ok')\n"
        "open('/tmp/scratch.txt', 'w').write('ok')\n"
        "print('WORK-OK')\n"
    )
    assert _python(tmp_path, code) == (0, "10001\nREAD-ONLY\nWORK-OK\n")
    # Writes went to a throwaway copy, never to the source tree.
    assert not (tmp_path / "project" / "scratch.txt").exists()


@needs_python
def test_host_environment_never_reaches_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "sk-test-not-a-real-key-000000000000")
    monkeypatch.setenv("POSTGRES_PASSWORD", "host-secret")
    # The image's own variables (e.g. the public GPG_KEY id of CPython releases) may exist; the
    # host's names and values must not.
    code = (
        "import os\n"
        "names = {'MODEL_API_KEY', 'POSTGRES_PASSWORD'} & set(os.environ)\n"
        "values = [v for v in os.environ.values() if 'host-secret' in v or 'sk-test' in v]\n"
        "print(sorted(names), values)\n"
    )
    assert _python(tmp_path, code) == (0, "[] []\n")


@needs_python
def test_timeouts_kill_and_remove_the_container(tmp_path: Path) -> None:
    started = DockerSandbox(SandboxLimits(timeout_seconds=5))
    result = started.run(PYTHON, _project(tmp_path, {"main.py": "import time\ntime.sleep(60)\n"}),
                         ["python", "main.py"])  # fmt: skip
    assert result.timed_out and result.exit_code is None and not result.succeeded
    left = subprocess.run(  # noqa: S603
        [shutil.which("docker") or "docker", "ps", "-aq", "--filter", "name=agentflow-sbx-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert left == ""


@needs_python
def test_process_count_is_limited(tmp_path: Path) -> None:
    code = (
        "import subprocess\n"
        "procs = []\n"
        "try:\n"
        "    for _ in range(200):\n"
        "        procs.append(subprocess.Popen(['sleep', '5']))\n"
        "    print('UNLIMITED')\n"
        "except OSError:\n"
        "    print('LIMITED')\n"
    )
    exit_code, output = _python(tmp_path, code, SandboxLimits(pids=32))
    assert "LIMITED" in output and "UNLIMITED" not in output


@needs_python
def test_links_in_the_source_are_not_copied(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("host secret")
    project = _project(tmp_path, {"main.py": "import os\nprint(os.path.exists('link.txt'))\n"})
    try:
        (project / "link.txt").symlink_to(secret)
    except OSError:
        pytest.skip("cannot create a symlink here")
    result = DockerSandbox().run(PYTHON, project, ["python", "main.py"])
    assert result.output == "False\n"


@needs_node
def test_a_typescript_project_type_checks_and_tests_offline(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "package.json": '{"name": "x", "private": true, "type": "module"}\n',
            "tsconfig.json": '{"compilerOptions": {"strict": true, "module": "esnext",'
            ' "moduleResolution": "bundler", "types": ["node"], "noEmit": true}}\n',
            "src/sum.ts": "export function sum(a: number, b: number): number {\n"
            "  return a + b;\n}\n",
            "tests/sum.test.ts": 'import { expect, test } from "vitest";\n'
            'import { sum } from "../src/sum";\n\ntest("sum", () => {\n'
            "  expect(sum(1, 2)).toBe(3);\n});\n",
        },
    )
    command = ["sh", "-c", "ln -s /opt/deps/node_modules node_modules && tsc -p . && vitest run"]
    result = DockerSandbox().run(NODE, project, command)
    assert result.succeeded, result.output


def test_missing_or_foreign_images_fail_closed(tmp_path: Path) -> None:
    sandbox = DockerSandbox()
    with pytest.raises(SandboxUnavailableError) as refused:
        sandbox.run("python:3.12-slim", tmp_path, ["python", "-V"])
    assert refused.value.code == "IMAGE_REFUSED"
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    with pytest.raises(SandboxUnavailableError) as missing:
        sandbox.run("agentflow-sandbox/nope:9", tmp_path, ["true"])
    assert missing.value.code in {"IMAGE_MISSING", "DOCKER_UNAVAILABLE"}


def test_docker_missing_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.sandbox.runner.shutil.which", lambda name: None)
    with pytest.raises(SandboxUnavailableError, match="DOCKER_UNAVAILABLE"):
        DockerSandbox().run(PYTHON, tmp_path, ["python", "-V"])


def test_output_redaction() -> None:
    text = "token sk-abcdefghijklmnopqrstu and https://user:pass@example.com/x AKIAABCDEFGHIJKLMNOP"
    redacted = redact(text)
    assert "sk-abcdef" not in redacted and "pass@" not in redacted and "AKIA" not in redacted
    assert redacted.count("[redacted]") == 3
