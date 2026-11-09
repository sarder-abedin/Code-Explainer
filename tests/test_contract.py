"""The contract of a function: what it takes, hands back, can fail with, and touches. Worked out from the syntax tree."""
import json

import pytest

from code_explainer import contract
from code_explainer.analysis import javascript_ts
from code_explainer.core import analyze_repo
from conftest import write

needs_ts = pytest.mark.skipif(not javascript_ts.AVAILABLE, reason="tree-sitter not installed")


def contracts(tmp_path, files):
    write(tmp_path, files)
    data = analyze_repo(tmp_path, "x")
    return {(f["path"], s["qual"]): s.get("contract") for f in data["files"] for s in f["symbols"]}, data


def one(tmp_path, code, name="f", path="a.py"):
    got, _ = contracts(tmp_path, {path: code})
    return got[(path, name)]


# ----------------------------------------------------------------- what it takes
def test_parameters_with_their_kinds_and_defaults(tmp_path):
    c = one(tmp_path, "def f(a, b=2, *args, c, d=4, **kw):\n    return a\n")
    assert c["params"] == [{"n": "a", "k": "pos"}, {"n": "b", "k": "pos", "d": "2"}, {"n": "args", "k": "var"},
                           {"n": "c", "k": "kw"}, {"n": "d", "k": "kw", "d": "4"}, {"n": "kw", "k": "varkw"}]


def test_positional_only_parameters_are_still_parameters(tmp_path):
    c = one(tmp_path, "def f(a, b, /, c):\n    return a\n")
    assert [p["n"] for p in c["params"]] == ["a", "b", "c"]


def test_self_and_cls_are_not_listed_but_the_method_is_marked(tmp_path):
    got, _ = contracts(tmp_path, {"a.py": "class A:\n    def m(self, x):\n        return x\n\n    @classmethod\n    def make(cls, y):\n        return cls()\n"})
    assert got[("a.py", "A.m")]["params"] == [{"n": "x", "k": "pos"}] and got[("a.py", "A.m")]["method"] is True
    assert got[("a.py", "A.make")]["params"] == [{"n": "y", "k": "pos"}]


def test_a_plain_function_is_not_marked_as_a_method(tmp_path):
    assert one(tmp_path, "def f(x):\n    return x\n").get("method") is False


def test_type_annotations_are_kept(tmp_path):
    c = one(tmp_path, "def f(x: int, y: 'list[str]' = None) -> bool:\n    return True\n")
    assert c["params"][0] == {"n": "x", "k": "pos", "t": "int"} and c["params"][1]["t"] == "'list[str]'" and c["returns"]["t"] == "bool"


def test_a_long_default_is_shortened(tmp_path):
    c = one(tmp_path, "def f(x=" + "[" + ", ".join(str(i) for i in range(40)) + "]):\n    return x\n")
    assert len(c["params"][0]["d"]) <= 30 and c["params"][0]["d"].endswith("…")


def test_a_very_long_parameter_list_is_capped_and_says_how_many_were_left_out(tmp_path):
    c = one(tmp_path, "def f(" + ", ".join(f"a{i}" for i in range(20)) + "):\n    return 1\n")
    assert len(c["params"]) == contract.MAX_PARAMS and c["moreParams"] == 20 - contract.MAX_PARAMS


# ----------------------------------------------------------------- what it hands back
@pytest.mark.parametrize("expr,kind", [
    ("1", "number"), ("2.5", "number"), ("'a'", "text"), ("f'{x}'", "text"), ("None", "none"), ("True", "bool"),
    ("[1]", "list"), ("[i for i in x]", "list"), ("{'a': 1}", "dict"), ("{1, 2}", "set"), ("(1, 2)", "tuple"),
    ("g(x)", "call:g"), ("self.g(x)", "call:self.g"), ("x", "name:x"), ("x.y", "attr:x.y"), ("x + 1", "computed"),
    ("x > 1", "bool"), ("not x", "bool"), ("x and y", "bool"), ("await g()", "call:g"), ("lambda: 1", "function"),
])
def test_what_each_kind_of_return_is_called(tmp_path, expr, kind):
    c = one(tmp_path, f"async def f(x, y):\n    return {expr}\n")
    assert c["returns"]["v"] == [kind]


def test_several_returns_are_listed_once_each_in_order(tmp_path):
    c = one(tmp_path, "def f(x):\n    if x == 1:\n        return 1\n    if x == 2:\n        return 2\n    if x == 3:\n        return None\n    return 'a'\n")
    assert c["returns"]["v"] == ["number", "none", "text"]


def test_a_conditional_return_lists_both_sides(tmp_path):
    assert one(tmp_path, "def f(x):\n    return 1 if x else None\n")["returns"]["v"] == ["number", "none"]


def test_returns_in_nested_functions_and_lambdas_do_not_count(tmp_path):
    c = one(tmp_path, "def f(x):\n    def inner():\n        return 'text'\n    g = lambda: [1]\n    return 1\n")
    assert c["returns"]["v"] == ["number"]


def test_a_generator_is_marked_and_an_async_function_too(tmp_path):
    g = one(tmp_path, "def f(x):\n    yield x\n    yield from other()\n")
    assert g["returns"]["gen"] is True
    a = one(tmp_path / "b", "async def f(x):\n    return 1\n")
    assert a["async"] is True and one(tmp_path / "c", "def f(x):\n    return 1\n")["async"] is False


@pytest.mark.parametrize("code,falls", [
    ("def f(x):\n    if x:\n        return 1\n", True),
    ("def f(x):\n    return 1\n", False),
    ("def f(x):\n    if x:\n        return 1\n    else:\n        return 2\n", False),
    ("def f(x):\n    print(x)\n", True),
    ("def f(x):\n    raise ValueError()\n", False),
    ("def f(x):\n    while True:\n        pass\n", False),
    ("def f(x):\n    for i in x:\n        return i\n", True),
    ("def f(x):\n    try:\n        return 1\n    finally:\n        pass\n", False),
])
def test_it_notes_when_the_end_of_the_function_can_be_reached_and_so_nothing_is_returned(tmp_path, code, falls):
    assert one(tmp_path, code)["returns"]["none"] is falls


def test_a_loop_that_forever_can_still_be_left_with_break(tmp_path):
    assert one(tmp_path, "def f(c):\n    while True:\n        if c():\n            break\n")["returns"]["none"] is True


def test_a_break_in_an_inner_loop_does_not_leave_the_outer_one(tmp_path):
    assert one(tmp_path, "def f(xs):\n    while True:\n        for x in xs:\n            break\n")["returns"]["none"] is False


def test_many_different_returns_are_cut_and_counted(tmp_path):
    c = one(tmp_path, "def f(x):\n    if x == 1:\n        return 1\n    if x == 2:\n        return 'a'\n    if x == 3:\n        return [1]\n    if x == 4:\n        return {}\n    if x == 5:\n        return None\n    return True\n")
    assert len(c["returns"]["v"]) == contract.MAX_RETURNS and c["returns"]["moreValues"] == 2


# ----------------------------------------------------------------- what it can fail with
def test_raised_errors_are_named(tmp_path):
    c = one(tmp_path, "def f(x):\n    if x:\n        raise ValueError('no')\n    raise pkg.Boom from None\n")
    assert c["raises"] == ["ValueError", "pkg.Boom"]


def test_a_bare_raise_and_a_raised_variable_are_noted_as_such(tmp_path):
    c = one(tmp_path, "def f(x):\n    try:\n        g(x)\n    except Exception as e:\n        raise\n    raise e\n")
    assert c["raises"] == ["re-raise", "a caught error"]


def test_an_assert_can_fail_with_assertion_error(tmp_path):
    assert one(tmp_path, "def f(x):\n    assert x\n    return x\n")["raises"] == ["AssertionError"]


def test_raises_in_nested_functions_do_not_count(tmp_path):
    assert one(tmp_path, "def f(x):\n    def inner():\n        raise KeyError()\n    return inner\n")["raises"] == []


def test_the_list_of_errors_is_capped(tmp_path):
    body = "".join(f"    if x == {i}:\n        raise E{i}()\n" for i in range(12))
    assert len(one(tmp_path, "def f(x):\n" + body)["raises"]) == contract.MAX_RAISES


# ----------------------------------------------------------------- what it changes
def test_assignments_to_attributes_of_self_are_changes(tmp_path):
    got, _ = contracts(tmp_path, {"a.py": "class A:\n    def m(self, x):\n        self.a = x\n        self.b += 1\n        self.c: int = 2\n        self.d, self.e = 1, 2\n        local = 1\n        return local\n"})
    assert got[("a.py", "A.m")]["changes"] == ["self.a", "self.b", "self.c", "self.d", "self.e"]


def test_changing_a_list_held_by_self_is_a_change(tmp_path):
    got, _ = contracts(tmp_path, {"a.py": "class A:\n    def m(self, x):\n        self.items.append(x)\n"})
    assert got[("a.py", "A.m")]["changes"] == ["self.items"]


def test_a_global_that_is_written_is_a_change(tmp_path):
    assert one(tmp_path, "COUNT = 0\n\n\ndef f():\n    global COUNT\n    COUNT += 1\n")["changes"] == ["global COUNT"]


@pytest.mark.parametrize("stmt", ["out.append(1)", "out.update({})", "out.pop()", "out[0] = 1", "out.x = 1", "out.sort()"])
def test_changing_an_argument_is_noted(tmp_path, stmt):
    assert one(tmp_path, f"def f(out):\n    {stmt}\n")["changes"] == ["argument out"]


def test_giving_an_argument_a_new_value_is_not_a_change_to_it(tmp_path):
    assert one(tmp_path, "def f(x):\n    x = 5\n    x += 1\n    return x\n")["changes"] == []


def test_changing_a_local_is_not_a_change(tmp_path):
    assert one(tmp_path, "def f(x):\n    out = []\n    out.append(x)\n    out[0] = 1\n    return out\n")["changes"] == []


def test_configuring_a_logger_is_not_logging(tmp_path):
    assert one(tmp_path, "import logging\nlog = logging.getLogger()\n\n\ndef f():\n    log.setLevel(10)\n    log.handlers.clear()\n")["outputs"] == []


def test_printing_logging_and_reading_input_are_listed_as_outputs(tmp_path):
    c = one(tmp_path, "import logging\nlog = logging.getLogger()\n\n\ndef f(x):\n    print(x)\n    log.info('hi')\n    logging.warning('w')\n    y = input()\n    return y\n")
    assert c["outputs"] == ["prints", "logs", "reads input"]


# ----------------------------------------------------------------- what it touches
def test_calls_into_known_libraries_are_recognised(tmp_path):
    c = one(tmp_path, "import sqlite3\n\n\ndef f():\n    return sqlite3.connect('x')\n")
    assert c["touches"] == [{"kind": "database", "via": ["sqlite3.connect"]}] and c["indirect"] == []


@pytest.mark.parametrize("code,kind,via", [
    ("import requests\n\ndef f():\n    return requests.get('u')\n", "web-client", "requests.get"),
    ("import subprocess\n\ndef f():\n    return subprocess.run(['ls'])\n", "processes", "subprocess.run"),
    ("from subprocess import run\n\ndef f():\n    return run(['ls'])\n", "processes", "run"),
    ("import sqlite3 as db\n\ndef f():\n    return db.connect('x')\n", "database", "db.connect"),
    ("import os\n\ndef f():\n    return os.system('ls')\n", "processes", "os.system"),
    ("def f():\n    return open('a')\n", "files", "open"),
    ("import shutil\n\ndef f():\n    return shutil.copy('a', 'b')\n", "files", "shutil.copy"),
    ("import socket\n\ndef f():\n    return socket.socket()\n", "network", "socket.socket"),
])
def test_what_the_function_talks_to_directly(tmp_path, code, kind, via):
    c = one(tmp_path, code)
    assert [(t["kind"], via in t["via"]) for t in c["touches"]] == [(kind, True)]


def test_a_function_defined_in_the_project_with_a_builtins_name_is_not_the_builtin(tmp_path):
    c = one(tmp_path, "def open(x):\n    return x\n\n\ndef f():\n    return open('a')\n", name="f")
    assert c["touches"] == []


def test_a_project_module_with_a_library_name_is_not_the_library(tmp_path):
    got, _ = contracts(tmp_path, {"redis.py": "def get():\n    return 1\n", "main.py": "import redis\n\n\ndef f():\n    return redis.get()\n"})
    assert got[("main.py", "f")]["touches"] == []


def test_things_it_only_reaches_through_other_functions_are_marked_indirect(tmp_path):
    files = {"db.py": "import sqlite3\n\n\ndef load():\n    return sqlite3.connect('x')\n",
             "svc.py": "from db import load\n\n\ndef total():\n    return load()\n\n\ndef report():\n    return total()\n"}
    got, _ = contracts(tmp_path, files)
    assert got[("svc.py", "total")]["touches"] == [] and got[("svc.py", "total")]["indirect"] == [{"kind": "database", "via": "load"}]
    assert got[("svc.py", "report")]["indirect"] == [{"kind": "database", "via": "total"}]      # names the first step, not the whole chain


def test_a_guess_further_down_the_chain_does_not_count_either(tmp_path):
    files = {"a.py": "from b import mid\n\n\ndef top():\n    return mid(1)\n",
             "b.py": "def mid(o):\n    return o.run()\n",
             "k.py": "import sqlite3\n\n\nclass K:\n    def run(self):\n        return sqlite3.connect('x')\n"}
    got, _ = contracts(tmp_path, files)
    assert got[("a.py", "top")]["indirect"] == [] and got[("b.py", "mid")]["indirect"] == []


def test_a_very_long_chain_stops_being_followed(tmp_path):
    body = "".join(f"def f{i}():\n    return f{i + 1}()\n\n\n" for i in range(10))
    code = "import sqlite3\n\n\n" + body + "def f10():\n    return sqlite3.connect('x')\n"
    got, _ = contracts(tmp_path, {"a.py": code})
    assert got[("a.py", "f0")]["indirect"] == [] and got[("a.py", "f2")]["indirect"] == [{"kind": "database", "via": "f3"}]


def test_a_direct_touch_is_not_repeated_as_indirect(tmp_path):
    files = {"db.py": "import sqlite3\n\n\ndef load():\n    return sqlite3.connect('x')\n",
             "svc.py": "import sqlite3\nfrom db import load\n\n\ndef total():\n    sqlite3.connect('y')\n    return load()\n"}
    got, _ = contracts(tmp_path, files)
    assert [t["kind"] for t in got[("svc.py", "total")]["touches"]] == ["database"] and got[("svc.py", "total")]["indirect"] == []


def test_calls_that_loop_back_do_not_hang_the_search(tmp_path):
    files = {"a.py": "import sqlite3\n\n\ndef a():\n    sqlite3.connect('x')\n    return b()\n\n\ndef b():\n    return a()\n"}
    got, _ = contracts(tmp_path, files)
    assert [t["kind"] for t in got[("a.py", "a")]["touches"]] == ["database"] and got[("a.py", "b")]["indirect"] == [{"kind": "database", "via": "a"}]


def test_guessed_calls_do_not_count_as_touching_things(tmp_path):
    files = {"a.py": "def use(o):\n    return o.run()\n", "b.py": "import sqlite3\n\n\nclass K:\n    def run(self):\n        return sqlite3.connect('x')\n"}
    got, _ = contracts(tmp_path, files)
    assert got[("a.py", "use")]["indirect"] == []


def test_the_same_kind_is_listed_once_with_a_couple_of_examples(tmp_path):
    c = one(tmp_path, "import sqlite3\n\n\ndef f():\n    sqlite3.connect('a')\n    sqlite3.connect('b')\n    sqlite3.Row\n    sqlite3.complete_statement('x')\n    sqlite3.connect('c')\n")
    assert [t["kind"] for t in c["touches"]] == ["database"] and 1 <= len(c["touches"][0]["via"]) <= contract.MAX_VIA


# ----------------------------------------------------------------- the whole analysis
def test_only_functions_and_methods_get_a_contract(tmp_path):
    got, _ = contracts(tmp_path, {"a.py": "import os\nx = os.getcwd()\n\n\nclass A:\n    def m(self):\n        return 1\n\n\ndef f():\n    return 2\n"})
    assert got[("a.py", "A")] is None and got[("a.py", "<module>")] is None
    assert got[("a.py", "A.m")] is not None and got[("a.py", "f")] is not None


def test_the_contract_is_plain_json_and_the_same_every_time(tmp_path):
    a, _ = contracts(tmp_path / "one", {"a.py": "def f(x: int = 1) -> int:\n    return x\n"})
    b, _ = contracts(tmp_path / "two", {"a.py": "def f(x: int = 1) -> int:\n    return x\n"})
    assert json.loads(json.dumps(a[("a.py", "f")])) == a[("a.py", "f")] and a == b


def test_when_the_report_would_get_too_big_the_least_complex_functions_lose_theirs(tmp_path, monkeypatch):
    files = {"a.py": "def simple(x):\n    return x\n\n\ndef busy(x):\n    if x:\n        for i in x:\n            if i:\n                return i\n    return None\n"}
    full, _ = contracts(tmp_path / "full", files)
    size = {q: len(json.dumps(full[("a.py", q)], separators=(",", ":"))) for q in ("simple", "busy")}
    monkeypatch.setattr(contract, "CONTRACT_BUDGET_BYTES", 1)
    none, data = contracts(tmp_path / "none", files)
    assert none[("a.py", "simple")] is None and none[("a.py", "busy")] is None and data["contractTrimmed"] == 2
    monkeypatch.setattr(contract, "CONTRACT_BUDGET_BYTES", size["busy"] + 5)          # room for the busy one only
    kept, data2 = contracts(tmp_path / "kept", files)
    assert kept[("a.py", "busy")] is not None and kept[("a.py", "simple")] is None and data2["contractTrimmed"] == 1


def test_nothing_is_reported_trimmed_when_everything_fits(tmp_path):
    _, data = contracts(tmp_path, {"a.py": "def f():\n    return 1\n"})
    assert data["contractTrimmed"] == 0


def test_a_syntax_error_file_just_has_no_contracts(tmp_path):
    got, _ = contracts(tmp_path, {"a.py": "def f(:\n    pass\n"})
    assert all(v is None for v in got.values())


# ----------------------------------------------------------------- JavaScript and TypeScript
@needs_ts
def test_javascript_parameters_defaults_and_rest(tmp_path):
    c = one(tmp_path, "function f(a, b = 2, ...rest) {\n  return a;\n}\n", path="a.js")
    assert c["params"] == [{"n": "a", "k": "pos"}, {"n": "b", "k": "pos", "d": "2"}, {"n": "rest", "k": "var"}]


@needs_ts
def test_javascript_destructured_and_arrow_parameters(tmp_path):
    c = one(tmp_path, "const f = ({ a, b }, [c]) => {\n  return a;\n};\n", path="a.js")
    assert [p["n"] for p in c["params"]] == ["{ a, b }", "[c]"]


@needs_ts
def test_typescript_types_and_async(tmp_path):
    c = one(tmp_path, "export async function f(x: number, y?: string): Promise<boolean> {\n  return true;\n}\n", path="a.ts")
    assert c["async"] is True and c["params"][0] == {"n": "x", "k": "pos", "t": "number"} and c["returns"]["t"] == "Promise<boolean>"


@needs_ts
@pytest.mark.parametrize("expr,kind", [
    ("1", "number"), ("'a'", "text"), ("`a${x}`", "text"), ("null", "none"), ("undefined", "none"), ("true", "bool"), ("[1]", "list"),
    ("({a: 1})", "dict"), ("g(x)", "call:g"), ("x", "name:x"), ("x.y", "attr:x.y"), ("x + 1", "computed"), ("x > 1", "bool"),
    ("!x", "bool"), ("new Map()", "call:Map"), ("await g()", "call:g"),
])
def test_javascript_returns(tmp_path, expr, kind):
    c = one(tmp_path, f"async function f(x) {{\n  return {expr};\n}}\n", path="a.js")
    assert c["returns"]["v"] == [kind]


@needs_ts
def test_javascript_throws_and_the_end_of_the_function(tmp_path):
    c = one(tmp_path, "function f(x) {\n  if (x) {\n    throw new TypeError('no');\n  }\n}\n", path="a.js")
    assert c["raises"] == ["TypeError"] and c["returns"]["none"] is True
    d = one(tmp_path / "d", "function f(x) {\n  return 1;\n}\n", path="a.js")
    assert d["returns"]["none"] is False and d["raises"] == []


@needs_ts
def test_javascript_changes_and_outputs(tmp_path):
    got, _ = contracts(tmp_path, {"a.js": "class A {\n  m(out) {\n    this.count = 1;\n    out.push(2);\n    console.log('x');\n  }\n}\n"})
    c = got[("a.js", "A.m")]
    assert c["changes"] == ["this.count", "argument out"] and c["outputs"] == ["prints"]


@needs_ts
def test_javascript_touches(tmp_path):
    files = {"package.json": '{"name": "x"}', "a.js": "import fs from 'node:fs';\nimport { Pool } from 'pg';\n\nexport function f() {\n  fetch('u');\n  return fs.readFileSync('a');\n}\n\nexport function g() {\n  return new Pool();\n}\n"}
    got, _ = contracts(tmp_path, files)
    assert sorted(t["kind"] for t in got[("a.js", "f")]["touches"]) == ["files", "web-client"]
    assert [t["kind"] for t in got[("a.js", "g")]["touches"]] == ["database"]


@needs_ts
def test_javascript_giving_an_argument_a_new_value_is_not_a_change_to_it(tmp_path):
    assert one(tmp_path, "function f(x) {\n  x = 5;\n  x += 1;\n  return x;\n}\n", path="a.js")["changes"] == []


@needs_ts
def test_javascript_a_loop_that_forever_can_still_be_left_with_break(tmp_path):
    left = one(tmp_path, "function f(c) {\n  while (true) {\n    if (c()) { break; }\n  }\n}\n", path="a.js")
    stuck = one(tmp_path / "x", "function f(c) {\n  while (true) {\n    c();\n  }\n}\n", path="a.js")
    assert left["returns"]["none"] is True and stuck["returns"]["none"] is False


@needs_ts
def test_javascript_thrown_errors_are_named_and_a_thrown_variable_is_noted(tmp_path):
    c = one(tmp_path, "function f(x, err) {\n  if (x) { throw new TypeError('no'); }\n  throw err;\n}\n", path="a.js")
    assert c["raises"] == ["TypeError", "a caught error"]
