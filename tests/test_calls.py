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


def test_python_dotted_package_chains_and_class_methods(tmp_path):
    d, f = build(tmp_path, {
        "src/shop/__init__.py": "",
        "src/shop/cart/__init__.py": "",
        "src/shop/cart/items.py": "def add():\n    pass\n\nclass Box:\n    def open(self):\n        pass\n    def shut(self):\n        self.open()\n",
        "main.py": (
            "import shop.cart.items\nfrom shop.cart import items as it\nfrom shop.cart.items import Box\n\n"
            "class Local:\n    def go(self):\n        pass\n    def run(self):\n        self.go()\n        Local.go(self)\n\n"
            "def use():\n    shop.cart.items.add()\n    it.add()\n    Box.open(None)\n    it.Box.open(None)\n    Local.go(None)\n"),
    })
    use = callees(f["main.py"], "use")
    assert use == {"src/shop/cart/items.py::add": "certain", "src/shop/cart/items.py::Box.open": "certain",
                   "main.py::Local.go": "certain"}
    assert callees(f["main.py"], "Local.run") == {"main.py::Local.go": "certain"}
    assert callees(f["src/shop/cart/items.py"], "Box.shut") == {"src/shop/cart/items.py::Box.open": "certain"}


def test_reexport_chain_and_unfollowable_imports(tmp_path):
    d, f = build(tmp_path, {
        "pkg/__init__.py": "from .impl import work\n",
        "pkg/impl.py": "def work():\n    pass\n",
        "user.py": "from pkg import work\nfrom missing_lib import thing\nimport requests\n\ndef go():\n    work()\n    thing()\n    requests.get('x')\n    unknown()\n",
    })
    assert callees(f["user.py"], "go") == {"pkg/impl.py::work": "certain"}   # followed through the re-export; externals ignored


def test_self_call_to_missing_method_falls_back_to_inference(tmp_path):
    d, f = build(tmp_path, {"a.py": "class A:\n    def x(self):\n        self.helper()\n\nclass B:\n    def helper(self):\n        pass\n"})
    assert callees(f["a.py"], "A.x") == {"a.py::B.helper": "inferred"}


def test_dunder_methods_are_never_inferred(tmp_path):
    d, f = build(tmp_path, {"a.py": "class A:\n    def __init__(self):\n        pass\n\ndef use(o):\n    o.__init__()\n"})
    assert callees(f["a.py"], "use") == {}


def test_certain_beats_inferred_for_the_same_target(tmp_path):
    d, f = build(tmp_path, {"a.py": "class A:\n    def go(self):\n        pass\n    def run(self, other):\n        other.go()\n        self.go()\n"})
    assert callees(f["a.py"], "A.run") == {"a.py::A.go": "certain"}


@needs_ts
def test_typescript_default_class_namespace_and_reexport(tmp_path):
    d, f = build(tmp_path, {
        "a.ts": "import Svc from './svc';\nimport { work } from './barrel';\nimport * as u from './util';\nexport function main() { Svc.make(); work(); u.helper(); new Svc(); }\n",
        "svc.ts": "export default class Svc { static make() {} }\n",
        "barrel.ts": "export { work } from './impl';\n",
        "impl.ts": "export function work() {}\n",
        "util.ts": "export function helper() {}\n",
    })
    main = callees(f["a.ts"], "main")
    assert main["svc.ts::Svc.make"] == "certain" and main["util.ts::helper"] == "certain" and main["svc.ts::Svc"] == "certain"
    assert "impl.ts::work" not in main  # barrel `export … from` re-exports are not followed (documented limit)
