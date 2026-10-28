"""Render the analysis dict into a single self-contained HTML file."""

from __future__ import annotations

import json
import re
from pathlib import Path

WEB = Path(__file__).parent / "web"
# Scripts spliced into app.js at their marker, so they share one closure in a single-file page.
SPLICES = {"// @@AI@@": "ai.js", "// @@HEALTH@@": "health.js"}


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _json_for_script(data: dict) -> str:
    # Nothing in the payload may close the <script> element or open an HTML comment.
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _script() -> str:
    """app.js with its extra scripts spliced in."""
    app = _read("app.js")
    for marker, name in SPLICES.items():
        if app.count(marker) != 1:
            raise RuntimeError(f"app.js must contain {marker} exactly once")
        app = app.replace(marker, _read(name))
    return app


def render_html(data: dict, serve: dict | None = None) -> str:
    html = _read("template.html")
    # Single pass over the placeholders so payload text can never be re-substituted.
    parts = {
        "{{TITLE}}": data["repo"]["name"].replace("<", "&lt;").replace(">", "&gt;"),
        "{{STYLE}}": _read("style.css"),
        "{{SCRIPT}}": _script().replace("</script", "<\\/script"),
        "{{DATA}}": _json_for_script(data),
        "{{SERVE}}": _json_for_script(serve) if serve else "null",
    }
    return re.sub("|".join(re.escape(k) for k in parts), lambda m: parts[m.group(0)], html)
