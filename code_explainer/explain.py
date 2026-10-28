"""Plain-language explanations generated from the code's structure.

No language model is involved. Every sentence is assembled from facts the
analysis already knows (names, docstrings, who calls whom, sizes, complexity)
plus a small table of naming conventions. Anything that is a guess from a name
alone is phrased as a guess.

Two reading levels are produced for every piece of text:

* "kid":   short sentences and one consistent metaphor, the project is a big house.
           Folders are floors/hallways, files are rooms, functions are helpers
           that each do one little job, classes are recipes, importing is borrowing
           tools from another room, calling is asking a helper for a favour.
* "plain": the same facts in everyday English with no metaphor.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict

from .model import FileInfo, Symbol

Text = dict  # {"kid": str, "plain": str}

# ---------------------------------------------------------------- vocabulary

_VERBS = {
    "get": "gets", "fetch": "fetches", "load": "loads", "read": "reads", "save": "saves",
    "write": "writes", "store": "stores", "set": "sets", "add": "adds", "append": "adds",
    "remove": "removes", "delete": "deletes", "update": "updates", "create": "creates",
    "make": "makes", "build": "builds", "init": "sets up", "setup": "sets up",
    "parse": "reads and makes sense of", "render": "draws", "draw": "draws", "show": "shows",
    "print": "prints", "format": "formats", "convert": "converts", "find": "looks for",
    "search": "searches for", "lookup": "looks up", "run": "runs", "start": "starts",
    "execute": "runs", "launch": "launches", "handle": "takes care of", "process": "works on",
    "send": "sends", "receive": "receives", "open": "opens", "close": "closes",
    "clone": "copies", "copy": "copies", "sort": "sorts", "filter": "picks out",
    "merge": "combines", "split": "splits up", "join": "joins together", "count": "counts",
    "compute": "works out", "calculate": "works out", "resolve": "figures out",
    "analyze": "examines", "analyse": "examines", "validate": "checks", "check": "checks",
    "verify": "double-checks", "register": "signs up", "connect": "connects to",
    "clean": "cleans up", "clear": "clears", "reset": "resets", "stop": "stops",
    "wait": "waits for", "log": "writes down", "list": "lists", "walk": "walks through",
    "collect": "gathers", "mark": "marks", "apply": "applies", "extract": "pulls out",
    "generate": "generates", "compile": "compiles", "install": "installs",
    "download": "downloads", "upload": "uploads", "emit": "sends out", "dispatch": "sends off",
    "notify": "lets people know about", "schedule": "schedules", "retry": "tries again at",
    "wrap": "wraps", "encode": "encodes", "decode": "decodes", "serialize": "packs up",
    "deserialize": "unpacks", "dump": "writes out", "normalize": "tidies up",
    "sanitize": "cleans", "escape": "protects", "scan": "scans", "visit": "visits",
    "traverse": "walks through", "evaluate": "works out", "select": "picks",
    "toggle": "flips", "enable": "turns on", "disable": "turns off", "bind": "attaches",
    "use": "uses", "test": "tests", "assert": "insists on", "raise": "raises",
    "move": "moves", "rename": "renames", "replace": "swaps", "insert": "puts in",
    "push": "pushes", "pop": "takes out", "pull": "pulls", "sync": "syncs", "fix": "fixes",
    "patch": "patches", "plan": "plans", "pick": "picks", "choose": "chooses",
    "ensure": "makes sure of", "prepare": "gets ready", "finish": "finishes",
    "unwrap": "unwraps", "inspect": "looks closely at", "explain": "explains",
    "describe": "describes", "summarize": "sums up", "summarise": "sums up",
    "track": "keeps track of", "watch": "keeps an eye on", "listen": "listens for",
    "trigger": "triggers", "invoke": "calls", "call": "calls", "ask": "asks for",
    "report": "reports",
}
_PREDICATES = {"is": "is", "has": "has", "can": "can", "should": "should", "needs": "needs", "did": "did", "was": "was"}
_SOLO = {
    "main": ("the main starter: it kicks everything off", "Main starting point; kicks everything off."),
    "run": ("the one that makes things go", "Runs the main work."),
    "start": ("the one that gets things going", "Starts things up."),
    "cli": ("the helper that listens to what you type in the terminal", "Command-line entry point."),
    "setup": ("the one that gets everything ready first", "Prepares things before use."),
    "init": ("the one that gets everything ready first", "Initial set-up."),
    "handler": ("the one that answers when something happens", "Responds to an event or request."),
    "default": ("the main thing this file offers", "The main thing this file exports."),
}
_DUNDERS = {
    "__init__": ("sets up a brand-new {cls}", "Creates and initialises a new {cls}."),
    "__post_init__": ("finishes setting up a new {cls}", "Finishes initialising a new {cls}."),
    "__repr__": ("turns it into words so people can read it", "Text form used when debugging."),
    "__str__": ("turns it into words so people can read it", "Text form shown to people."),
    "__eq__": ("decides whether two of them are the same", "Equality check."),
    "__hash__": ("gives it an ID number for quick lookup", "Hash value."),
    "__len__": ("counts what is inside", "How many items it holds."),
    "__iter__": ("lets you walk through what is inside, one at a time", "Iteration support."),
    "__getitem__": ("lets you grab one item from inside", "Item access with []."),
    "__setitem__": ("lets you put an item inside", "Item assignment with []."),
    "__contains__": ("answers whether something is inside", "Membership test (`in`)."),
    "__enter__": ("gets things ready when you start using it", "Context manager entry."),
    "__exit__": ("tidies up when you are finished with it", "Context manager exit."),
    "__call__": ("lets you use it like a helper", "Makes instances callable."),
    "__lt__": ("decides which one comes first", "Ordering comparison."),
    "constructor": ("sets up a brand-new {cls}", "Creates and initialises a new {cls}."),
    "render": ("draws it on the screen", "Renders output."),
}

# filename-stem (or substring) -> (kid, plain). Always phrased as a guess.
_FILE_ROLES: list[tuple[tuple[str, ...], str, str]] = [
    (("test", "spec", "conftest"), "it is where inspectors check that the other rooms really work",
     "it contains automated tests"),
    (("config", "settings", "constants", "env", "defaults"), "it holds the knobs and switches that control how things behave",
     "it holds settings or constants"),
    (("util", "utils", "helpers", "helper", "common", "tools", "misc", "lib"), "it is a toolbox of small helpers that many rooms borrow",
     "it is a collection of small shared helpers"),
    (("model", "models", "schema", "schemas", "entity", "entities", "types", "dto"), "it describes the shapes of the things the program talks about, like labels on boxes",
     "it defines data shapes or models"),
    (("view", "views", "route", "routes", "router", "api", "controller", "controllers", "endpoint", "endpoints", "urls", "handlers"),
     "it answers requests from the outside world, like someone answering the doorbell", "it handles incoming requests"),
    (("service", "services", "manager", "engine"), "it does a bigger job by getting other helpers to work together",
     "it coordinates other components to do a larger job"),
    (("db", "database", "repository", "repo", "storage", "dao", "migrations", "migration", "store"),
     "it talks to the place where information is kept", "it deals with storing and loading data"),
    (("main", "app", "cli", "server", "manage", "index", "__main__", "wsgi", "asgi"), "it is a front door, a place where the program can start",
     "it is a place where the program starts"),
    (("__init__",), "it tells the program that this hallway is a group, and often points at the important things inside",
     "it marks the folder as a package and often re-exports its main parts"),
    (("auth", "login", "permissions", "security"), "it decides who is allowed to come in and what they may do",
     "it deals with authentication or permissions"),
    (("log", "logging", "logger"), "it writes down what is happening, like a diary", "it deals with logging"),
    (("parser", "lexer", "tokenizer"), "it reads text and works out what it means", "it parses text"),
    (("render", "template", "templates"), "it draws things so that people can see them", "it renders output"),
    (("cache",), "it remembers answers so the program doesn't have to work them out twice", "it caches results"),
    (("middleware",), "it checks and adjusts every request on its way in or out", "it processes requests in a pipeline"),
]
_NONCODE = {
    "Markdown": ("written notes for people to read", "documentation or notes"),
    "JSON": ("information written in a tidy list the computer can read", "structured data or configuration (JSON)"),
    "YAML": ("settings or information written as a simple list", "configuration or data (YAML)"),
    "TOML": ("settings written as a simple list", "configuration (TOML)"),
    "HTML": ("the layout of a web page, like a blueprint for how it looks", "a web page or template"),
    "CSS": ("the paint and decorations for a web page", "styling rules for a web page"),
    "SQL": ("questions and instructions for a database", "database queries or schema"),
    "Shell": ("a to-do list of commands for the computer to run", "a shell script"),
}
_SPECIAL_FILES = {
    "readme": ("the welcome note that tells visitors what this project is", "the project's readme"),
    "license": ("the legal note about who may use this and how", "the license"),
    "pyproject": ("the instruction sheet for building and running this project", "Python project metadata and build settings"),
    "setup": ("the instruction sheet for building and running this project", "package build script"),
    "package": ("the instruction sheet for building and running this project", "JavaScript project metadata and scripts"),
    "makefile": ("a list of shortcuts for common jobs", "task shortcuts"),
    "dockerfile": ("a recipe for packing the project into a box that runs anywhere", "container build recipe"),
    "requirements": ("the shopping list of outside tools the project needs", "dependency list"),
    ".gitignore": ("a list of things that should not be saved or shared", "files git should ignore"),
    "changelog": ("a diary of what changed over time", "release history"),
    "contributing": ("rules for people who want to help", "contribution guidelines"),
}
_FOLDER_HINTS = {
    "tests": ("🔍", "the inspectors' floor: checks that everything works", "automated tests"),
    "test": ("🔍", "the inspectors' floor: checks that everything works", "automated tests"),
    "__tests__": ("🔍", "the inspectors' floor: checks that everything works", "automated tests"),
    "spec": ("🔍", "the inspectors' floor: checks that everything works", "automated tests"),
    "src": ("🏠", "the main floor, where the real work lives", "the main source code"),
    "lib": ("🧰", "a floor of shared tools", "library code"),
    "app": ("🏠", "the main floor, where the real work lives", "the application code"),
    "docs": ("📚", "the library: guides written for people", "documentation"),
    "doc": ("📚", "the library: guides written for people", "documentation"),
    "scripts": ("📜", "a hallway of little to-do lists for the computer", "helper scripts"),
    "bin": ("🚪", "a hallway of things you can start directly", "executable entry scripts"),
    "static": ("🎨", "the decoration room: pictures, styles and other things shown as they are", "static assets"),
    "assets": ("🎨", "the decoration room: pictures, styles and other things shown as they are", "static assets"),
    "public": ("🎨", "the decoration room: things shown to visitors as they are", "publicly served files"),
    "templates": ("🖼️", "a floor of page blueprints", "page templates"),
    "components": ("🧩", "a floor of building blocks for screens", "UI components"),
    "pages": ("📄", "a floor with one room per web page", "page-level UI code"),
    "api": ("📞", "the telephone room: where outside programs call in", "the API layer"),
    "models": ("📦", "a floor that describes the shapes of things", "data models"),
    "utils": ("🧰", "a toolbox floor of small shared helpers", "shared utilities"),
    "helpers": ("🧰", "a toolbox floor of small shared helpers", "shared utilities"),
    "config": ("🎛️", "the control panel: knobs and switches", "configuration"),
    "migrations": ("🚚", "a floor that records how the storage room changed over time", "database migrations"),
    "examples": ("💡", "a floor of sample projects that show how to use this", "usage examples"),
    "web": ("🌐", "the part that people see in their browser", "web front-end code"),
    "fixtures": ("🧪", "pretend data used by the inspectors", "test data"),
    ".github": ("🤖", "the robot helpers' floor: automatic checks and releases", "CI/CD configuration"),
    "workflows": ("🤖", "the robot helpers' floor: automatic checks and releases", "CI workflows"),
}

GLOSSARY = [
    ("Repository (repo)", "The whole big house: every room and floor of one project, plus a diary of how it changed.",
     "A project's folder of code, along with its history."),
    ("Folder", "A hallway or floor that groups rooms that belong together.", "A directory that groups related files."),
    ("File", "One room. It holds a piece of the program or some notes.", "A single document containing code or text."),
    ("Function", "A helper who does one little job when you ask. You give it things, it gives you an answer.",
     "A named block of code that does one job and can be reused."),
    ("Class", "A recipe for making things of one kind, like a cookie cutter.", "A template for creating objects that bundle data and behaviour."),
    ("Method", "A helper that belongs to a recipe. Every thing made from the recipe can ask it for help.",
     "A function that belongs to a class."),
    ("Import", "Borrowing tools from another room so you do not have to build them again.",
     "Making code from one file available in another."),
    ("Call", "Asking a helper to do its job.", "Running a function."),
    ("Entry point", "The front door: where the program starts when you run it.", "The place where execution begins."),
    ("Test", "An inspector that checks something still works after changes.", "Code that automatically checks other code."),
    ("Dependency", "A room (or tool) another room needs in order to work.", "Something a piece of code relies on."),
    ("Branch / choice", "A fork in the road: 'if this, do that, otherwise do something else'.",
     "A point where code takes different paths (if/else, loops, cases)."),
    ("Complexity", "How many forks and twists a helper has. More twists means it is harder to understand.",
     "A count of decision points in a piece of code."),
    ("Possibly unused", "A room that nobody seems to visit. It could be old and safe to tidy, or visited through a secret door we could not see.",
     "Code nothing seems to import or call. Check before deleting: dynamic use can hide it."),
    ("Docstring / comment", "A sign on the door written by the builder to tell you what is inside.",
     "Text written by developers to explain code."),
    ("Commit", "A saved snapshot of the house at one moment, with a note about what changed.", "A saved set of changes in version control."),
    ("Churn (most changed)", "Rooms that people keep fixing. They are often the busy or fragile ones.", "How often a file changes in history."),
    ("Diff", "A list of exactly what is different between two snapshots: what was added and what was taken away.",
     "The differences between two versions of code."),
    ("Blast radius", "If you break this room, how many other rooms feel it. A wide radius means be extra careful.",
     "The set of code that could be affected by a change."),
    ("Guess (inferred link)", "We are not sure about this one. It is a good guess based on the name only.",
     "A relationship found by name matching rather than by following imports."),
    ("Variable", "A labelled box that holds something for a while.", "A named place that stores a value."),
    ("Bug", "A mistake in the instructions that makes the program do something unexpected.", "A defect in code."),
    ("Library / framework", "A big box of tools made by someone else that this project borrows.", "Reusable code from outside the project."),
    ("API", "A list of what you may ask a program to do, like a menu in a restaurant.", "A defined way for programs to talk to each other."),
    ("Refactor", "Tidying and reorganising rooms without changing what the house does.", "Restructuring code without changing behaviour."),
    ("Duplicate code", "The same helper written twice in different rooms. Fix one and you must remember the other.",
     "Copies of the same logic in several places."),
]


# ---------------------------------------------------------------- helpers


def _words(name: str) -> list[str]:
    name = name.strip("_")
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    return re.sub(r"[\W_]+", " ", name).lower().split()


def _readable(name: str) -> str:
    return " ".join(_words(name)) or name


def _list(names: list[str], limit: int = 4, quote: bool = True) -> str:
    q = (lambda n: f"`{n}`") if quote else (lambda n: n)
    shown = [q(n) for n in names[:limit]]
    extra = len(names) - limit
    if extra > 0:
        shown.append(f"{extra} more")
    if len(shown) <= 1:
        return "".join(shown)
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _size_word(loc: int) -> str:
    return "tiny" if loc < 15 else "small" if loc < 60 else "medium-sized" if loc < 200 else "big" if loc < 600 else "huge"


def _ensure_period(s: str) -> str:
    s = s.strip()
    return s if not s or s[-1] in ".!?" else s + "."


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _name_meaning(sym: Symbol) -> tuple[str, str]:
    """What the name alone suggests, as (kid, plain) sentences."""
    n = sym.name
    cls = (sym.parent or "").split(".")[-1] or "thing"
    if n in _DUNDERS:
        k, p = _DUNDERS[n]
        return f"This helper {k.format(cls=cls)}.", _ensure_period(p.format(cls=cls))
    if n in _SOLO:
        k, p = _SOLO[n]
        return f"This is {k}.", p
    ws = _words(n)
    if not ws:
        return f"This helper is called `{n}`.", f"Named `{n}`."
    first, rest = ws[0], " ".join(ws[1:])
    if first in _PREDICATES and rest:
        return (f"This helper answers a yes-or-no question: {first} it {rest}?", f"Checks whether it {first} {rest}.")
    if first in _VERBS:
        verb = _VERBS[first]
        obj = rest or "things"
        return f"This helper {verb} {obj}.", _cap(f"{verb} {obj}.")
    if first in ("to", "as") and rest:
        return f"This helper turns things into {rest}.", f"Converts into {rest}."
    if first == "from" and rest:
        return f"This helper builds something from {rest}.", f"Builds from {rest}."
    if first in ("on", "when") and rest:
        return f"This helper wakes up when {rest} happens.", f"Runs when {rest} happens."
    return (f"This helper is in charge of “{_readable(n)}”.", f"Concerned with {_readable(n)}.")


def _shape(cx: int, lines: int) -> tuple[str, str]:
    size_k = f"It is about {_plural(lines, 'line')} long."
    if cx <= 1:
        return ("It does its job in a straight line, with no forks in the road. " + size_k,
                f"Straight-line code with no branching. {size_k}")
    if cx <= 3:
        return ("It has a couple of choices to make, like “if this, do that”. " + size_k,
                f"A few branches (complexity {cx}). {size_k}")
    if cx <= 7:
        return ("It has quite a few choices to make, so take your time reading it. " + size_k,
                f"Several branches (complexity {cx}); read carefully. {size_k}")
    return ("It is full of forks and twists. This is one of the tricky helpers! " + size_k,
            f"Highly branched (complexity {cx}); a likely place for bugs. {size_k}")


# ---------------------------------------------------------------- symbols


def _callers(files: dict[str, FileInfo]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for path, info in files.items():
        for sym in info.symbols:
            me = f"{path}::{sym.qual}"
            for c in sym.callees:
                if c["conf"] == "certain" and c["id"] != me and me not in out[c["id"]]:
                    out[c["id"]].append(me)
    return out


def _callee_names(sym: Symbol, names: dict[str, str]) -> list[str]:
    seen, out = set(), []
    for c in sym.callees:
        if c["conf"] == "certain" and c["id"] in names and c["id"] not in seen:
            seen.add(c["id"])
            out.append(names[c["id"]])
    return out


def explain_symbol(path: str, sym: Symbol, info: FileInfo, names: dict[str, str], callers: dict[str, list[str]]) -> Text:
    me = f"{path}::{sym.qual}"
    lines = max(1, sym.end_line - sym.line + 1)
    if sym.kind == "module":
        return {
            "kid": "This is the code that runs the moment the file wakes up, outside of any helper. It is where the file starts its day.",
            "plain": "Top-level code that runs when the file is loaded or run directly.",
        }
    if sym.kind == "class":
        methods = [s.name for s in info.symbols if s.kind == "method" and s.parent == sym.qual and not s.name.startswith("_")]
        kid = [f"This is a recipe (a class) for making `{_readable(sym.name)}` things."]
        plain = [f"Class `{sym.name}`: a template for `{_readable(sym.name)}` objects."]
        if sym.doc:
            kid.append(f"The builder's sign says: “{_ensure_period(sym.doc)}”")
            plain.append(_ensure_period(sym.doc))
        if methods:
            kid.append(f"Things made from it can: {_list(methods, 5, False)}.")
            plain.append(f"Its methods include {_list(methods, 5)}.")
        else:
            kid.append("It does not list any special tricks of its own.")
            plain.append("It defines no public methods.")
        return {"kid": " ".join(kid), "plain": " ".join(plain)}
    k_name, p_name = _name_meaning(sym)
    k_shape, p_shape = _shape(sym.complexity, lines)
    kid, plain = [], []
    if sym.doc:
        kid.append(f"The sign on its door says: “{_ensure_period(sym.doc)}”")
        plain.append(_ensure_period(sym.doc))
    else:
        kid.append(k_name.rstrip(".") + " (that is my guess from its name).")
        plain.append(p_name.rstrip(".") + " (inferred from its name).")
    kid.append(k_shape)
    plain.append(p_shape)
    callees = _callee_names(sym, names)
    if callees:
        kid.append(f"To do its job it asks other helpers for help: {_list(callees, 4, False)}.")
        plain.append(f"It calls {_list(callees, 4)}.")
    else:
        kid.append("It does not ask any other helper in this project for help.")
        plain.append("It calls nothing else in this project.")
    n_callers = len(callers.get(me, []))
    if n_callers:
        kid.append(f"{_plural(n_callers, 'other helper')} {'asks' if n_callers == 1 else 'ask'} it for favours.")
        plain.append(f"Called from {_plural(n_callers, 'place')}.")
    elif not sym.entry:
        kid.append("No helper in the project asks for this one directly. It may be called from outside, or forgotten.")
        plain.append("Nothing in the project calls it directly (it may be used externally or be dead code).")
    if sym.entry:
        kid.append(f"The outside world can start here: {sym.entry}.")
        plain.append(f"Entry point: {sym.entry}.")
    return {"kid": " ".join(x for x in kid if x), "plain": " ".join(x for x in plain if x)}


# ---------------------------------------------------------------- files & folders


def _file_role(info: FileInfo) -> tuple[str, str] | None:
    base = posixpath.basename(info.path)
    stem = posixpath.splitext(base)[0].lower()
    for key, val in _SPECIAL_FILES.items():
        if stem == key or base.lower() == key:
            return val
    if info.is_test:
        return ("it is where inspectors check that the other rooms really work", "it contains automated tests")
    if info.lang in _NONCODE and not info.symbols:
        return _NONCODE[info.lang]
    parts = set(re.split(r"[^a-z0-9_]+", stem)) | {stem}
    for keys, kid, plain in _FILE_ROLES:
        if parts & set(keys):
            return kid, plain
    for keys, kid, plain in _FILE_ROLES:
        if any(k in stem for k in keys if len(k) > 4):
            return kid, plain
    return None


def explain_file(info: FileInfo) -> Text:
    name = posixpath.basename(info.path)
    syms = [s for s in info.symbols if s.kind != "module"]
    funcs = [s.qual for s in syms if s.kind in ("function", "method") and not s.name.startswith("__")]
    classes = [s.name for s in syms if s.kind == "class"]
    role = _file_role(info)
    kid = [f"`{name}` is a {_size_word(info.loc)} room ({_plural(info.loc, 'line')})."]
    plain = [f"`{name}`: {info.lang}, {_plural(info.loc, 'line')}."]
    if role:
        kid.append(f"From its name I would guess {role[0]}.")
        plain.append(f"Judging by its name, {role[1]}.")
    if info.doc:
        kid.append(f"The sign on its door says: “{_ensure_period(info.doc)}”")
        plain.append(f"Its docstring: “{_ensure_period(info.doc)}”")
    if classes or funcs:
        bits_k, bits_p = [], []
        if classes:
            bits_k.append(f"{_plural(len(classes), 'recipe')} for making things ({_list(classes, 3, False)})")
            bits_p.append(f"{_plural(len(classes), 'class', 'classes')} ({_list(classes, 3)})")
        if funcs:
            bits_k.append(f"{_plural(len(funcs), 'helper')} ({_list(funcs, 4, False)})")
            bits_p.append(f"{_plural(len(funcs), 'function')} ({_list(funcs, 4)})")
        kid.append("Inside live " + " and ".join(bits_k) + ".")
        plain.append("It defines " + " and ".join(bits_p) + ".")
    elif info.analyzed:
        kid.append("It does not define any helpers or recipes of its own.")
        plain.append("It defines no functions or classes.")
    if info.entry_reasons:
        kid.append("This is a front door: the program can start here.")
        plain.append("Entry point: " + info.entry_reasons[0] + ".")
    if info.imported_by:
        users = [posixpath.basename(p) for p in info.imported_by]
        kid.append(f"These rooms come here to borrow things: {_list(users, 3, False)}.")
        plain.append(f"Imported by {_list(users, 3)}.")
    elif info.possibly_unused:
        kid.append("Nobody seems to visit this room. It might be old and forgotten, or visited by a secret door we cannot see.")
        plain.append("Nothing imports this file. It may be unused, or loaded dynamically.")
    if info.imports:
        deps = [posixpath.basename(p) for p in info.imports]
        kid.append(f"It borrows tools from: {_list(deps, 4, False)}.")
        plain.append(f"It imports {_list(deps, 4)}.")
    elif info.analyzed:
        kid.append("It does not borrow from any other room in this project.")
        plain.append("It imports nothing from this project.")
    if info.churn >= 8:
        kid.append(f"People have changed this room a lot ({info.churn} times), so it is a busy one.")
        plain.append(f"Changed in {info.churn} commits: a frequently edited file.")
    return {"kid": " ".join(kid), "plain": " ".join(plain)}


def explain_folders(files: dict[str, FileInfo]) -> dict[str, dict]:
    dirs: dict[str, list[FileInfo]] = defaultdict(list)
    for info in files.values():
        parts = info.path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs["/".join(parts[:i])].append(info)
    out = {}
    for d, members in dirs.items():
        name = d.split("/")[-1]
        langs: dict[str, int] = defaultdict(int)
        for m in members:
            if m.lang in ("Python", "JavaScript", "TypeScript", "Go", "Rust", "Java", "Ruby", "C", "C++", "C#"):
                langs[m.lang] += m.loc
        main_lang = max(langs, key=langs.get) if langs else ""
        hint = _FOLDER_HINTS.get(name.lower())
        emoji = hint[0] if hint else "📁"
        count = len(members)
        kid = f"`{name}/` is {hint[1]}." if hint else f"`{name}/` is a hallway with {_plural(count, 'room')} in it."
        plain = f"`{name}/`: {hint[2]}." if hint else f"`{name}/`: a folder with {_plural(count, 'file')}."
        if hint:
            kid += f" ({_plural(count, 'room')})"
            plain += f" ({_plural(count, 'file')})"
        if main_lang:
            plain += f" Mostly {main_lang}."
        out[d] = {"emoji": emoji, "kid": kid, "plain": plain, "hinted": bool(hint)}
    return out


# ---------------------------------------------------------------- story


def _flow_steps(files: dict[str, FileInfo], root_id: str, limit: int = 9, max_depth: int = 3):
    by_id = {f"{p}::{s.qual}": (p, s) for p, i in files.items() for s in i.symbols}
    steps, seen = [], set()

    def walk(sid, depth, parent, line):
        if sid in seen or sid not in by_id or len(steps) >= limit:
            return
        seen.add(sid)
        steps.append((sid, parent, line, depth))
        if depth >= max_depth:
            return
        for c in by_id[sid][1].callees:
            if c["conf"] == "certain":
                walk(c["id"], depth + 1, sid, c["line"])

    walk(root_id, 0, None, 0)
    return steps, by_id


def build_story(files, flow_roots, reading_order, repo, folders, names, callers) -> list[dict]:
    steps: list[dict] = []
    code_langs = sorted(repo["languages"].items(), key=lambda kv: -kv[1]["loc"])
    code_langs = [(n, v) for n, v in code_langs if n in ("Python", "JavaScript", "TypeScript", "Go", "Rust", "Java", "Ruby", "C", "C++", "C#", "PHP", "Kotlin", "Swift")]
    lang_txt = _list([n for n, _ in code_langs[:2]], 2, False) or "text files"
    n_files, loc = repo["files"], repo["loc"]
    top_dirs = sorted({p.split("/")[0] for p in files if "/" in p})
    steps.append({
        "title": {"kid": "The big picture", "plain": "Overview"},
        "text": {
            "kid": (f"Imagine this project is a big house. It has {_plural(n_files, 'room')} and about {loc:,} lines of instructions, "
                    f"mostly written in {lang_txt}. Each room (file) holds helpers that each do one little job. "
                    "Rooms borrow tools from each other, and one room is the front door where everything begins. "
                    "Let's take a tour!"),
            "plain": (f"{repo['name']} has {n_files} files and about {loc:,} lines, mostly {lang_txt}. "
                      "This walkthrough shows how it is organised, where execution starts, and what happens first."),
        },
    })
    if top_dirs:
        lines_k = [f"{folders[d]['emoji']} {folders[d]['kid']}" for d in top_dirs if d in folders][:8]
        lines_p = [f"{folders[d]['plain']}" for d in top_dirs if d in folders][:8]
        root_files = [p for p in files if "/" not in p]
        if root_files:
            lines_k.append(f"📄 And {_plural(len(root_files), 'room')} {'sits' if len(root_files) == 1 else 'sit'} right by the entrance: {_list([posixpath.basename(p) for p in root_files], 5, False)}.")
            lines_p.append(f"Top-level files: {_list(root_files, 5)}.")
        steps.append({
            "title": {"kid": "The floors of the house", "plain": "Folder layout"},
            "text": {"kid": "\n".join(lines_k), "plain": "\n".join(lines_p)},
        })
    root = next((r for r in flow_roots if r["kind"] in ("console", "route", "handler", "main")), None) or (flow_roots[0] if flow_roots else None)
    if root:
        flow, by_id = _flow_steps(files, root["id"])
        if len(flow) <= 1 and len(flow_roots) > 1:  # pick a root with something to show
            for r in flow_roots:
                f2, b2 = _flow_steps(files, r["id"])
                if len(f2) > len(flow):
                    root, flow, by_id = r, f2, b2
        for i, (sid, parent, line, depth) in enumerate(flow):
            path, sym = by_id[sid]
            ex = explain_symbol(path, sym, files[path], names, callers)
            label = names.get(sid, sym.qual)
            if i == 0:
                title = {"kid": "The front door", "plain": f"Start: {label}"}
                intro = {"kid": f"Everything begins here, in `{label}` (room `{posixpath.basename(path)}`). It starts because: {root['why']}. ",
                         "plain": f"Execution starts in `{label}` ({path}) because: {root['why']}. "}
            else:
                pl = names.get(parent, "")
                title = {"kid": f"Meet “{_readable(sym.name) if sym.kind != 'module' else label}”", "plain": label if sym.kind == "module" else f"{label}()"}
                intro = {"kid": f"`{pl}` asks `{label}` for help (line {line}). ",
                         "plain": f"`{pl}` calls `{label}` (line {line}). "}
            steps.append({"title": title, "text": {"kid": intro["kid"] + ex["kid"], "plain": intro["plain"] + ex["plain"]},
                          "path": path, "line": sym.line, "end": sym.end_line, "symbol": sid})
    if reading_order:
        top = reading_order[:6]
        steps.append({
            "title": {"kid": "Where to wander next", "plain": "Suggested reading order"},
            "text": {
                "kid": "Visit the rooms in this order. The earlier ones are the front doors and the busiest rooms:\n" +
                       "\n".join(f"{i}. {o['path']}" for i, o in enumerate(top, 1)),
                "plain": "Read files in this order:\n" + "\n".join(f"{i}. {o['path']}  ({o['why']})" for i, o in enumerate(top, 1)),
            },
            "path": top[0]["path"],
        })
    return steps


# ---------------------------------------------------------------- entry


def explain_repo(files: dict[str, FileInfo], flow_roots: list[dict], reading_order: list[dict], repo: dict) -> dict:
    names = {f"{p}::{s.qual}": (s.qual if s.kind != "module" else posixpath.basename(p)) for p, i in files.items() for s in i.symbols}
    callers = _callers(files)
    for path, info in files.items():
        info.explain = explain_file(info)
        for sym in info.symbols:
            sym.explain = explain_symbol(path, sym, info, names, callers)
    folders = explain_folders(files)
    return {
        "folders": folders,
        "story": build_story(files, flow_roots, reading_order, repo, folders, names, callers),
        "glossary": [{"term": t, "kid": k, "plain": p} for t, k, p in GLOSSARY],
    }
