"""Web UI checks (Step 9.10, ADR-0057) against the real python-3.12-web sandbox image: static
checks, the headless-browser smoke test, the screenshot, its UI_PREVIEW artifact and its copy
in the VERIFIED package.

Needs Docker and the image from `scripts/sandbox.ps1 build`; skipped otherwise (CI builds it).
"""

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.specialists import _MOCK_INDEX_HTML, _MOCK_WEB_APP, _MOCK_WEB_TEST
from app.artifacts.storage import LocalArtifactStorage
from app.db.sync_session import session_scope
from app.execution import outbox
from app.models import Artifact, ArtifactKind, IntegrationStatus, ProjectIntegration, Workflow
from app.planning.plan import MOCK_UI_FLOWS
from app.projects import integration_job, packaging
from app.projects.profiles import PROFILES
from app.projects.workspace import ProjectWorkspaces
from app.sandbox.runner import DockerSandbox
from tests.integration.test_project_integration import _built_and_applied
from tests.integration.test_project_packaging import _package
from tests.integration.test_project_workspace import _events
from tests.integration.test_sandbox import _image_ready

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _image_ready("agentflow-sandbox/python-3.12-web:1"), reason="web sandbox image missing"
    ),
]

Factory = sessionmaker[Session]
WEB = PROFILES["python-3.12-web"]
GOOD = {
    "pyproject.toml": '[project]\nname = "shop"\nversion = "0.1.0"\n',
    "README.md": "# Shop\n",
    "src/mock_app/__init__.py": "",
    "src/mock_app/main.py": _MOCK_WEB_APP,
    "static/index.html": _MOCK_INDEX_HTML,
    "tests/test_mock_app.py": _MOCK_WEB_TEST,
}


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "project"
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return root


def _check(name: str) -> list[str]:
    return list(dict(WEB.checks)[name])


def test_a_good_web_project_passes_every_check_and_leaves_a_screenshot(tmp_path: Path) -> None:
    project = _project(tmp_path, GOOD)
    sandbox = DockerSandbox()
    for name, command in WEB.checks:
        collect = [WEB.preview[1]] if WEB.preview and WEB.preview[0] == name else []
        result = sandbox.run(WEB.image, project, list(command), collect)
        assert result.succeeded, f"{name}: {result.output}"
        if collect:
            assert result.files[collect[0]].startswith(b"\x89PNG")


def test_static_checks_name_the_broken_files(tmp_path: Path) -> None:
    page = _MOCK_INDEX_HTML.replace(
        "</body>",
        '<script src="https://cdn.example.com/lib.js"></script>\n'
        '<script src="app.js"></script>\n</body>',
    )
    project = _project(tmp_path, {**GOOD, "static/index.html": page, "static/app.js": "let = ;\n"})
    result = DockerSandbox().run(WEB.image, project, _check("web"))
    assert not result.succeeded
    assert "static/app.js: JavaScript syntax error" in result.output
    assert "static/index.html" in result.output and "external URL" in result.output


def test_the_browser_check_catches_runtime_errors_and_unlabelled_controls(tmp_path: Path) -> None:
    page = _MOCK_INDEX_HTML.replace('<label for="name">Name</label>', "").replace(
        "</body>", '<script src="app.js"></script>\n</body>'
    )
    files = {**GOOD, "static/index.html": page, "static/app.js": "missingFunction();\n"}
    result = DockerSandbox().run(
        WEB.image, _project(tmp_path, files), _check("ui"), [".agentflow/ui-preview.png"]
    )
    assert not result.succeeded
    assert "missingFunction" in result.output
    assert "form control without a label" in result.output
    # The screenshot is still taken, so people (and fixes) can see what went wrong.
    assert result.files[".agentflow/ui-preview.png"].startswith(b"\x89PNG")


def test_the_page_is_stored_as_a_ui_preview_and_packaged(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    storage = LocalArtifactStorage(tmp_path / "artifacts", max_bytes=10 * 1024 * 1024)
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    # Turn the built project into a web project: platform writes, as the apply job would.
    for scope, files in (
        (["src/"], {"src/mock_app/__init__.py": "", "src/mock_app/main.py": _MOCK_WEB_APP}),
        (["static/"], {"static/index.html": _MOCK_INDEX_HTML}),
        (["tests/"], {"tests/test_mock_app.py": _MOCK_WEB_TEST}),
    ):
        workspaces.apply(
            graph.workspace_id, graph.workflow_id, scope=scope, files=files, message="web"
        )
    with session_scope(admin_factory) as s:
        workflow = s.get(Workflow, graph.workflow_id)
        assert workflow is not None and workflow.project_spec is not None
        workflow.project_spec = {
            **workflow.project_spec,
            "stack_profile": "python-3.12-web",
            "ui_flows": MOCK_UI_FLOWS,
        }
        s.commit()

    event = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    with session_scope(worker_factory) as s:
        job = integration_job.claim(
            s, workspaces, event_id=event.id, workspace_id=event.workspace_id,
            workflow_id=graph.workflow_id,
        )  # fmt: skip
    assert isinstance(job, integration_job.IntegrationJob)
    outcome = integration_job.execute(DockerSandbox(), job)
    with session_scope(worker_factory) as s:
        assert integration_job.finish(s, workspaces, job, outcome, storage) == "passed"

    with session_scope(admin_factory) as s:
        run = s.scalars(
            select(ProjectIntegration).where(ProjectIntegration.workflow_id == graph.workflow_id)
        ).one()
        assert run.status == IntegrationStatus.PASSED
        assert [c["name"] for c in run.checks] == ["compile", "tests", "web", "ui"]
        ui = run.checks[-1]
        assert "1 planned user flow(s) passed" in ui["output"]  # ADR-0062
        artifact = s.get(Artifact, ui["preview_artifact_id"])
        assert artifact is not None and artifact.kind == ArtifactKind.UI_PREVIEW
        assert artifact.media_type == "image/png" and artifact.size_bytes > 1000
        preview = (artifact.storage_key, artifact.size_bytes, artifact.sha256)

    package_event = _events(admin_factory, graph, outbox.PROJECT_PACKAGE)[-1]
    assert _package(worker_factory, workspaces, storage, package_event) == "ready"
    with session_scope(admin_factory) as s:
        archive = s.scalars(
            select(Artifact).where(
                Artifact.workflow_id == graph.workflow_id,
                Artifact.kind == ArtifactKind.PROJECT_ARCHIVE,
            )
        ).one()
        archive_path = storage.verified_path(
            archive.storage_key, size_bytes=archive.size_bytes, sha256=archive.sha256
        )
    shot = storage.verified_path(preview[0], size_bytes=preview[1], sha256=preview[2])
    with zipfile.ZipFile(archive_path) as zf:
        names = {n.split("/", 1)[1]: n for n in zf.namelist()}
        assert zf.read(names[packaging.PREVIEW_NAME]) == shot.read_bytes()
        report = zf.read(names[packaging.REPORT_NAME]).decode()
    assert f"`{packaging.PREVIEW_NAME}`" in report


_SHOP_APP = """from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI()
PRODUCTS: list[dict[str, object]] = {seed}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {{"status": "ok"}}


@app.get("/api/products")
def products() -> list[dict[str, object]]:
    return PRODUCTS


@app.get("/api/cart")
def cart() -> dict[str, object]:
    return {{"items": [], "total": 0}}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
"""
_SHOP_PAGE = _MOCK_INDEX_HTML.replace(
    "</body>",
    """<ul id="products"></ul>
<script>
Promise.all([fetch('/api/products'), fetch('/api/cart')]).then(async ([p]) => {
  for (const item of await p.json()) {
    const li = document.createElement('li'); li.textContent = item.name;
    document.getElementById('products').append(li);
  }
});
</script>
</body>""",
)


@pytest.mark.parametrize(
    ("seed", "passes"),
    [("[]", False), ('[{"id": 1, "name": "Notebook"}, {"id": 2, "name": "Pen"}]', True)],
)
def test_a_first_page_without_any_data_fails_with_a_hint_to_seed_it(
    tmp_path: Path, seed: str, passes: bool
) -> None:
    # Step 9.14 (ADR-0060): an app whose data APIs all return nothing shows an empty page; an
    # empty cart next to seeded products is fine.
    files = {
        **GOOD,
        "src/mock_app/main.py": _SHOP_APP.format(seed=seed),
        "static/index.html": _SHOP_PAGE,
    }
    result = DockerSandbox().run(
        WEB.image, _project(tmp_path, files), _check("ui"), [".agentflow/ui-preview.png"]
    )
    assert result.succeeded is passes, result.output
    if not passes:
        assert "src/mock_app/main.py: the page shows no data" in result.output
        assert "/api/cart, /api/products" in result.output
        assert "sample records at startup" in result.output


def _flows(*flows: Any) -> dict[str, bytes]:
    return {".agentflow/ui-flows.json": json.dumps(list(flows)).encode()}


def test_planned_flows_run_in_the_browser_and_name_the_failing_step(tmp_path: Path) -> None:
    # Step 9.16 (ADR-0062): the planned flows click, fill and expect on the real page.
    project = _project(tmp_path, GOOD)
    passing = DockerSandbox().run(WEB.image, project, _check("ui"), (), _flows(*MOCK_UI_FLOWS))
    assert passing.succeeded, passing.output
    assert "1 planned user flow(s) passed" in passing.output

    broken = {
        "name": "Check out",
        "steps": [
            {"action": "fill", "target": "Name", "value": "Ada"},
            {"action": "click", "target": "Checkout"},
            {"action": "expect_text", "target": "Order confirmed"},
        ],
    }
    failing = DockerSandbox().run(WEB.image, project, _check("ui"), (), _flows(broken))
    assert not failing.succeeded
    assert (
        "static/index.html: UI flow 'Check out' step 2 (click 'Checkout'): "
        "no visible button or link named 'Checkout'"
    ) in failing.output


def test_a_project_cannot_supply_the_platforms_files(tmp_path: Path) -> None:
    # The copy's .agentflow/ folder is replaced by the platform's inputs only (ADR-0062).
    files = {**GOOD, ".agentflow/ui-flows.json": "[]", ".agentflow/ui-preview.png": "fake"}
    listing = ["python", "-c", "import os; print(sorted(os.listdir('.agentflow')))"]
    result = DockerSandbox().run(
        WEB.image, _project(tmp_path, files), listing, (), _flows(*MOCK_UI_FLOWS)
    )
    assert result.succeeded and "['ui-flows.json']" in result.output
    with pytest.raises(ValueError, match="directly in .agentflow/"):
        DockerSandbox().run(WEB.image, tmp_path, listing, (), {"src/x.py": b""})
