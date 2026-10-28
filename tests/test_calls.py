import pytest

from code_explainer.analysis import javascript_ts
from code_explainer.core import analyze_repo

needs_ts = pytest.mark.skipif(not javascript_ts.AVAILABLE, reason="tree-sitter not installed")


def build(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    d = analyze_repo(tmp_path)
    return d, {f["path"]: f for f in d["files"]}


def callees(f, qual):
    sym = next(s for s in f["symbols"] if s["qual"] == qual)
    return {c["id"]: c["conf"] for c in sym["callees"]}


def test_python_call_resolution(tmp_path):
    d, f = build(tmp_path, {
        "app/__init__.py": "from .core import run\n",
        "app/core.py": (
            "from . import util\nimport app.util as u\nfrom .models import Item\n\n"
            "def run():\n    util.clean()\n    u.clean()\n    Item(1).save()\n    helper()\n    os.getcwd()\n\n"
            "def helper():\n    run()\n"),
        "app/util.py": "def clean():\n    pass\n",
        "app/models.py": (
            "class Item:\n    def __init__(self, n):\n        self.n = n\n        self.prep()\n"
            "    def prep(self):\n        pass\n    def save(self):\n        pass\n"),
        "main.py": "import app\nfrom app import run\n\ndef go():\n    app.run()\n    run()\n\nif __name__ == '__main__':\n    go()\n",
    })
    run = callees(f["app/core.py"], "run")
    assert run["app/util.py::clean"] == "certain"
    assert run["app/models.py::Item.__init__"] == "certain"   # class call -> constructor
    assert run["app/models.py::Item.save"] == "inferred"       # obj.save(): by name only
    assert run["app/core.py::helper"] == "certain"
    assert not any("getcwd" in k for k in run)                   # external, no edge
    assert callees(f["app/core.py"], "helper") == {"app/core.py::run": "certain"}
    assert callees(f["app/models.py"], "Item.__init__") == {"app/models.py::Item.prep": "certain"}
    go = callees(f["main.py"], "go")
    assert go == {"app/core.py::run": "certain"}                 # via package re-export, both spellings
    assert callees(f["main.py"], "<module>") == {"main.py::go": "certain"}


def test_flow_roots_and_decorators(tmp_path):
    d, f = build(tmp_path, {
        "web.py": "from flask import Flask\napp = Flask(__name__)\n\n@app.route('/x')\ndef index():\n    return work()\n\ndef work():\n    return 1\n",
        "pyproject.toml": "[project]\nname='x'\n[project.scripts]\nxx = 'tool:main'\n",
        "tool.py": "def main():\n    step()\n\ndef step():\n    pass\n",
    })
    roots = {r["id"]: r for r in d["flowRoots"]}
    assert roots["web.py::index"]["kind"] == "route"
    assert roots["tool.py::main"]["kind"] == "console"
    assert d["flowRoots"][0]["kind"] == "console"


def test_inference_is_capped(tmp_path):
    cls = "".join(f"class C{i}:\n    def go(self):\n        pass\n" for i in range(5))
    d, f = build(tmp_path, {"a.py": cls + "def use(x):\n    x.go()\n"})
    assert callees(f["a.py"], "use") == {}


@needs_ts
def test_typescript_calls_and_symbols(tmp_path):
    d, f = build(tmp_path, {
        "src/index.ts": (
            "import { greet as hi } from './lib/greet';\nimport def from './lib/def';\nimport * as ns from './lib/ns';\n"
            "const { tool } = require('./lib/cjs');\n"
            "export class Svc {\n  run() { this.help(); hi(); def(); ns.go(); tool(); }\n  help() {}\n}\n"
            "export function main() { new Svc().run(); }\nmain();\n"),
        "src/lib/greet.ts": "export const greet = () => 1;\n",
        "src/lib/def.ts": "function d() {}\nexport default d;\n",
        "src/lib/ns.ts": "export function go() {}\n",
        "src/lib/cjs.js": "function tool() {}\nmodule.exports = { tool };\n",
    })
    idx = f["src/index.ts"]
    assert idx["analyzer"] == "tree-sitter"
    run = callees(idx, "Svc.run")
    assert run == {
        "src/index.ts::Svc.help": "certain", "src/lib/greet.ts::greet": "certain",
        "src/lib/def.ts::d": "certain", "src/lib/ns.ts::go": "certain", "src/lib/cjs.js::tool": "certain"}
    assert callees(idx, "main") == {"src/index.ts::Svc": "certain", "src/index.ts::Svc.run": "inferred"}
    assert callees(idx, "<module>") == {"src/index.ts::main": "certain"}
    svc = next(s for s in idx["symbols"] if s["qual"] == "Svc")
    assert (svc["line"], svc["end_line"]) == (5, 8)


def test_regex_fallback_has_no_call_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(javascript_ts, "AVAILABLE", False)
    d, f = build(tmp_path, {"a.js": "function a() { b(); }\nfunction b() {}\n"})
    assert f["a.js"]["analyzer"] == "regex" and f["a.js"]["hasCalls"] is False
    assert [s["name"] for s in f["a.js"]["symbols"]] == ["a", "b"]
