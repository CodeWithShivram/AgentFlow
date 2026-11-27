"""AgentFlow platform check (ADR-0057): static checks of a project's web frontend in static/.

Baked into the python-3.12-web sandbox image; generated code never supplies or changes it.
Run in the project root (/work). Exit 0 when every check passes, 1 otherwise. Messages name the
files involved (static/...) so integration rework can reach the task that owns them.
"""

import re
import subprocess
import sys
from pathlib import Path

import html5lib

ROOT = Path.cwd()
STATIC = ROOT / "static"
# Anything fetched from outside the app: the sandbox is offline, and pages must not depend on
# third-party hosts (CDNs, fonts, trackers).
EXTERNAL = re.compile(
    r"""(?:src|href|action)\s*=\s*["']\s*(?:https?:)?//(?!localhost|127\.0\.0\.1)"""
    r"""|url\(\s*["']?\s*(?:https?:)?//(?!localhost|127\.0\.0\.1)"""
    r"""|@import\s+["']?\s*(?:https?:)?//"""
    r"""|fetch\(\s*["'`]\s*https?://(?!localhost|127\.0\.0\.1)""",
    re.IGNORECASE,
)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def main() -> int:
    problems: list[str] = []
    index = STATIC / "index.html"
    if not index.is_file():
        print("FAIL static/index.html is missing: the page served at / must live there.")
        return 1
    files = sorted(p for p in STATIC.rglob("*") if p.is_file() and not p.is_symlink())
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        name = relative(path)
        if path.suffix.lower() in {".html", ".htm"}:
            if not text.lstrip().lower().startswith("<!doctype html>"):
                problems.append(f"{name}: must start with <!DOCTYPE html>")
            parser = html5lib.HTMLParser(strict=False)
            parser.parse(text)
            for (line, column), code, _data in parser.errors[:10]:
                problems.append(f"{name}:{line}:{column}: HTML error {code}")
        if path.suffix.lower() in {".html", ".htm", ".css", ".js"}:
            for match in EXTERNAL.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{name}:{line}: loads an external URL; keep every asset local")
        if path.suffix.lower() == ".js":
            check = subprocess.run(  # noqa: S603 (fixed executable, platform-built arguments)
                ["node", "--check", str(path)], capture_output=True, text=True, check=False
            )
            if check.returncode != 0:
                detail = (check.stderr or check.stdout).strip().splitlines()
                problems.append(f"{name}: JavaScript syntax error: {' | '.join(detail[:4])}")
    if problems:
        print(f"FAIL {len(problems)} web problem(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"OK {len(files)} file(s) in static/: HTML parses, JavaScript is valid, assets are local.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
