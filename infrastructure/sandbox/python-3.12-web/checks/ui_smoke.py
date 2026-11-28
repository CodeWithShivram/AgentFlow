"""AgentFlow platform check (ADR-0057): a headless-browser smoke test of a project's web UI.

Baked into the python-3.12-web sandbox image; generated code never supplies or changes it.
Run in the project root (/work), offline. It starts the FastAPI app found at
src/<package>/main.py (`app`) with uvicorn on 127.0.0.1, opens / in headless Chromium, and fails
on JavaScript errors, failed requests, missing accessibility basics, or a first page whose data
APIs all return nothing (projects ship with sample data, ADR-0060). Then it runs the planned
user flows from .agentflow/ui-flows.json, written by the platform (ADR-0062): click a button or
link by its visible name, fill a field by its label, expect a visible text. It always tries to save a
full-page screenshot to .agentflow/ui-preview.png for the platform to show. Messages name the
files involved (static/... or src/...) so integration rework can reach the task that owns them.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path.cwd()
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
SCREENSHOT = ROOT / ".agentflow" / "ui-preview.png"
FLOWS = ROOT / ".agentflow" / "ui-flows.json"
STEP_TIMEOUT_MS = 5_000
LABEL_CHECK = """
() => Array.from(document.querySelectorAll('input, select, textarea'))
  .filter(el => !['hidden', 'submit', 'button', 'reset', 'image'].includes((el.type || '').toLowerCase()))
  .filter(el => {
    if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) return false;
    if (el.closest('label')) return false;
    return !(el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
  })
  .map(el => el.outerHTML.slice(0, 120))
"""


def find_app() -> str | None:
    for main in sorted((ROOT / "src").glob("*/main.py")):
        if "FastAPI(" in main.read_text(encoding="utf-8", errors="replace"):
            return f"{main.parent.name}.main:app"
    return None


def source_for(url: str) -> str:
    """The project file a page URL most likely comes from (static/<path>), for attribution."""
    path = urlparse(url).path.lstrip("/") or "index.html"
    candidate = ROOT / "static" / path
    return f"static/{path}" if candidate.is_file() else url


def is_empty(value: object) -> bool:
    """An empty list, or an object whose lists are all empty (`{"items": [], "total": 0}`).
    Objects without lists (`{"status": "ok"}`) are not data and never count as empty."""
    if isinstance(value, list):
        return not value
    if isinstance(value, dict):
        lists = [v for v in value.values() if isinstance(v, list)]
        return bool(lists) and not any(lists)
    return False


def empty_data(responses: list[object]) -> list[str]:
    """The /api/ paths the page loaded with JSON data, when every one of them was empty."""
    loaded: dict[str, bool] = {}
    for response in responses:
        path = urlparse(response.url).path  # type: ignore[attr-defined]
        try:
            body = response.json()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 (not JSON: not data)
            continue
        if isinstance(body, (list, dict)) and (isinstance(body, list) or any(isinstance(v, list) for v in body.values())):
            loaded[path] = loaded.get(path, True) and is_empty(body)
    return sorted(loaded) if loaded and all(loaded.values()) else []


def run_flow(browser: object, flow: dict, watch: object) -> list[str]:
    """Runs one planned flow on a fresh page; returns its problems (the first failing step)."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})  # type: ignore[attr-defined]
    watch(page)  # type: ignore[operator]
    name = str(flow.get("name", "flow"))[:100]
    try:
        page.goto(f"{BASE}/", wait_until="networkidle", timeout=20_000)
        for number, step in enumerate(flow.get("steps", []), start=1):
            action, target = step.get("action"), str(step.get("target", ""))
            where = f"static/index.html: UI flow '{name}' step {number} ({action} '{target}')"
            try:
                if action == "click":
                    control = page.get_by_role("button", name=target).or_(
                        page.get_by_role("link", name=target)
                    )
                    control.first.click(timeout=STEP_TIMEOUT_MS)
                    try:
                        page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
                    except Exception:  # noqa: BLE001 (a page that keeps polling still continues)
                        pass
                elif action == "fill":
                    page.get_by_label(target).first.fill(
                        str(step.get("value", "")), timeout=STEP_TIMEOUT_MS
                    )
                elif action == "expect_text":
                    page.get_by_text(target).first.wait_for(state="visible", timeout=STEP_TIMEOUT_MS)
                else:
                    return [f"{where}: unknown action"]
            except Exception:  # noqa: BLE001 (Playwright timeouts and missing elements)
                missing = {
                    "click": f"no visible button or link named '{target}'",
                    "fill": f"no field labelled '{target}'",
                    "expect_text": f"the text '{target}' did not appear",
                }
                return [f"{where}: {missing.get(str(action), 'step failed')}"]
        return []
    finally:
        page.close()


def wait_for_port(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                return True
        time.sleep(0.25)
    return False


def main() -> int:
    module = find_app()
    if module is None:
        print("FAIL no FastAPI app found: src/<package>/main.py must define app = FastAPI().")
        return 1
    env = {**os.environ, "PYTHONPATH": "src"}
    server = subprocess.Popen(  # noqa: S603 (fixed executable, platform-built arguments)
        [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    problems: list[str] = []
    api_responses: list[object] = []
    ran = 0
    try:
        if not wait_for_port(20):
            server.terminate()
            output = (server.communicate(timeout=5)[0] or "").strip().splitlines()
            print(f"FAIL the app ({module}) did not start on port {PORT}:")
            for line in output[-25:]:
                print(f"  {line}")
            return 1
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )

            def watch(page: object) -> None:
                """Reports JavaScript errors, failed requests and HTTP errors of a page."""
                page.on(  # type: ignore[attr-defined]
                    "console",
                    lambda m: problems.append(
                        f"{source_for(m.location.get('url', ''))}:"
                        f"{m.location.get('lineNumber', 0) + 1}: console error: {m.text[:300]}"
                    )
                    if m.type == "error"
                    else None,
                )
                page.on(  # type: ignore[attr-defined]
                    "pageerror", lambda e: problems.append(f"page JavaScript error: {str(e)[:300]}")
                )
                page.on(  # type: ignore[attr-defined]
                    "requestfailed",
                    lambda r: problems.append(f"{source_for(r.url)}: request failed ({r.failure})"),
                )
                page.on(  # type: ignore[attr-defined]
                    "response",
                    lambda r: problems.append(
                        f"{source_for(r.url)}: HTTP {r.status} for {urlparse(r.url).path}"
                    )
                    if r.status >= 400 and not r.url.endswith("/favicon.ico")
                    else None,
                )

            page = browser.new_page(viewport={"width": 1280, "height": 800})
            watch(page)
            page.on(
                "response",
                lambda r: api_responses.append(r)
                if r.request.method == "GET"
                and r.status == 200
                and urlparse(r.url).path.startswith("/api/")
                else None,
            )
            response = page.goto(f"{BASE}/", wait_until="networkidle", timeout=20_000)
            if response is None or response.status != 200:
                status = response.status if response else "no response"
                problems.append(f"src/{module.split('.')[0]}/main.py: GET / returned {status}")
            page.wait_for_timeout(500)
            if not (page.title() or "").strip():
                problems.append("static/index.html: the page has no <title>")
            if not page.evaluate("() => document.documentElement.getAttribute('lang')"):
                problems.append("static/index.html: <html> has no lang attribute")
            for element in page.evaluate(LABEL_CHECK):
                problems.append(f"static/index.html: form control without a label: {element}")
            empty = empty_data(api_responses)
            if empty:
                problems.append(
                    f"src/{module.split('.')[0]}/main.py: the page shows no data: every API it "
                    f"loaded returned an empty list ({', '.join(empty)}). Create sample records at "
                    "startup while the store is empty, so the first run shows content."
                )
            # The preview shows the first page as people meet it, before any flow changes data.
            SCREENSHOT.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(SCREENSHOT), full_page=True)
            page.close()
            flows = json.loads(FLOWS.read_text(encoding="utf-8")) if FLOWS.is_file() else []
            for flow in flows:
                problems.extend(run_flow(browser, flow, watch))
            ran = len(flows)
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    if problems:
        print(f"FAIL {len(problems)} UI problem(s) on / :")
        for problem in dict.fromkeys(problems):
            print(f"  {problem}")
        return 1
    print(
        "OK the page loads without errors, shows data, and has a title, a lang attribute and "
        f"labelled controls; {ran} planned user flow(s) passed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
