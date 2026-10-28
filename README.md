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

The core needs only the Python standard library (plus `tomli` on Python 3.10, which has no built-in TOML parser), and the report works offline.

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

## Ask a local AI (optional)

`code-explainer serve` has an **AI** tab that talks to a model running on *your* computer. Nothing is sent to
the internet, and nothing runs until you press **Start AI Explainer**.

- **Servers:** Ollama, LM Studio, or any OpenAI-compatible server (llama.cpp, Jan, BionicGPT, vLLM...).
  The tab lists the models you have already downloaded, even if the server is not running yet.
- **Start and stop for you:** if the server is off, it starts it (`ollama serve` / `lms server start`), loads the
  model, and unloads and stops *only what it started* when you press Stop, after the idle time you set, or when
  you quit. A server that was already running, or a model you had loaded yourself, is left alone.
- **Tune the answers:** presets (Precise, Balanced, Creative), temperature, top-p, top-k, min-p, answer length,
  context size, repeat penalty, seed, answer length and your own extra instructions. Controls a server cannot use
  are greyed out with the reason. Answers follow the "Explain" level in the top menu (like I'm 8 / plain / technical).
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
