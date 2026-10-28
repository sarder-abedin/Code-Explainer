import pytest

from code_explainer.ai.prompts import estimate_tokens
from code_explainer.ai.tasks import TEMPLATE_VERSION, TaskError, build_task
from code_explainer.core import analyze_repo

SHOP = {
    "shop/__init__.py": "",
    "shop/cli.py": '"""Command line front door."""\nfrom shop.pricing import total\nfrom shop import cart\n\n\ndef main():\n    """Print the order total."""\n    items = cart.items()\n    return total(items)\n\n\nif __name__ == "__main__":\n    main()\n',
    "shop/pricing.py": ('"""Price maths."""\n\n\ndef total(items):\n    """Add up the price of every item, skipping negative prices."""\n    result = 0\n'
                        "    for item in items:\n        if item.price > 0:\n            result += item.price\n    return result\n"),
    "shop/cart.py": '"""The shopping cart."""\n\n\nclass Cart:\n    """Holds the items."""\n\n    def add(self, item):\n        """Put an item in."""\n        return item\n\n    def clear(self):\n        """Empty it."""\n        return None\n\n\ndef items():\n    """The things in the cart."""\n    return []\n',
    "README.md": "# Shop\n\nA tiny shop.\n\n## Install\n\nRun `pip install shop`.\n",
}


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    root = tmp_path_factory.mktemp("shop")
    for rel, text in SHOP.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return analyze_repo(root, "shop")


def task(data, budget=4000, **req):
    return build_task(data, req, budget_tokens=budget)


# ----------------------------------------------------------------- symbols
def test_a_function_task_has_its_code_facts_and_the_code_it_calls(data):
    t = task(data, kind="symbol", path="shop/cli.py", symbol="main")
    assert t.kind == "symbol" and t.target == "shop/cli.py::main" and "main" in t.title
    assert "`main`" in t.message and "shop/cli.py" in t.message
    assert "### shop/cli.py:6-9 — main" in t.context and "   9|     return total(items)" in t.context
    assert "calls: items, total" in t.context                                         # in the order the calls appear
    assert "called by" in t.context
    assert "(called by main)" in t.context and "result += item.price" in t.context     # the helper's code comes along
    assert t.sources[0]["label"] == "main" and {s["label"] for s in t.sources} >= {"main", "total", "items"}


def test_callers_are_named_including_a_scripts_own_top_level_code(data):
    t = task(data, kind="symbol", path="shop/cli.py", symbol="main")
    assert "called by: the top-level code of shop/cli.py" in t.context                  # the `if __name__ == "__main__"` block
    t2 = task(data, kind="symbol", path="shop/pricing.py", symbol="total")
    assert "called by: main (shop/cli.py)" in t2.context and "calls nothing else" in t2.context


def test_a_function_nobody_calls_says_so(data):
    t = task(data, kind="symbol", path="shop/cart.py", symbol="Cart.clear")
    assert "no caller found" in t.context


def test_a_class_task_shows_the_header_and_lists_its_methods(data):
    t = task(data, kind="symbol", path="shop/cart.py", symbol="Cart")
    assert "class Cart" in t.context and "Cart.add" in t.context and "Cart.clear" in t.context and "Put an item in." in t.context


def test_a_method_can_be_named_by_its_qualified_name(data):
    t = task(data, kind="symbol", path="shop/cart.py", symbol="Cart.add")
    assert "def add" in t.context and t.target == "shop/cart.py::Cart.add"


# ----------------------------------------------------------------- files
def test_a_small_file_is_sent_whole_with_its_connections(data):
    t = task(data, kind="file", path="shop/cli.py")
    assert t.target == "shop/cli.py" and "   1| \"\"\"Command line front door.\"\"\"" in t.context and "   9|     return total(items)" in t.context
    assert "imports: shop/cart.py, shop/pricing.py" in t.context and "entry point" in t.context.lower()
    t2 = task(data, kind="file", path="shop/pricing.py")
    assert "imported by: shop/cli.py" in t2.context


def test_a_big_file_gets_an_outline_instead_of_everything(tmp_path):
    body = "".join(f'def fn_{i}(x):\n    """Does thing {i}."""\n' + "".join(f"    x += {j}\n" for j in range(8)) + "    return x\n\n\n" for i in range(60))
    (tmp_path / "big.py").write_text(body)
    d = analyze_repo(tmp_path, "x")
    t = task(d, budget=1500, kind="file", path="big.py")
    assert "fn_0" in t.context and "fn_59" in t.context and "Does thing 59." in t.context       # the outline covers all of it
    assert "first 60 lines" in t.context and estimate_tokens(t.context) <= 1500
    assert "    x += 7" in t.context                                                          # and the start is shown in full


def test_a_documentation_file_can_be_explained(data):
    t = task(data, kind="file", path="README.md")
    assert "pip install shop" in t.context and "   1| # Shop" in t.context


# ----------------------------------------------------------------- flow steps
def test_a_flow_step_knows_who_called_it_and_where(data):
    t = task(data, kind="flow", path="shop/pricing.py", symbol="total", parent="shop/cli.py::main")
    assert t.kind == "flow" and "main" in t.message and "total" in t.message
    assert "### shop/cli.py:" in t.context and "return total(items)" in t.context           # the call site, in the caller
    assert "line 9" in t.context.lower() and "result += item.price" in t.context
    assert {s["label"] for s in t.sources} >= {"total", "main"}


def test_a_step_called_from_a_scripts_top_level_code_reads_naturally(data):
    t = task(data, kind="flow", path="shop/cli.py", symbol="main", parent="shop/cli.py::<module>")
    assert "It was called by the top-level code of shop/cli.py at line 13." in t.message and "`the top-level" not in t.message
    assert "### shop/cli.py:" in t.context and "main()" in t.context


def test_a_flow_step_with_an_unknown_parent_still_works(data):
    t = task(data, kind="flow", path="shop/pricing.py", symbol="total", parent="nope.py::x")
    assert "result += item.price" in t.context and "nope.py" not in t.context


# ----------------------------------------------------------------- changes
def test_a_change_task_has_the_patch_the_symbols_and_the_blast_radius(repo):
    from code_explainer.core import analyze_repo as an
    d = an(repo, "r", base="HEAD~1", head="HEAD")
    t = build_task(d, {"kind": "change", "path": "app/lib.py"}, budget_tokens=4000)
    assert t.kind == "change" and "app/lib.py" in t.message
    assert "@@" in t.context and "+def fresh" in t.context
    assert "added: fresh" in t.context and "modified: load" in t.context
    assert "app/main.py" in t.context                                                       # who is affected
    assert "def fresh" in t.context and any(s["label"] == "fresh" for s in t.sources)
    gone = build_task(d, {"kind": "change", "path": "app/legacy.py"}, budget_tokens=4000)
    assert "removed" in gone.message.lower() and "-def old" in gone.context


def test_change_tasks_need_a_loaded_review_and_a_changed_file(data, repo):
    with pytest.raises(TaskError, match="no change review"):
        task(data, kind="change", path="shop/cli.py")
    from code_explainer.core import analyze_repo as an
    d = an(repo, "r", base="HEAD~1", head="HEAD")
    with pytest.raises(TaskError, match="not part of"):
        build_task(d, {"kind": "change", "path": "tests/test_lib.py"}, budget_tokens=4000)


# ----------------------------------------------------------------- validation
@pytest.mark.parametrize("req, match", [
    ({}, "kind"), ({"kind": "bogus", "path": "shop/cli.py"}, "kind"), ({"kind": "file"}, "path"),
    ({"kind": "file", "path": 5}, "path"), ({"kind": "file", "path": "nope.py"}, "not in this project"),
    ({"kind": "file", "path": "../../etc/passwd"}, "not in this project"), ({"kind": "file", "path": "x" * 10000}, "path"),
    ({"kind": "symbol", "path": "shop/cli.py"}, "symbol"), ({"kind": "symbol", "path": "shop/cli.py", "symbol": "nope"}, "no symbol"),
    ({"kind": "symbol", "path": "shop/cli.py", "symbol": 7}, "symbol"), ({"kind": "symbol", "path": "shop/cli.py", "symbol": "<module>"}, "no symbol"),
])
def test_bad_requests_are_rejected_with_a_clear_message(data, req, match):
    with pytest.raises(TaskError, match=match):
        build_task(data, req, budget_tokens=3000)


def test_non_dict_requests_are_rejected(data):
    for bad in (None, "x", 5, []):
        with pytest.raises(TaskError):
            build_task(data, bad, budget_tokens=3000)


# ----------------------------------------------------------------- budget and stability
@pytest.mark.parametrize("kind, extra", [("file", {"path": "shop/cart.py"}), ("symbol", {"path": "shop/cli.py", "symbol": "main"})])
def test_the_context_always_fits_the_budget(data, kind, extra):
    for budget in (120, 300, 800, 3000):
        t = task(data, budget=budget, kind=kind, **extra)
        assert estimate_tokens(t.context) <= budget, (budget, estimate_tokens(t.context))
        assert t.context and t.sources                                                    # something useful always survives


def test_a_tiny_budget_is_an_error_not_an_empty_prompt(data):
    with pytest.raises(TaskError, match="too small"):
        task(data, budget=20, kind="file", path="shop/cli.py")


def test_tasks_are_deterministic_and_content_tracks_the_code(data, tmp_path):
    a = task(data, kind="symbol", path="shop/pricing.py", symbol="total")
    b = task(data, kind="symbol", path="shop/pricing.py", symbol="total")
    assert a.context == b.context and a.message == b.message and a.content == b.content
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(SHOP["shop/pricing.py"].replace("item.price > 0", "item.price > 1"))
    d2 = analyze_repo(tmp_path, "shop")
    c = build_task(d2, {"kind": "symbol", "path": "shop/pricing.py", "symbol": "total"}, budget_tokens=4000)
    assert c.content != a.content                                                         # an edit changes what the cache keys on


def test_template_version_is_a_positive_int():
    assert isinstance(TEMPLATE_VERSION, int) and TEMPLATE_VERSION >= 1


# ----------------------------------------------------------------- tests and safety facts
@pytest.fixture(scope="module")
def checked(tmp_path_factory):
    root = tmp_path_factory.mktemp("checked")
    files = {**SHOP, "tests/test_pricing.py": "from shop.pricing import total\n\n\ndef test_total():\n    assert total([]) == 0\n",
             "shop/danger.py": "def run(cmd):\n    return eval(cmd)\n", "tests/test_eval.py": "def test_e():\n    eval('1')\n"}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return analyze_repo(root, "shop")


def test_a_function_task_says_whether_a_test_reaches_it(checked):
    assert "no test was seen reaching it" not in task(checked, kind="symbol", path="shop/pricing.py", symbol="total").context
    assert "no test was seen reaching it" in task(checked, kind="symbol", path="shop/cart.py", symbol="items").context


def test_a_file_task_carries_the_test_state_and_safety_notes(checked):
    ctx = task(checked, kind="file", path="shop/pricing.py").context
    assert "Tests (static estimate): fully tested" in ctx
    cart = task(checked, kind="file", path="shop/cart.py").context
    assert "no test was seen reaching it" in cart
    danger = task(checked, kind="file", path="shop/danger.py").context
    assert "Safety notes: Runs text as code (eval) (line 2)" in danger and "Safety notes" not in ctx
    assert "Safety notes" not in task(checked, kind="file", path="tests/test_eval.py").context      # test code is not held to the same bar


def test_a_change_task_lists_changed_code_no_test_reaches(repo):
    d = analyze_repo(repo, "demo", base="HEAD~1")
    ctx = build_task(d, {"kind": "change", "path": "app/lib.py"}, 4000).context
    assert "changed code that no test was seen reaching: fresh" in ctx
