"""Command line interface."""

from __future__ import annotations

import argparse
import hashlib
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .core import analyze_repo
from .ingest import IngestError, open_repo
from .paths import repo_id, state_dir
from .report import render_html


def _serve(args) -> int:
    from .server import serve

    if args.head and not args.base:
        print("error: --head needs --base", file=sys.stderr)
        return 2

    def ready(httpd, session):
        url = f"http://{args.host if args.host != '0.0.0.0' else '127.0.0.1'}:{httpd.server_port}/"
        print(f"Serving {session.name} at {url}  (Ctrl-C to stop)", file=sys.stderr)
        print(f"Your progress is saved in {session.progress_path.parent}", file=sys.stderr)
        if args.open:
            webbrowser.open(url)

    try:
        with open_repo(args.target, depth=args.depth) as (root, name):
            print(f"Analysing {name} ...", file=sys.stderr)
            # A cloned copy never changes, so only watch local directories.
            serve(root, name, args.host, args.port, args.base, args.head,
                  watch=Path(args.target).expanduser().is_dir(), on_ready=ready)
    except (IngestError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _positive(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def _ai_cache_dir(target: str, root: Path) -> Path:
    """Saved explanations live outside the repo. A local folder shares its cache with `serve`; a clone is keyed by its address."""
    key = repo_id(root) if Path(target).expanduser().is_dir() else hashlib.sha1(target.encode()).hexdigest()[:12]
    return state_dir() / "ai-cache" / key


def _ai_progress(i: int, n: int, target: dict, state: str, note: str) -> None:
    what = f"{target['kind']} {target['path']}"
    if state == "start":
        print(f"[{i}/{n}] {what} ...", file=sys.stderr, flush=True)
    elif state == "failed":
        print(f"      could not explain it: {note}", file=sys.stderr, flush=True)
    else:
        print(f"      {'explained' if state == 'generated' else 'saved answer reused'}", file=sys.stderr, flush=True)


def _explain_with_ai(args, data: dict, cache_dir: Path) -> int:
    """Run the batch and print what happened. A problem never loses the report; it only changes the exit code."""
    from .ai import batch

    print(f"Asking the local AI to explain up to {args.ai_limit} important files and changes "
          "(Ctrl-C keeps what is finished) ...", file=sys.stderr)
    try:
        r = batch.generate(data, cache_dir, args.ai_model, args.ai_limit, args.ai_level, progress=_ai_progress)
    except batch.BatchError as e:
        print(f"warning: {e}\nThe report was written without AI explanations.", file=sys.stderr)
        return 1
    print(f"AI explanations: {r.generated} explained, {r.reused} reused from saved answers, {len(r.failed)} failed.",
          file=sys.stderr)
    if r.stopped:
        print("Stopped early. Run the same command again to continue; finished explanations are reused.", file=sys.stderr)
    if r.aborted:
        print(f"The model stopped answering: {r.aborted}\nRun the same command again to continue.", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="code-explainer",
        description="Turn a code repository into an interactive map you can navigate.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sv = sub.add_parser("serve", help="serve the report locally; it updates as files change and remembers your progress")
    sv.add_argument("target", help="local path, git URL, or GitHub owner/repo")
    sv.add_argument("--port", type=int, default=8765, help="port (default: 8765; 0 picks a free one)")
    sv.add_argument("--host", default="127.0.0.1", help="address to bind (default: 127.0.0.1; the report contains your source)")
    sv.add_argument("--open", action="store_true", help="open the report in a browser")
    sv.add_argument("--base", metavar="REF", help="review the changes since this git ref")
    sv.add_argument("--head", metavar="REF", help="with --base: compare against this ref instead of the working tree")
    sv.add_argument("--depth", type=int, default=500, help="clone depth for remote repos")
    an = sub.add_parser("analyze", help="analyse a repo and write a self-contained HTML report")
    an.add_argument("target", help="local path, git URL, or GitHub owner/repo")
    an.add_argument("-o", "--output", default="report.html", help="output file (default: report.html)")
    an.add_argument("--open", action="store_true", help="open the report in a browser when done")
    an.add_argument("--base", metavar="REF", help="review the changes since this git ref (branch, tag or commit)")
    an.add_argument("--head", metavar="REF", help="with --base: compare against this ref instead of the working tree")
    an.add_argument("--last", type=int, metavar="N", help="review the last N commits (shortcut for --base HEAD~N)")
    an.add_argument("--depth", type=int, default=500, help="clone depth for remote repos (default: 500)")
    an.add_argument("--ai", action="store_true",
                    help="have a local AI model explain the most important files and changes and put the answers in the report")
    an.add_argument("--ai-limit", type=_positive, metavar="N", default=None,
                    help="with --ai: how many files and changes to explain (default: 10)")
    an.add_argument("--ai-model", metavar="[PROVIDER:]MODEL", default=None,
                    help="with --ai: the model to use (default: the one chosen in the AI tab)")
    an.add_argument("--ai-level", choices=("kid", "plain", "tech"), default=None,
                    help="with --ai: how to explain (default: the level saved in the AI tab)")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.last is not None:
        if args.last < 1 or args.base:
            parser.error("--last must be at least 1 and cannot be combined with --base")
        args.base = f"HEAD~{args.last}"
    if args.head and not args.base:
        parser.error("--head needs --base (or --last)")
    if not args.ai and (args.ai_limit is not None or args.ai_model or args.ai_level):
        parser.error("--ai-limit, --ai-model and --ai-level only work together with --ai")
    if args.ai_limit is None:
        args.ai_limit = 10

    ai_dir = None
    try:
        with open_repo(args.target, depth=args.depth) as (root, name):
            print(f"Analysing {name} ...", file=sys.stderr)
            data = analyze_repo(root, name, base=args.base, head=args.head)
            ai_dir = _ai_cache_dir(args.target, root)
    except IngestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    ai_code = _explain_with_ai(args, data, ai_dir) if args.ai else 0
    out = Path(args.output)
    out.write_text(render_html(data), encoding="utf-8")
    r = data["repo"]
    print(f"Wrote {out} ({r['files']} files, {r['loc']:,} lines of code)", file=sys.stderr)
    if "changes" in data:
        c = data["changes"]["summary"]
        print(f"Reviewed {data['changes']['base']}..{data['changes']['head']}: {c['files']} files changed, "
              f"{c['impactedFiles']} other files affected, {c['newDead']} leftovers, {c['newDuplicates']} new duplicates",
              file=sys.stderr)
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return ai_code


if __name__ == "__main__":
    sys.exit(main())
