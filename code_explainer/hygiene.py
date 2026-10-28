"""A safety and hygiene lens: patterns worth a second look, found without running anything.

Nothing here proves a bug. Each finding is a place where code agents (and people) commonly leave a hole, with a
plain-English reason and a suggested fix. Secrets are never repeated in full: the report only shows a masked form.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict

from . import hygiene_py
from .analysis import CODE_LANGS
from .model import FileInfo

SEVERITIES = ("high", "medium", "low", "info")
MAX_FINDINGS = 300
PER_FILE_RULE_CAP = 20
MAX_CYCLES = 10
MAX_LINE_CHARS = 2000          # longer lines are minified or generated; not worth scanning
SNIPPET_CHARS = 160
_TEXT_ONLY = {"Markdown", "Other", "JSON", "YAML", "TOML", "HTML", "CSS", "SQL", "Shell"}   # no assignment-style secret scan in prose

# id -> (title, why, fix)
RULES = {
    "py-eval": ("Runs text as code (eval)", "eval() turns a piece of text into a command and runs it. If anyone else can influence that text, they can make the program do anything.",
                "Use ast.literal_eval for data, or a lookup table of allowed actions."),
    "py-exec": ("Runs text as code (exec)", "exec() runs a whole piece of text as a program. If that text can come from outside, a stranger can take over.",
                "Avoid it. Call real functions instead of building code as text."),
    "js-eval": ("Runs text as code (eval / new Function)", "eval() and new Function() run text as a program. If that text can come from a user or a website, it can be used to attack the page.",
                "Parse data with JSON.parse and call real functions instead."),
    "shell-injection": ("Builds a shell command", "The command goes through a shell, which treats special characters (; & |) as new commands. If any part comes from outside, someone can add their own commands.",
                        "Pass the program and its arguments as a list (no shell), e.g. subprocess.run([\"ls\", path])."),
    "py-pickle": ("Loads data that can run code (pickle)", "Opening a pickle file can run code hidden inside it. It is only safe if you made the file yourself.",
                  "Use JSON for data you receive. Only unpickle files you trust completely."),
    "py-yaml-load": ("Unsafe YAML loading", "yaml.load without a safe loader can build any Python object from the file, which can run code.",
                     "Use yaml.safe_load."),
    "sql-string": ("Database query built from text", "The query is glued together from pieces. If a piece comes from a user, they can rewrite the query (SQL injection) and read or erase data.",
                   "Use placeholders: cursor.execute(\"... WHERE id = %s\", (value,))."),
    "tls-verify-off": ("Secure-connection check turned off", "verify=False means the program no longer checks who it is talking to, so someone in the middle can pretend to be the server.",
                       "Remove verify=False, or point it at the right certificate file."),
    "weak-hash": ("Weak fingerprint (md5 / sha1)", "These are old and can be faked. Fine for a quick checksum, but not for passwords or anything that must be trusted.",
                  "Use sha256 for integrity, and a password hasher (argon2, bcrypt) for passwords."),
    "bare-except": ("Catches every error without looking", "A bare except hides all errors, even ones that should stop the program, so problems go unnoticed.",
                    "Catch the specific error you expect, and log or re-raise anything else."),
    "swallowed-error": ("Errors are silently ignored", "The code catches every error and does nothing, so failures vanish without a trace.",
                        "At least log the error, or catch only the one you expect."),
    "py-mktemp": ("Predictable temporary file name", "mktemp gives a name but does not create the file, so someone else can grab that name first.",
                  "Use tempfile.mkstemp or NamedTemporaryFile."),
    "debug-on": ("Debug mode switched on", "Debug mode shows detailed errors and sometimes lets visitors run code. It should never be on in a real deployment.",
                 "Turn it off, or read it from a setting that is off by default."),
    "js-inner-html": ("Puts text straight into the page as HTML", "If that text contains a <script> or a trick, it runs in the visitor's browser (cross-site scripting).",
                      "Use textContent, or clean the HTML first with a sanitizer."),
    "secret-token": ("A real-looking secret key is in the code", "Keys in code are copied everywhere the code goes (git history, forks, logs) and can be found by anyone who sees it.",
                     "Treat it as leaked: revoke it, create a new one, and load it from an environment variable or secret store."),
    "secret-private-key": ("A private key is in the code", "A private key proves who you are. Anyone who gets this file can pretend to be you.",
                           "Remove it, create a new key pair, and keep keys out of the repository."),
    "secret-assignment": ("A password or key is written in the code", "Passwords in code are copied everywhere the code goes and are easy to find.",
                          "Read it from an environment variable or a secret store instead."),
    "secret-file": ("An environment file with values is in the project", "A .env file usually holds real passwords and keys. Committing it shares them with everyone who can see the project.",
                    "Remove it from git, add it to .gitignore, and commit a .env.example with empty values."),
    "import-cycle": ("Files that import each other in a circle", "When A needs B and B needs A, neither can be understood or changed alone, and imports can break depending on the order.",
                     "Move the shared part into a third file that both import."),
}

_JS_COMMENT = re.compile(r"^\s*(//|/\*|\*)")
_JS_EVAL = re.compile(r"(?<![\w.$])eval\s*\(|\bnew\s+Function\s*\(")
_JS_HTML_ASSIGN = re.compile(r"\.(?:inner|outer)HTML\s*\+?=(?!=)\s*(.*)")
_JS_HTML_OTHER = re.compile(r"dangerouslySetInnerHTML|\bdocument\.write(?:ln)?\s*\(|\.insertAdjacentHTML\s*\(")
_JS_EXEC_SYNC = re.compile(r"(?<![\w$])(?:[\w$]+\.)?execSync\(\s*(?:`[^`]*\$\{|(['\"])[^'\"]*\1\s*\+)")
_JS_EXEC = re.compile(r"(?<![\w$])(?:[\w$]+\.)?exec\(\s*(?:`[^`]*\$\{|(['\"])[^'\"]*\1\s*\+)")
_CONST_STRING = re.compile(r"^(['\"])[^'\"`]*\1\s*;?\s*(//.*)?$")

_TOKENS = [re.compile(p) for p in (
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    r"\bxox[baprs]-[0-9A-Za-z-]{10,}",
    r"\bsk_live_[0-9a-zA-Z]{24,}",
    r"\bAIza[0-9A-Za-z_\-]{35}\b",
)]
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")
_ASSIGN = re.compile(r"""(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|api_?key|access_?key|token))["']?\s*[:=]\s*(["'])([^"'\s]{8,})\2""")
_PLACEHOLDER = ("example", "changeme", "change_me", "your", "xxx", "<", "${", "{{", "placeholder", "dummy", "sample", "fake", "redacted", "todo")
_LABEL = re.compile(r"^[a-z]+([_.\-][a-z]+)+$")
_ENV_FILE = re.compile(r"^\.env(\..+)?$")
_ENV_EXAMPLE = re.compile(r"\.(example|sample|template|dist|defaults?)$")
_TODO = re.compile(r"(?:#|//|/\*|\*|--)\s*.*\b(TODO|FIXME|HACK|XXX)\b")


def _mask(value: str) -> str:
    return f"{value[:3]}…({len(value)} chars)"


def _looks_like_placeholder(value: str) -> bool:
    low = value.lower()
    return (any(p in low for p in _PLACEHOLDER) or len(set(value)) <= 2 or bool(_LABEL.match(value)) or "://" in value
            or value.startswith(("/", "./", "$", "%")))


def _snippet(line: str, secrets: list[str] = ()) -> str:
    text = line.strip()
    for s in secrets:
        text = text.replace(s, _mask(s))
    return text[:SNIPPET_CHARS]


def _secret_hits(info: FileInfo, lines: list[str]):
    """(rule, line number, snippet) for secrets in any text file."""
    base = posixpath.basename(info.path)
    if _ENV_FILE.match(base) and not _ENV_EXAMPLE.search(base):
        first = next((i for i, l in enumerate(lines, 1) if re.match(r"\s*(export\s+)?[A-Za-z_][\w]*\s*=\s*\S", l)), 0)
        if first:
            yield "secret-file", first, _snippet(re.sub(r"=.*", "=…", lines[first - 1]))
    for i, line in enumerate(lines, 1):
        if len(line) > MAX_LINE_CHARS:
            continue
        if _PRIVATE_KEY.search(line):
            yield "secret-private-key", i, _snippet(line)
            continue
        found = [m.group(0) for p in _TOKENS for m in p.finditer(line)]
        if found:
            yield "secret-token", i, _snippet(line, found)
            continue
        if info.lang in _TEXT_ONLY:
            continue
        for m in _ASSIGN.finditer(line):
            value = m.group(3)
            if not _looks_like_placeholder(value):
                yield "secret-assignment", i, _snippet(line, [value])
                break


def _js_hits(lines: list[str], text: str):
    shell_ok = "child_process" in text or "shelljs" in text
    for i, line in enumerate(lines, 1):
        if len(line) > MAX_LINE_CHARS or _JS_COMMENT.match(line):
            continue
        if _JS_EVAL.search(line):
            yield "js-eval", "high", i
        m = _JS_HTML_ASSIGN.search(line)
        if (m and not _CONST_STRING.match(m.group(1).strip())) or _JS_HTML_OTHER.search(line):
            yield "js-inner-html", "medium", i
        if _JS_EXEC_SYNC.search(line) or (shell_ok and _JS_EXEC.search(line)):
            yield "shell-injection", "high", i


def _cycles(files: dict[str, FileInfo]) -> list[list[str]]:
    """Groups of non-test files that import each other in a circle (strongly connected components)."""
    nodes = sorted(p for p, f in files.items() if not f.is_test and f.lang in CODE_LANGS)
    members = set(nodes)
    adj = {p: [t for t in files[p].imports if t in members and t != p] for p in nodes}
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = [0]
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(adj[root]))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(adj[nxt])))
                    advanced = True
                    break
                if nxt in on:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return sorted(out, key=lambda c: (-len(c), c))


def scan(files: dict[str, FileInfo]) -> dict:
    raw = []          # (rule, severity, path, line, snippet, is_test)
    todos: dict[str, int] = {}
    for path in sorted(files):
        info = files[path]
        text = info.source
        if not text:
            continue
        lines = text.splitlines()
        test = info.is_test
        if info.lang == "Python":
            for rule, sev, line in hygiene_py.scan(text):
                raw.append((rule, sev, path, line, _snippet(lines[line - 1]) if 0 < line <= len(lines) else "", test))
        elif info.lang in ("JavaScript", "TypeScript"):
            for rule, sev, line in _js_hits(lines, text):
                raw.append((rule, sev, path, line, _snippet(lines[line - 1]), test))
        for rule, line, snippet in _secret_hits(info, lines):
            raw.append((rule, "high" if rule != "secret-assignment" else "medium", path, line, snippet, test))
        if info.lang in CODE_LANGS:
            n = sum(1 for l in lines if len(l) <= MAX_LINE_CHARS and _TODO.search(l))
            if n:
                todos[path] = n
    cycles = []
    for comp in _cycles(files)[:MAX_CYCLES]:
        benign = any(posixpath.basename(p) == "__init__.py" for p in comp)
        cycles.append({"files": comp[:12], "size": len(comp), "benign": benign})
        raw.append(("import-cycle", "info" if benign else "medium", comp[0], 1,
                    _snippet("circle: " + " ↔ ".join(posixpath.basename(p) for p in comp[:6]) + (" …" if len(comp) > 6 else "")), False))

    findings, per = [], defaultdict(int)
    for rule, sev, path, line, code, test in raw:
        if test:
            sev = "low" if rule.startswith("secret-") else "info"
        title, why, fix = RULES[rule]
        findings.append({"id": rule, "sev": sev, "path": path, "line": line, "title": title, "why": why, "fix": fix,
                         "code": code, "test": test})
    findings.sort(key=lambda f: (SEVERITIES.index(f["sev"]), f["path"], f["line"], f["id"]))
    counts = {s: sum(1 for f in findings if f["sev"] == s) for s in SEVERITIES}
    kept = []
    for f in findings:
        key = (f["path"], f["id"])
        per[key] += 1
        if per[key] <= PER_FILE_RULE_CAP and len(kept) < MAX_FINDINGS:
            kept.append(f)
    top = sorted(todos.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    return {
        "findings": kept,
        "summary": {**counts, "total": len(findings), "truncated": len(kept) < len(findings)},
        "cycles": cycles,
        "todos": {"total": sum(todos.values()), "files": [{"path": p, "count": n} for p, n in top]},
    }
