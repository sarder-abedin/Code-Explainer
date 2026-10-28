# Code Explainer

Code written by a coding agent (or someone else) is hard to follow once there is a lot of it.
Code Explainer turns a repository into a single, self-contained, interactive HTML report so you can
see the shape of the code before you read any of it.

```bash
pip install -e .
code-explainer analyze path/to/repo -o report.html --open
code-explainer analyze owner/repo          # or any git URL
```

No dependencies beyond the Python standard library, and the report works offline.

## What you get (Phase 1)

- **Overview**: size, language mix, a *Start here* reading order built from entry points and
  what they import, complexity hotspots, most-changed files (from git history), and files that
  look unused.
- **Treemap**: area is lines of code; colour by language, complexity, churn, how many files import
  it, or "possibly unused". Click folders to zoom, files to inspect.
- **Dependencies**: module graph where folders collapse and expand. Hover to highlight neighbours.
- **Detail panel** for any file: symbols with docstrings, imports and importers, and the source
  with jump-to-line. Search (`/`) covers files and symbols. State lives in the URL hash, so
  views are linkable.

## Honest limits

Analysis is static. Python uses `ast`; JS/TS use a regex scanner for now (tree-sitter comes in
Phase 2); other languages get size and a rough complexity only. Dynamic imports and framework
magic can make a used file look "possibly unused", so treat that as a place to look, not a verdict.

## Roadmap

1. Architecture map (this release)
2. JS/TS depth (tree-sitter, optional extra) and call-graph flow tracing from entry points (done)
3. Review of agent changes: diff mode, blast radius, new dead code
4. `serve` mode with live reload, notes and an "understood" tracker

## Development

```bash
pip install -e '.[dev]'
pytest
```
