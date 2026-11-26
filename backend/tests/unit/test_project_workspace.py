"""Project workspaces (ADR-0049): platform-only writes under a task's scope, exact
synchronisation, idempotent commits, and refusal of unsafe paths and links."""

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.projects.workspace import AUTHOR_EMAIL, ProjectWorkspaces, WorkspaceError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

WS, WF = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def workspaces(tmp_path: Path) -> ProjectWorkspaces:
    return ProjectWorkspaces(tmp_path / "workspaces")


def _log(project: Path) -> list[str]:
    output = subprocess.run(  # noqa: S603 (fixed git path, test-built arguments)
        [shutil.which("git") or "git", "-C", str(project), "log", "--format=%ae %s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return output.strip().splitlines()


def test_apply_writes_files_and_commits_as_the_platform(workspaces: ProjectWorkspaces) -> None:
    change = workspaces.apply(
        WS,
        WF,
        scope=["src/", "pyproject.toml"],
        files={"src/app/main.py": "print('hi')\n", "pyproject.toml": "[project]\n"},
        message="Implement the application",
    )
    project = workspaces.project_path(WS, WF)
    assert (project / "src" / "app" / "main.py").read_text() == "print('hi')\n"
    assert change.commit_sha == change.head_sha
    assert change.files_written == 2 and change.files_removed == 0
    assert _log(project) == [
        f"{AUTHOR_EMAIL} Implement the application",
        f"{AUTHOR_EMAIL} Start project",
    ]


def test_reapplying_is_idempotent_and_removes_stale_scope_files(
    workspaces: ProjectWorkspaces,
) -> None:
    files = {"src/a.py": "a = 1\n", "src/b.py": "b = 2\n"}
    first = workspaces.apply(WS, WF, scope=["src/"], files=files, message="t2")
    again = workspaces.apply(WS, WF, scope=["src/"], files=files, message="t2")
    assert again.commit_sha is None and again.head_sha == first.head_sha
    assert again.tree_sha256 == first.tree_sha256

    reworked = workspaces.apply(WS, WF, scope=["src/"], files={"src/a.py": "a = 3\n"}, message="t2")
    project = workspaces.project_path(WS, WF)
    assert reworked.files_removed == 1 and reworked.files_written == 1
    assert not (project / "src" / "b.py").exists()
    assert reworked.commit_sha is not None


def test_other_tasks_files_are_left_alone(workspaces: ProjectWorkspaces) -> None:
    workspaces.apply(WS, WF, scope=["README.md"], files={"README.md": "# x\n"}, message="t1")
    workspaces.apply(WS, WF, scope=["src/"], files={"src/a.py": "a\n"}, message="t2")
    workspaces.apply(WS, WF, scope=["src/"], files={"src/b.py": "b\n"}, message="t2")
    project = workspaces.project_path(WS, WF)
    assert (project / "README.md").read_text() == "# x\n"


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ({"tests/x.py": "x"}, "FILE_OUTSIDE_SCOPE"),
        ({"src/big.py": "x" * 200_001}, "FILE_TOO_LARGE"),
    ],
)
def test_files_outside_scope_or_too_large_are_refused(
    workspaces: ProjectWorkspaces, files: dict[str, str], code: str
) -> None:
    with pytest.raises(WorkspaceError) as raised:
        workspaces.apply(WS, WF, scope=["src/"], files=files, message="t")
    assert raised.value.code == code


@pytest.mark.parametrize(
    "path", ["src/../x.py", "src/.git/config", "src/.gitattributes", ".gitmodules", "src\\a.py"]
)
def test_unsafe_paths_are_refused(workspaces: ProjectWorkspaces, path: str) -> None:
    with pytest.raises((WorkspaceError, ValueError)):
        workspaces.apply(WS, WF, scope=["src/", ".gitmodules"], files={path: "x"}, message="t")


def test_an_empty_scope_is_refused(workspaces: ProjectWorkspaces) -> None:
    with pytest.raises(WorkspaceError, match="SCOPE_EMPTY"):
        workspaces.apply(WS, WF, scope=[], files={}, message="t")


def _link_folder_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("cannot create a directory link here")


def test_a_linked_folder_inside_the_project_is_refused(
    workspaces: ProjectWorkspaces, tmp_path: Path
) -> None:
    project = workspaces.ensure_repository(WS, WF)
    outside = tmp_path / "outside"
    outside.mkdir()
    _link_folder_or_skip(project / "src", outside)
    with pytest.raises(WorkspaceError, match="WORKSPACE_PATH_REFUSED"):
        workspaces.apply(WS, WF, scope=["src/"], files={"src/a.py": "a"}, message="t")
    assert list(outside.iterdir()) == []


def test_a_project_gitignore_cannot_hide_platform_files(workspaces: ProjectWorkspaces) -> None:
    workspaces.apply(WS, WF, scope=[".gitignore"], files={".gitignore": "*\n"}, message="t1")
    change = workspaces.apply(WS, WF, scope=["src/"], files={"src/a.py": "a\n"}, message="t2")
    assert change.commit_sha is not None
