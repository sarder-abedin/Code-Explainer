"""Command line interface."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .core import analyze_repo
from .ingest import IngestError, open_repo
from .report import render_html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="code-explainer",
        description="Turn a code repository into an interactive map you can navigate.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    an = sub.add_parser("analyze", help="analyse a repo and write a self-contained HTML report")
    an.add_argument("target", help="local path, git URL, or GitHub owner/repo")
    an.add_argument("-o", "--output", default="report.html", help="output file (default: report.html)")
    an.add_argument("--open", action="store_true", help="open the report in a browser when done")
    an.add_argument("--depth", type=int, default=500, help="clone depth for remote repos (default: 500)")
    args = parser.parse_args(argv)

    try:
        with open_repo(args.target, depth=args.depth) as (root, name):
            print(f"Analysing {name} ...", file=sys.stderr)
            data = analyze_repo(root, name)
    except IngestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out = Path(args.output)
    out.write_text(render_html(data), encoding="utf-8")
    r = data["repo"]
    print(f"Wrote {out} ({r['files']} files, {r['loc']:,} lines of code)", file=sys.stderr)
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
