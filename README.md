# Code Explainer

Code written by a coding agent (or someone else) is hard to follow once there is a lot of it.
Code Explainer turns a repository into an interactive map that anyone can read, from a child to a
senior engineer, using nothing but static analysis. There is no AI model, no network call and no
API key: every sentence is worked out from the code itself.

```bash
pip install -e .                      # add '.[treesitter]' for deeper JavaScript/TypeScript
code-explainer analyze path/to/repo -o report.html --open     # one self-contained HTML file
code-explainer analyze owner/repo                              # or any git URL
code-explainer serve path/to/repo --open                       # live: updates as files change
```

The core has no dependencies beyond the Python standard library, and the report works offline.

## Explained at three levels

A menu at the top switches every explanation between **like I'm 8**, **plain English** and
**technical**. The "like I'm 8" level tells the story of a big house: folders are floors, files are
rooms, functions are helpers who each do one job, classes are recipes, importing is borrowing
tools, calling is asking a helper for a favour.

- **Story:** a guided tour. The big picture, the floors, the front door, then the real call flow
  step by step with the code beside it, and where to wander next.
- **Words:** a glossary (function, import, blast radius, diff, ...) at the same two levels.
- Every file, function, class and folder has a short explanation. Facts come from the code
  (sizes, who uses whom, complexity, docstrings, quoted verbatim). Anything that is only a guess
  from a name is said to be a guess.

## Navigate

- **Flow:** pick a starting point (console script, web route, `main()`, a script) and read what
  runs, in order, as an expandable call tree. Step through it with the code shown. Links are marked
  *certain* (followed through names and imports) or *guess* (matched by method name only).
- **Overview:** reading order, complexity hotspots, files nothing uses, most-changed files,
  copy-pasted functions, and your progress.
- **Treemap:** area is lines of code. Colour by language, complexity, churn, popularity,
  "possibly unused", what you understand, or what changed.
- **Dependencies:** module graph where folders collapse and expand.
- Search (`/`) covers files and symbols. Views are linkable through the URL.

## Review what an agent changed

```bash
code-explainer analyze . --last 3 -o review.html          # the last 3 commits
code-explainer analyze . --base main -o review.html       # everything since main, including uncommitted work
code-explainer analyze . --base v1.0 --head v1.1
```

The **Changes** tab shows files and functions added, removed and modified, with patches; the
blast radius (who depends on the change); which entry points can reach it; leftovers (new code
nothing calls, code that lost its last caller, removals that unchanged code still uses); new
copy-paste; complexity change; and a suggested reading order, riskiest first and tests last.

## Keep track of what you understand

Tick "I understand this file", bookmark files and leave notes. The overview shows a progress bar
and what to read next. In `serve` mode these are saved in `~/.code-explainer/` (never in your
repository); in a static report they are saved in the browser. `serve` binds to `127.0.0.1` only,
because the report contains your source.

## Languages

| | symbols | imports | call graph |
|---|---|---|---|
| Python | yes (`ast`) | yes | yes |
| JavaScript / TypeScript | yes | yes | yes with `[treesitter]`; regex fallback without it (no call graph) |
| Others | size and rough complexity | no | no |

## Honest limits

Analysis is static. Dynamic imports, reflection and framework magic can make a used file look
"possibly unused" and can hide calls, so treat those labels as places to look, not verdicts. Calls
into code outside the repository produce no link. Copy-paste detection finds identical bodies, not
renamed ones. Name-based explanations are conventions ("`get_x` gets x") and are labelled as guesses.

## Development

```bash
pip install -e '.[dev,treesitter]'
playwright install chromium     # for the browser tests; or set CHROMIUM_PATH
pytest
```

Work test-first: run the whole suite before you change anything (so you know the baseline is
green), write a failing test for the new behaviour or bug, make it pass, then run the whole suite
again. CI enforces a coverage floor (90%, or 80% without tree-sitter).

CI runs the tests on Python 3.10 to 3.13, with and without tree-sitter, plus a browser test and
a package build. Pushing a `v*` tag publishes to PyPI (trusted publishing) and creates a GitHub release.
