<p align="center"><img src="code_explainer/web/logo.svg" width="96" alt="Code Explainer logo"></p>

# Code Explainer

Code written by a coding agent (or someone else) is hard to follow once there is a lot of it.
Code Explainer turns a repository into an interactive map that anyone can read, from a complete beginner to a
senior engineer. The core is pure static analysis: every sentence is worked out from the code itself, with no
AI model, no network call and no API key. On top of that there is an optional AI layer that uses a model running on
*your own* computer (Ollama, LM Studio or any OpenAI-compatible server); it never runs unless you ask for it.

```bash
pip install -e .                      # add '.[treesitter]' for deeper JavaScript/TypeScript
code-explainer analyze path/to/repo -o report.html --open     # one self-contained HTML file
code-explainer analyze owner/repo                              # or any git URL
code-explainer serve path/to/repo --open                       # live: updates as files change, AI tab included
code-explainer analyze path/to/repo --ai                       # also have a local model explain the key files
```

What you get, tab by tab: **Story** (a guided tour), **Changes** (review of a diff), **Map** (the architecture),
**Overview**, **Flow**,
**Treemap**, **Dependencies**, **Health** (which tests reach the code, and a safety check), **AI** (local model, `serve`
only) and **Words** (a glossary).

Remote repositories are cloned with your normal git setup. For a private repository, either clone it yourself first
and pass the folder, or make sure `git clone` already works for it (an SSH key, a credential helper, or
`gh auth login`).

The core needs only the Python standard library (plus `tomli` on Python 3.10, which has no built-in TOML parser), and the report works offline.

## Explained at three levels

A menu at the top switches every explanation between **beginner-friendly**, **plain English** and
**technical**. The beginner-friendly level tells the story of a big house: folders are floors, files are
rooms, functions are helpers who each do one job, classes are recipes, importing is borrowing
tools, calling is asking a helper for a favour.

- **Story:** a guided tour. The big picture, the floors, the front door, then the real call flow
  step by step with the code beside it, and where to wander next.
- **Words:** a glossary (function, import, blast radius, diff, ...) at the same two levels.
- Every file, function, class and folder has a short explanation. Facts come from the code
  (sizes, who uses whom, complexity, docstrings, quoted verbatim). Anything that is only a guess
  from a name is said to be a guess.

## Navigate

Open any file and its panel has four tabs: **Summary** (what it is for, its functions), **Code**, **Connections**
(what it imports and what imports it, and its part on the map) and **Tests & safety**. Under the file name a
**Show in** bar jumps to the same file or function in the other views: Map, Flow (any function can be a starting
point), Dependencies, Treemap, Health (filtered to this file) and, in a review, Changes. The Back and Forward buttons
of your browser follow every file and tab you visit, and the address always links to exactly what you were looking at.

- **Flow:** pick a starting point (console script, web route, `main()`, a script) and read what
  runs, in order, as an expandable call tree. Step through it with the code shown. Links are marked
  *certain* (followed through names and imports) or *guess* (matched by method name only).
- **Overview:** reading order, complexity hotspots, files nothing uses, most-changed files,
  copy-pasted functions, and your progress.
- **Treemap:** area is lines of code. Colour by language, complexity, churn, popularity,
  "possibly unused", what you understand, or what changed.
- **Dependencies:** module graph where folders collapse and expand.
- **Map:** the architecture on one page. The parts of the project are boxes (found from its folders, or from which
  files import each other in a flat project), arrows show which part uses which with the number of imports and calls
  behind them, and dashed boxes are the outside systems it talks to (a database, the web, other programs, a message
  queue, an AI service...), recognised from the libraries it imports. Parts that need each other (an import circle) are
  drawn in red. Click a part to read what it does, who it uses and who uses it, how much of it tests reach, and its
  safety findings; click an arrow to see the exact imports behind it. Colour the parts by test coverage or safety
  findings, **Copy as Mermaid** for a README or pull request, or **Download SVG**. Every file panel has a link back to
  its part, and from a part you reach its files and then their flowcharts: system, part, file, function.
- **Flowcharts:** every function that makes a choice has a "▦ Flowchart" button in the file panel (and on Flow
  steps). It draws the function step by step: choices as diamonds, loops, `try`/`except` paths, early returns and
  errors. The boxes are worded for the reading level you picked (the technical level shows the exact code) and
  link to their lines. Click a box to read it, and click a call inside it to open that function's own flowchart
  (a breadcrumb takes you back). Scroll to zoom, drag to move, **Fit** shows the whole chart, **Copy as Mermaid**
  gives text you can paste into GitHub or a README, and **Download SVG** saves the picture. Python is fully
  supported; JavaScript/TypeScript needs `[treesitter]`. The words come from fixed rules, not a model.
- **Health** and **AI** are described below.
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

## Health: tests and safety

The **Health** tab answers two questions an agent's code leaves open, without running anything.

- **Which tests reach which code?** Starting from every test function it follows the call graph and marks each
  function as reached directly, reached through other code, reached only by a guess (`obj.method()` resolved from the
  name), or not seen at all. Each file shows the tests that reach it and the functions that no test was seen reaching;
  the tab lists the riskiest untested functions (complex, widely used, often changed). In a change review, new or
  edited functions that no test reaches are called out on the change card and in a summary count.
  This is a static estimate, not coverage: dynamic calls, fixtures and mocks are invisible to it.
- **Is anything obviously unsafe?** A pattern check that explains each finding in plain words and says how to fix it:
  `eval`/`exec`, shell commands built from text, database queries built from strings, unsafe `pickle`/`yaml.load`,
  switched-off TLS checks, weak hashes, swallowed errors, `innerHTML` and similar in JavaScript, passwords and
  well-known key formats written into code (shown masked, never in full), committed `.env` files and private keys,
  files that import each other in a circle, and TODO/FIXME notes. Findings in test files are kept separate and less
  serious. A finding is a place worth a look, not proof of a bug.

The same facts are given to the local AI when you press Explain, so its explanation can mention that a function has no
test or that a file has a safety note.

## Keep track of what you understand

Tick "I understand this file", bookmark files and leave notes. The overview shows a progress bar
and what to read next. In `serve` mode these are saved in `~/.code-explainer/` (never in your
repository); in a static report they are saved in the browser. `serve` binds to `127.0.0.1` only,
because the report contains your source.

## Ask a local AI (optional)

`code-explainer serve` has an **AI** tab that talks to a model running on *your* computer. Nothing is sent to
the internet, and nothing runs until you press **Start AI Explainer**.

- **Servers:** Ollama, LM Studio, or any OpenAI-compatible server (llama.cpp, Jan, BionicGPT, vLLM...).
  The tab lists the models you have already downloaded, even if the server is not running yet.
- **Start and stop for you:** if the server is off, it starts it (`ollama serve` / `lms server start`), loads the
  model, and unloads and stops *only what it started* when you press Stop, after the idle time you set, or when
  you quit. A server that was already running, or a model you had loaded yourself, is left alone.
- **Tune the answers:** presets (Precise, Balanced, Creative), temperature, top-p, top-k, min-p, answer length,
  context size, repeat penalty, seed and your own extra instructions. Controls a server cannot use
  are greyed out with the reason. Answers follow the "Explain" level in the top menu (beginner-friendly / plain English / technical).
- **Grounded in your code, with sources:** before each question the tool searches the whole project (functions,
  classes, docstrings, docs, and the call and import graphs) and gives the model only the relevant code, numbered by
  line. The call graph is handed over as exact facts ("`total` is called by `main`"), so the model does not have to
  guess. Each answer lists the code it was based on ("Based on: ..."), and any `path:line` the model cites becomes a
  link into the code. A citation that points at a file or line that does not exist is struck through, so an invented
  one is easy to spot. If nothing matches, the answer says it may be a guess. The open file can be added as focus.
- **Explain with AI, anywhere:** a "✨ Explain with AI" button on every file, function, flow step and change in a
  review. The tool builds the prompt itself from its analysis (the numbered code, the exact call-graph facts, the code
  it calls, who called it, or the patch), so the page can never steer it. The same sources and clickable, verified
  citations appear under each explanation.
- **Saved explanations:** a finished explanation is saved on this computer (never in your project, and without your
  code) and reused instantly next time, **even when the AI is stopped**. It is reused only while the code, the model,
  every setting and the reading level are unchanged, so nothing stale is ever shown; Regenerate asks again. Answers
  that were cut off, stopped or failed are never saved. See and clear them under AI > Advanced.
- **Transparent:** every answer has "Show what was sent": the exact prompt, the settings, and the search details.
- **Safe by default:** only addresses on this computer are allowed unless you switch on "Allow servers on other
  computers"; API keys are read from an environment variable you name and are never stored; model output is shown
  as text (never as HTML); the page can only choose among the servers you configured.

The search is keyword-based, not semantic: it matches the words your question shares with names, docstrings and code
(it understands `parse_http_response` and "parse the HTTP response" as the same thing, and weighs exact names and the
open file heavily), but it will miss a question phrased in completely different words from the code ("overflow" vs
"fit"). Naming the function or file in your question always works.

Models can be wrong. Treat answers as a second opinion beside the deterministic explanations, not as the truth.
A saved report (`analyze`) cannot talk to a model while you read it; use `serve` for that, or write the explanations
into the report ahead of time:

```
code-explainer analyze . --ai                         # the model chosen in the AI tab
code-explainer analyze . --last 3 --ai --ai-limit 6   # explain the changes of the last 3 commits first
code-explainer analyze . --ai --ai-model qwen2.5-coder:7b --ai-level plain
```

`--ai` starts your local model, asks it to explain the most important files (entry points first, then what they
depend on) and, with `--last`/`--base`, the changed files that affect the most other code (up to half of the
`--ai-limit`, default 10). It then unloads what it started. The answers appear on those files and changes as clearly
labelled, read-only **AI explanation** sections, with the model and level that wrote them. Progress is printed as it
goes. Press Ctrl-C at any time to keep what is finished; run the same command again to continue, because finished
answers are saved and reused (they are shared with `serve`). If the model is missing or off, the report is still
written, without AI sections, and the exit code is 1. An explanation that was cut off at the length limit is
reported as failed instead of being embedded half-written.

## Languages

| | symbols | imports | call graph |
|---|---|---|---|
| Python | yes (`ast`) | yes | yes |
| JavaScript / TypeScript | yes | yes | yes with `[treesitter]`; regex fallback without it (no call graph) |
| Others | size and rough complexity | no | no |

The Health tab's test map needs the call graph, so it covers Python and (with `[treesitter]`) JavaScript/TypeScript;
other languages are never counted as untested. The safety check reads Python from its syntax
tree and JavaScript/TypeScript by pattern, and looks for secrets in every text file.

## Honest limits

Analysis is static. Dynamic imports, reflection and framework magic can make a used file look
"possibly unused" and can hide calls, so treat those labels as places to look, not verdicts. Calls
into code outside the repository produce no link. Copy-paste detection finds identical bodies, not
renamed ones. Name-based explanations are conventions ("`get_x` gets x") and are labelled as guesses.

Flowcharts show the shape of a function's code, not what it does at run time: a function over 80 boxes is cut off
(and says so), a `switch` case that runs on into the next one is drawn as leaving the switch, and a `finally` block is
drawn once, not on every path. The charts of the least complex functions are left out if a report would get very large.

The Map is a best guess too: parts come from the folder layout (a project with a single folder is shown one box per
file, or clustered by imports when it is big), outside systems from a fixed list of well-known libraries, and arrows
from imports and followed calls only, so wiring done at run time does not show. Empty `__init__.py` files are left out.

The Health tab is an estimate too: "no test seen" means no test was seen reaching a function through the call graph, not
that coverage was measured, and a safety finding is a place to look, not proof of a bug.

The AI layer is built against the documented APIs of Ollama and LM Studio and is tested with simulated servers, so a
real server can still behave differently (LM Studio's older model list and `lms ls --json` output are partly assumed).
Please report mismatches.

## Development

```bash
pip install -e '.[dev,treesitter]'
playwright install chromium     # for the browser tests; or set CHROMIUM_PATH
pytest
```

Where things live: `code_explainer/analysis/` (per-language parsers), `flowgraph.py` / `flowgraph_js.py` /
`flowlayout.py` (function flowcharts), `architecture.py` / `graphs.py` (the Map tab), `calls.py` / `resolve.py` (call graph and
imports), `explain.py` (the plain-language layer), `diff.py` (change review), `testmap.py` and `hygiene*.py` (the
Health tab), `server.py` (serve mode), `ai/` (providers, lifecycle, retrieval, explain tasks, batch), and `web/`
(the single-file app: `app.js` with `ai.js`, `health.js`, `chart.js` and `arch.js` spliced in by `report.py`). The AI tests run against
fake Ollama and LM Studio servers (`tests/ai_fakes.py`, `tests/fake_backends.py`), so they need no model.

Work test-first: run the whole suite before you change anything (so you know the baseline is
green), write a failing test for the new behaviour or bug, make it pass, then run the whole suite
again. CI enforces a coverage floor (90%, or 80% without tree-sitter).

CI runs the tests on Python 3.10 to 3.13, with and without tree-sitter, plus the browser tests
(`tests/test_ui*.py`) and a package build. Pushing a `v*` tag publishes to PyPI (trusted publishing) and creates a GitHub release.
