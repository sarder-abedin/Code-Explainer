import pytest

from code_explainer.analysis import analyze_file, detect_language, javascript_ts
from code_explainer.duplicates import body_hash, find_duplicates, symbol_hashes


def py(src, path="m.py"):
    return analyze_file(path, "Python", src)


def test_detect_language():
    assert detect_language("a/b.PY") == "Python"
    assert detect_language("x.tsx") == "TypeScript" and detect_language("x.mjs") == "JavaScript"
    assert detect_language("Makefile") == "Other" and detect_language("x.go") == "Go"


def test_python_syntax_error_is_reported_not_raised():
    f = py("def broken(:\n")
    assert f.parse_error and not f.analyzed and f.symbols == []
    assert f.loc == 1 and f.lines == 1


def test_python_symbols_nested_classes_async_and_module_calls():
    f = py('"""Mod doc."""\nimport os as o\nfrom a import b as c, d\n\nclass Outer:\n    """O."""\n    class Inner:\n        def m(self):\n            pass\n    async def run(self):\n        await helper()\n\ndef top():\n    pass\n\ntop()\n')
    quals = {s.qual: s.kind for s in f.symbols}
    assert quals == {"<module>": "module", "Outer": "class", "Outer.Inner": "class", "Outer.Inner.m": "method",
                     "Outer.run": "method", "top": "function"}
    assert f.doc == "Mod doc."
    assert f.bindings == {"o": (0, "os", None), "c": (0, "a", "b"), "d": (0, "a", "d")}
    mod = next(s for s in f.symbols if s.kind == "module")
    assert mod.raw_calls == [(["top"], 16)]


def test_python_complexity_counts_each_construct():
    src = "def f(x):\n    if x:\n        pass\n    elif x > 1 and x < 3 or x == 5:\n        pass\n    for i in x:\n        while i:\n            pass\n    try:\n        pass\n    except ValueError:\n        pass\n    return [i for i in x]\n"
    # 1 + if + elif(if) + boolop(and/or: 2 operators across one BoolOp tree) + for + while + except + comprehension
    sym = next(s for s in py(src).symbols if s.name == "f")
    assert sym.complexity == 1 + 1 + 1 + 2 + 1 + 1 + 1 + 1


def test_python_match_statement_branches():
    sym = next(s for s in py("def f(x):\n    match x:\n        case 1:\n            pass\n        case 2:\n            pass\n").symbols if s.name == "f")
    assert sym.complexity == 3


def test_python_call_chains():
    f = py("def f():\n    a.b.c()\n    foo()\n    get().x()\n    (lambda: 1)()\n")
    chains = [c for c, _ in next(s for s in f.symbols if s.name == "f").raw_calls]
    # `get()` runs before `.x()` is called on its result
    assert chains == [["a", "b", "c"], ["foo"], ["get"], ["?", "x"]]


def test_python_nested_call_runs_inner_first():
    f = py("def f():\n    outer(inner(1), 2)\n    second()\n")
    assert [c for c, _ in next(s for s in f.symbols if s.name == "f").raw_calls] == [["inner"], ["outer"], ["second"]]


@pytest.mark.parametrize("deco, expected", [
    ("@app.route('/x')", "HTTP route"), ("@router.get('/x')", "HTTP route"),
    ("@click.command()", "registered handler"), ("@shared_task", "registered handler"), ("@staticmethod", ""),
    ("@app.get", ""),   # bare attribute without a call is not a route registration
])
def test_python_decorator_entry_points(deco, expected):
    f = py(f"{deco}\ndef h():\n    pass\n")
    entry = next(s for s in f.symbols if s.name == "h").entry
    assert (expected in entry) if expected else entry == ""


def test_python_main_guard_variants():
    assert py("if __name__ == '__main__':\n    pass\n").entry_reasons
    assert py("if '__main__' == __name__:\n    pass\n").entry_reasons
    assert not py("if __name__ == 'other':\n    pass\n").entry_reasons


def test_generic_languages_get_only_size_and_rough_complexity():
    go = analyze_file("a.go", "Go", "func f() {\n  if a && b {\n  }\n  for {\n  }\n}\n")
    assert go.complexity == 3 and not go.analyzed and go.symbols == []
    assert analyze_file("a.json", "JSON", '{"if": 1}').complexity == 0
    assert analyze_file("a.md", "Markdown", "if for while").complexity == 0


JS = """import a from './a';
const b = require('./b');
// ---- section ----
export function plain(x) { if (x && y) { return 1; } }
/** Docs here. */
export default class Widget {}
const arrow = async (q) => q ? 1 : 2;
export const typed = (n: number): number => n;
"""


def test_regex_fallback_symbols_docs_and_imports(monkeypatch):
    monkeypatch.setattr(javascript_ts, "AVAILABLE", False)
    f = analyze_file("x.js", "JavaScript", JS)
    assert f.analyzer == "regex" and not f.has_calls
    assert [(s.name, s.kind) for s in f.symbols] == [("plain", "function"), ("Widget", "class"), ("arrow", "function"), ("typed", "function")]
    assert f.raw_imports == ["./a", "./b"]
    docs = {s.name: s.doc for s in f.symbols}
    assert docs["plain"] == "" and docs["Widget"] == "Docs here."      # section dividers are not docs
    assert next(s for s in f.symbols if s.name == "plain").complexity >= 3


@pytest.mark.skipif(not javascript_ts.AVAILABLE, reason="tree-sitter not installed")
class TestTreeSitter:
    def test_default_export_forms_and_reexports(self):
        f = analyze_file("a.ts", "TypeScript", "export default function () { go(); }\nexport * from './x';\nexport { y } from './y';\n")
        assert f.default_export == "default" and f.raw_imports == ["./x", "./y"]
        f = analyze_file("b.js", "JavaScript", "class K {}\nexport default K;\n")
        assert f.default_export == "K"
        f = analyze_file("c.ts", "TypeScript", "export default class Svc { run() {} }\n")
        assert f.default_export == "Svc" and [s.qual for s in f.symbols] == ["Svc", "Svc.run"]

    def test_class_property_arrow_methods_and_ts_only_syntax(self):
        f = analyze_file("a.tsx", "TypeScript", "interface I { x: number }\ntype T = string;\nenum E { A }\n"
                         "class C { handle = (e: Event) => { go(e); };\n  abstractLike(): void;\n}\n"
                         "const el = <div>{render()}</div>;\n")
        quals = [s.qual for s in f.symbols]
        assert "C" in quals and "C.handle" in quals and "I" not in quals and "E" not in quals
        assert (["go"], 4) in next(s for s in f.symbols if s.qual == "C.handle").raw_calls
        assert any(c == ["render"] for c, _ in next(s for s in f.symbols if s.kind == "module").raw_calls)

    def test_dynamic_import_and_require_patterns(self):
        f = analyze_file("a.js", "JavaScript", "const { a, b: bb } = require('./m');\nconst n = require('./n');\nasync function f() { await import('./lazy'); }\n")
        assert f.raw_imports == ["./m", "./n", "./lazy"]
        assert f.bindings == {"a": ("./m", "a"), "bb": ("./m", "b"), "n": ("./n", "*")}

    def test_nest_style_decorators(self):
        f = analyze_file("c.ts", "TypeScript", "class C {\n  @Post('/x')\n  create() {}\n  @Cron('* * * * *')\n  tick() {}\n  plain() {}\n}\n")
        e = {s.name: s.entry for s in f.symbols}
        assert "HTTP route" in e["create"] and "registered handler" in e["tick"] and e["plain"] == ""

    def test_nested_calls_are_in_execution_order(self):
        f = analyze_file("a.js", "JavaScript", "function f() { outer(inner(1), 2); get().x(); second(); }\n")
        assert [c for c, _ in f.symbols[0].raw_calls] == [["inner"], ["outer"], ["get"], ["?", "x"], ["second"]]

    def test_complexity_counts_logical_operators_and_ternaries(self):
        f = analyze_file("a.js", "JavaScript", "function f(a, b) { return a && b || (a ? 1 : 2) ?? 3; }\n")
        assert f.symbols[0].complexity == 1 + 2 + 1 + 1


def test_duplicates_ignore_comments_blank_lines_and_signature_names():
    body = "    a = 1\n    b = 2\n    c = a + b\n    d = c * 2\n    e = d - 1\n    return e\n"
    f1 = py(f"def one(x):\n{body}")
    f2 = py(f"def two(y, z):\n    # a comment\n\n{body}")
    f1.source, f2.source = f"def one(x):\n{body}", f"def two(y, z):\n    # a comment\n\n{body}"
    files = {"p.py": f1, "q.py": f2}
    groups = find_duplicates(files)
    assert len(groups) == 1 and {m["qual"] for m in groups[0]["members"]} == {"one", "two"}
    assert groups[0]["lines"] == 6


def test_duplicates_threshold_and_missing_source():
    short = py("def f():\n    return 1\n")
    assert body_hash(short, 1, 2) is None
    short.source = ""
    assert symbol_hashes({"a.py": short}) == {}
    assert find_duplicates({}) == []
