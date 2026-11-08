"""What the model is shown for a box of a flowchart, a part of the architecture map, and an arrow between two parts."""
import pytest

from code_explainer.ai.tasks import TaskError, build_task
from code_explainer.core import analyze_repo
from test_ai_tasks import SHOP

PKG = {
    "app/__init__.py": "",
    "app/web/__init__.py": "",
    "app/web/views.py": '"""Pages."""\nfrom app.core import pricing\nfrom app.core.pricing import total\n\n\ndef home():\n    return total([1])\n',
    "app/core/__init__.py": "",
    "app/core/pricing.py": '"""Price maths."""\n\n\ndef total(items):\n    if not items:\n        return 0\n    return sum(items)\n',
    "app/db/__init__.py": "",
    "app/db/store.py": 'import sqlite3\n\n\ndef load():\n    return sqlite3.connect("x")\n',
    "app/core/orders.py": "from app.db import store\n\n\ndef order():\n    return store.load()\n",
}


def _build(tmp, files):
    for rel, text in files.items():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text(text)
    return analyze_repo(tmp, "app")


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("shop"), SHOP)


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("pkg"), PKG)


def task(data, budget=4000, **req):
    return build_task(data, req, budget_tokens=budget)


def node(data, path, symbol, kind):
    f = next(f for f in data["files"] if f["path"] == path)
    s = next(s for s in f["symbols"] if s["qual"] == symbol)
    return next(n["id"] for n in s["flow"]["nodes"] if n["kind"] == kind)


# ----------------------------------------------------------------- a box of a flowchart
def test_a_flowchart_box_task_shows_that_boxs_code_and_where_each_way_out_leads(shop):
    nid = node(shop, "shop/pricing.py", "total", "decision")
    t = task(shop, kind="flowstep", path="shop/pricing.py", symbol="total", node=nid)
    assert t.kind == "flowstep" and t.target == f"shop/pricing.py::total#{nid}" and "total" in t.title
    assert "if item.price > 0:" in t.context and "   8|" in t.context
    assert "decision" in t.context and "yes" in t.context                               # the label of the way out
    assert "result += item.price" in t.context                                          # where "yes" leads
    assert "`total`" in t.message and "shop/pricing.py" in t.message and "path:line" in t.message
    assert t.sources[0]["line"] == 8


def test_a_flowchart_box_task_also_shows_the_function_around_it(shop):
    nid = node(shop, "shop/pricing.py", "total", "return")
    t = task(shop, kind="flowstep", path="shop/pricing.py", symbol="total", node=nid)
    assert "for item in items:" in t.context and "return result" in t.context


def test_calls_made_in_the_box_are_named(tmp_path):
    data = _build(tmp_path, {"m.py": "def helper(x):\n    return x\n\n\ndef run(items):\n    if not items:\n        return 0\n    return helper(items)\n"})
    f = next(f for f in data["files"] if f["path"] == "m.py")
    flow = next(s for s in f["symbols"] if s["qual"] == "run")["flow"]
    nid = [n["id"] for n in flow["nodes"] if n["kind"] == "return"][-1]
    t = task(data, kind="flowstep", path="m.py", symbol="run", node=nid)
    assert "calls here: helper" in t.context


@pytest.mark.parametrize("bad", [None, "1", -1, 99, 1.5, True])
def test_a_flowchart_box_must_be_a_real_box(shop, bad):
    with pytest.raises(TaskError):
        task(shop, kind="flowstep", path="shop/pricing.py", symbol="total", node=bad)


def test_the_start_box_has_nothing_to_explain(shop):
    with pytest.raises(TaskError, match="nothing"):
        task(shop, kind="flowstep", path="shop/pricing.py", symbol="total", node=0)


def test_a_function_without_a_flowchart_or_a_missing_function_is_refused(shop):
    with pytest.raises(TaskError):
        task(shop, kind="flowstep", path="shop/pricing.py", symbol="nope", node=1)
    with pytest.raises(TaskError):
        task(shop, kind="flowstep", path="shop/cart.py", symbol="Cart", node=1)               # a class has no flowchart


def test_the_same_box_gives_the_same_prompt(shop):
    a = task(shop, kind="flowstep", path="shop/pricing.py", symbol="total", node=3)
    b = task(shop, kind="flowstep", path="shop/pricing.py", symbol="total", node=3)
    assert a.content == b.content


# ----------------------------------------------------------------- a part of the map
def part_ids(data):
    return [c["id"] for c in data["architecture"]["components"]]


def test_a_part_task_describes_the_part_its_files_and_its_neighbours(pkg):
    ids = part_ids(pkg)
    core = next(i for i in ids if i.endswith("core"))
    t = task(pkg, kind="part", part=core)
    assert t.kind == "part" and t.target == core and "core" in t.title
    assert "Facts about this part" in t.context and "app/core/pricing.py" in t.context
    assert "used by" in t.context and "web" in t.context                                  # the views import it
    assert "uses" in t.context and "db" in t.context                                      # orders.py imports the store
    assert "path:line" in t.message and "core" in t.message


def test_a_part_task_shows_the_start_of_its_main_files_as_code(pkg):
    core = next(i for i in part_ids(pkg) if i.endswith("core"))
    t = task(pkg, kind="part", part=core)
    assert "### app/core/pricing.py:1-" in t.context and "def total(items):" in t.context
    assert any(s["path"] == "app/core/pricing.py" for s in t.sources)


def test_a_part_that_talks_to_the_outside_says_to_what(pkg):
    db = next(i for i in part_ids(pkg) if i.endswith("db"))
    t = task(pkg, kind="part", part=db)
    assert "database" in t.context.lower()


def test_an_unknown_part_is_refused(pkg):
    with pytest.raises(TaskError, match="part"):
        task(pkg, kind="part", part="nope")
    with pytest.raises(TaskError):
        task(pkg, kind="part")


# ----------------------------------------------------------------- an arrow between two parts
def test_an_arrow_task_counts_the_imports_and_shows_the_lines_that_make_them(pkg):
    ids = part_ids(pkg)
    web, core = next(i for i in ids if i.endswith("web")), next(i for i in ids if i.endswith("core"))
    t = task(pkg, kind="link", **{"from": web, "to": core})
    assert t.kind == "link" and t.target == f"{web}->{core}" and "web" in t.title and "core" in t.title
    assert "imports" in t.context and "from app.core import pricing" in t.context and "app/web/views.py" in t.context
    assert "path:line" in t.message


def test_an_arrow_that_is_part_of_a_cycle_says_so(pkg):
    for e in pkg["architecture"]["edges"]:
        e["cycle"] = True
        break
    e = pkg["architecture"]["edges"][0]
    t = task(pkg, kind="link", **{"from": e["from"], "to": e["to"]})
    assert "circular" in t.context.lower()
    e["cycle"] = False


def test_a_pair_with_no_arrow_is_refused(pkg):
    ids = part_ids(pkg)
    with pytest.raises(TaskError, match="no arrow"):
        task(pkg, kind="link", **{"from": ids[0], "to": "nope"})
    with pytest.raises(TaskError):
        task(pkg, kind="link", **{"from": ids[0]})


def test_the_prompt_stays_within_a_small_budget(pkg):
    core = next(i for i in part_ids(pkg) if i.endswith("core"))
    from code_explainer.ai.prompts import estimate_tokens
    t = task(pkg, budget=300, kind="part", part=core)
    assert estimate_tokens(t.context) <= 300


# ----------------------------------------------------------------- the details that make a part worth explaining
RICH = {
    "app/__init__.py": "",
    "app/web/__init__.py": "",
    "app/web/main.py": '"""Pages."""\nfrom app.core import c0\n\n\ndef run(x):\n    return eval(x)\n\n\nif __name__ == "__main__":\n    run("1")\n',
    "app/core/__init__.py": "",
    **{f"app/core/c{i}.py": f"from app.web import main\n\n\ndef f{i}(x):\n    if x:\n        return {i}\n    return 0\n" for i in range(6)},
    "app/db/__init__.py": "",
    "app/db/store.py": "def load():\\n    return 1\\n",
    "tests/test_core.py": "from app.core import c0\n\n\ndef test_c0():\n    assert c0.f0(1) == 0\n",
}


@pytest.fixture(scope="module")
def rich(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("rich"), RICH)


def part(data, suffix):
    return next(c["id"] for c in data["architecture"]["components"] if c["id"].endswith(suffix))


def test_a_part_says_when_it_holds_the_entry_point_has_docs_findings_tests_and_a_cycle(rich):
    t = task(rich, kind="part", part=part(rich, "web"))
    assert "entry point" in t.context and "what its own docs say: Pages." in t.context
    assert "circular dependency" in t.context and "safety notes: 1" in t.context
    core = task(rich, kind="part", part=part(rich, "core"))
    assert "tests: " in core.context and "functions are reached by a test" in core.context


def test_a_part_without_any_of_those_leaves_them_out(pkg):
    t = task(pkg, kind="part", part=part(pkg, "db"))
    for words in ("entry point", "what its own docs say", "circular", "safety notes"):
        assert words not in t.context


def test_only_the_first_few_files_of_a_part_are_shown_as_code(rich):
    t = task(rich, budget=8000, kind="part", part=part(rich, "core"))
    assert len([s for s in t.sources if s["path"].startswith("app/core/")]) == 3


def test_only_a_few_examples_of_an_arrow_are_listed_and_shown(rich):
    t = task(rich, budget=8000, kind="link", **{"from": part(rich, "core"), "to": part(rich, "web")})
    listed = t.context.split("example links: ")[1].split("\n")[0]
    assert listed.count(" -> ") == 4 and len(t.sources) <= 4


def test_a_file_that_imports_the_same_thing_many_times_shows_a_few_lines(tmp_path):
    many = "".join(f"from app.core import c0 as a{i}\n" for i in range(7)) + "\n\ndef g():\n    return 1\n"
    data = _build(tmp_path, {"app/__init__.py": "", "app/web/__init__.py": "", "app/web/m.py": many, "app/core/__init__.py": "", "app/core/c0.py": "def f():\n    return 1\n",
                              "app/db/__init__.py": "", "app/db/s.py": "def l():\n    return 1\n"})
    t = task(data, budget=8000, kind="link", **{"from": part(data, "web"), "to": part(data, "core")})
    assert len(t.sources) == 4


def test_a_guessed_call_is_not_listed_as_a_call_in_the_box(tmp_path):
    data = _build(tmp_path, {"m.py": "class K:\n    def m(self):\n        return 1\n\n\ndef run(o, x):\n    if x:\n        return 0\n    return o.m()\n"})
    f = next(f for f in data["files"] if f["path"] == "m.py")
    flow = next(s for s in f["symbols"] if s["qual"] == "run")["flow"]
    nid = [n["id"] for n in flow["nodes"] if n["kind"] == "return"][-1]
    assert "calls here" not in task(data, kind="flowstep", path="m.py", symbol="run", node=nid).context


def test_a_call_elsewhere_in_the_function_is_not_listed_for_this_box(tmp_path):
    data = _build(tmp_path, {"m.py": "def helper(x):\n    return x\n\n\ndef run(items):\n    if not items:\n        return 0\n    return helper(items)\n"})
    f = next(f for f in data["files"] if f["path"] == "m.py")
    flow = next(s for s in f["symbols"] if s["qual"] == "run")["flow"]
    first = [n["id"] for n in flow["nodes"] if n["kind"] == "return"][0]
    assert "calls here" not in task(data, kind="flowstep", path="m.py", symbol="run", node=first).context
