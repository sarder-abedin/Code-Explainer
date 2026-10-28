"""The step-by-step graph of one function: its branches, loops, error paths and early exits."""
import ast
import sys

import pytest

from code_explainer import flowgraph


def build(code, name="f"):
    tree = ast.parse(code)
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return flowgraph.build_python(fn, code.splitlines())


def by_kind(g, kind):
    return [n for n in g["nodes"] if n["kind"] == kind]


def out(g, node):
    return [(e["label"], g["nodes"][e["to"]]) for e in g["edges"] if e["from"] == node["id"]]


def one(g, kind):
    found = by_kind(g, kind)
    assert len(found) == 1, f"expected one {kind}, got {[n['kind'] for n in g['nodes']]}"
    return found[0]


def reachable(g):
    adj = {}
    for e in g["edges"]:
        adj.setdefault(e["from"], []).append(e["to"])
    seen, todo = {0}, [0]
    while todo:
        for t in adj.get(todo.pop(), []):
            if t not in seen:
                seen.add(t)
                todo.append(t)
    return seen


# ----------------------------------------------------------------- what gets a chart at all
def test_a_function_with_no_branching_has_no_chart():
    assert build("def f(a):\n    b = a + 1\n    print(b)\n    return b\n") is None


def test_a_function_with_a_single_decision_does_get_one():
    assert build("def f(a):\n    if a:\n        return 1\n    return 2\n") is not None


def test_every_graph_starts_at_node_zero_and_all_nodes_are_reachable():
    g = build("def f(xs):\n    t = 0\n    for x in xs:\n        if x:\n            t += x\n    return t\n")
    assert g["nodes"][0]["kind"] == "start" and reachable(g) == {n["id"] for n in g["nodes"]}


# ----------------------------------------------------------------- if / elif / else
IF_ELSE = "def f(x):\n    if x > 0:\n        a = 1\n    else:\n        a = 2\n    return a\n"


def test_an_if_else_has_two_labelled_ways_that_meet_again():
    g = build(IF_ELSE)
    d = one(g, "decision")
    ways = dict(out(g, d))
    assert set(ways) == {"yes", "no"} and ways["yes"]["line"] == 3 and ways["no"]["line"] == 5
    ret = one(g, "return")
    into_ret = [e for e in g["edges"] if e["to"] == ret["id"]]
    assert len(into_ret) == 2                                       # both branches continue to the return


def test_an_if_without_else_skips_straight_on_when_the_answer_is_no():
    g = build("def f(x):\n    if x:\n        log(x)\n    return x\n")
    d = one(g, "decision")
    ways = dict(out(g, d))
    assert ways["no"]["kind"] == "return" and ways["yes"]["kind"] == "step"


def test_elif_chains_into_a_second_decision_on_the_no_path():
    g = build("def f(x):\n    if x > 1:\n        return 'big'\n    elif x > 0:\n        return 'small'\n    else:\n        return 'none'\n")
    ds = by_kind(g, "decision")
    assert [d["line"] for d in ds] == [2, 4]
    assert [d["code"] for d in ds] == ["if x > 1:", "elif x > 0:"]
    assert dict(out(g, ds[0]))["no"]["id"] == ds[1]["id"]
    assert len(by_kind(g, "return")) == 3 and by_kind(g, "end") == []


def test_an_early_return_is_a_dead_end_and_the_code_after_it_hangs_off_the_no_path():
    g = build("def f(x):\n    if not x:\n        return None\n    y = x * 2\n    return y\n")
    early = by_kind(g, "return")[0]
    assert out(g, early) == []
    assert dict(out(g, one(g, "decision")))["no"]["line"] == 4


def test_falling_off_the_end_gets_an_end_node_but_returns_do_not():
    g = build("def f(x):\n    if x:\n        print(x)\n")
    assert len(by_kind(g, "end")) == 1
    g2 = build("def f(x):\n    if x:\n        return 1\n    return 2\n")
    assert by_kind(g2, "end") == []


# ----------------------------------------------------------------- loops
def test_a_for_loop_goes_round_and_then_on():
    g = build("def f(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t\n")
    loop = one(g, "loop")
    ways = dict(out(g, loop))
    assert set(ways) == {"each item", "done"} and ways["each item"]["line"] == 4 and ways["done"]["kind"] == "return"
    assert any(e["to"] == loop["id"] and g["nodes"][e["from"]]["line"] == 4 for e in g["edges"])      # the body loops back


def test_a_while_loop_asks_its_question_each_time_round():
    g = build("def f(n):\n    while n > 0:\n        n -= 1\n    return n\n")
    loop = one(g, "loop")
    assert set(dict(out(g, loop))) == {"yes", "no"} and loop["code"].startswith("while n > 0")
    body = dict(out(g, loop))["yes"]
    assert [t["id"] for _, t in out(g, body)] == [loop["id"]]         # and the body goes back to ask again


def test_break_leaves_the_loop_and_continue_goes_round_again():
    g = build("def f(xs):\n    for x in xs:\n        if x < 0:\n            break\n        if x == 0:\n            continue\n        use(x)\n    return 1\n")
    loop = one(g, "loop")
    ret = one(g, "return")
    brk = next(n for n in g["nodes"] if n["line"] == 4)
    cont = next(n for n in g["nodes"] if n["line"] == 6)
    assert [t["id"] for _, t in out(g, brk)] == [ret["id"]]          # break lands on the code after the loop
    assert [t["id"] for _, t in out(g, cont)] == [loop["id"]]        # continue returns to the loop


def test_the_else_of_a_loop_runs_on_the_done_path():
    g = build("def f(xs):\n    for x in xs:\n        if x:\n            break\n    else:\n        return 'none'\n    return 'found'\n")
    loop = one(g, "loop")
    assert dict(out(g, loop))["done"]["line"] == 6


# ----------------------------------------------------------------- errors
TRY = ("def f(p):\n    try:\n        data = read(p)\n    except FileNotFoundError:\n        return None\n"
       "    except Exception as e:\n        log(e)\n        raise\n    else:\n        ok(data)\n    finally:\n        close()\n    return data\n")


def test_try_has_an_error_path_per_except_clause():
    g = build(TRY)
    t = one(g, "try")
    ways = out(g, t)
    assert [l for l, _ in ways if l.startswith("on error")] == ["on error", "on error"]
    handlers = by_kind(g, "handler")
    assert [h["code"] for h in handlers] == ["except FileNotFoundError", "except Exception as e"]


def test_raise_ends_that_path_and_finally_is_shared():
    g = build(TRY)
    assert len(by_kind(g, "raise")) == 1 and out(g, by_kind(g, "raise")[0]) == []
    fin = next(n for n in g["nodes"] if n["line"] == 12)
    assert any(g["nodes"][e["from"]]["line"] in (10, 3, 7) for e in g["edges"] if e["to"] == fin["id"])


def test_the_else_block_of_a_try_follows_the_good_path():
    g = build(TRY)
    ok = next(n for n in g["nodes"] if n["line"] == 10)
    assert any(g["nodes"][e["from"]]["kind"] in ("try", "step") and g["nodes"][e["from"]]["line"] in (2, 3) for e in g["edges"] if e["to"] == ok["id"])


def test_with_blocks_are_a_step_and_carry_on_inside():
    g = build("def f(p):\n    with open(p) as fh:\n        data = fh.read()\n    if data:\n        return data\n    return ''\n")
    assert by_kind(g, "step")[0]["say"]["plain"].startswith("Open")
    assert len(by_kind(g, "decision")) == 1


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match needs Python 3.10")
def test_match_has_one_way_out_per_case():
    code = "def f(c):\n    match c:\n        case 'a':\n            return 1\n        case 'b':\n            x = 2\n        case _:\n            x = 3\n    return x\n"
    g = build(code)
    m = one(g, "branch")
    assert sorted(l for l, _ in out(g, m)) == ["'a'", "'b'", "_"]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match needs Python 3.10")
def test_a_match_without_a_catch_all_has_a_way_out_for_no_match():
    g = build("def f(c):\n    match c:\n        case 1:\n            x = 1\n        case 2:\n            x = 2\n    return c\n")
    m = one(g, "branch")
    assert sorted(l for l, _ in out(g, m)) == ["1", "2", "no match"]


def test_inner_functions_are_a_single_step_not_expanded():
    g = build("def f(x):\n    def helper(y):\n        if y:\n            return 1\n        return 2\n    if x:\n        return helper(x)\n    return 0\n")
    assert len(by_kind(g, "decision")) == 1
    assert any(n["say"]["plain"] == "Define `helper`" for n in g["nodes"])


def test_async_functions_work_and_await_is_named():
    g = build("async def f(x):\n    if x:\n        await send(x)\n    return x\n")
    assert g is not None and any("Wait for" in n["say"]["plain"] for n in by_kind(g, "step"))


# ----------------------------------------------------------------- the words
@pytest.mark.parametrize("cond,phrase", [
    ("x > 0", "`x` is more than 0"),
    ("x >= 1", "`x` is at least 1"),
    ("x < limit", "`x` is less than `limit`"),
    ("x == 'a'", "`x` equals 'a'"),
    ("x != 3", "`x` is not 3"),
    ("x is None", "`x` is missing (None)"),
    ("x is not None", "`x` is set"),
    ("x in ys", "`x` is in `ys`"),
    ("x not in ys", "`x` is not in `ys`"),
    ("items", "`items` has something in it"),
    ("not items", "`items` is empty"),
    ("not x > 1", "`x` is at most 1"),
    ("a and b", "`a` has something in it and `b` has something in it"),
    ("isinstance(x, str)", "`x` is a `str`"),
    ("ready()", "`ready()` is true"),
])
def test_conditions_are_put_into_words(cond, phrase):
    g = build(f"def f(x, items, ys, a, b, limit, ready):\n    if {cond}:\n        return 1\n    return 2\n")
    d = one(g, "decision")
    assert d["say"]["plain"] == phrase + "?" and d["say"]["kid"] == phrase + "?"
    assert d["code"] == f"if {cond}:"


def test_the_code_is_kept_verbatim_for_the_technical_level():
    g = build("def f(x):\n    if x > 0 and x < 10:\n        return x\n    return 0\n")
    assert one(g, "decision")["code"] == "if x > 0 and x < 10:"


def test_steps_describe_what_they_do_differently_for_beginners():
    g = build("def f(xs):\n    total = 0\n    log(xs)\n    for x in xs:\n        total += x\n    return total\n")
    first = by_kind(g, "step")[0]
    assert first["say"]["plain"] == "Set `total`, then call `log`"
    assert first["say"]["kid"] == "Remember a value called `total`, then ask `log` to do its job"
    ret = one(g, "return")
    assert ret["say"]["plain"] == "Return `total`" and ret["say"]["kid"] == "Hand back `total`"
    assert one(g, "loop")["say"]["kid"] == "Take each `x` from `xs`, one by one"


def test_a_long_run_of_statements_is_summarised():
    body = "".join(f"    a{i} = {i}\n" for i in range(6))
    g = build("def f(x):\n" + body + "    if x:\n        return 1\n    return 2\n")
    first = by_kind(g, "step")[0]
    assert first["say"]["plain"] == "Set `a0`, then set `a1`, then set `a2`, then 3 more steps"
    assert first["line"] == 2 and first["end"] == 7


def test_node_text_is_short_and_free_of_control_characters():
    g = build("def f(x):\n    if " + " and ".join(f"cond_number_{i}(x)" for i in range(12)) + ":\n        return 1\n    return 2\n")
    for n in g["nodes"]:
        assert len(n["code"]) <= 90 and all(c.isprintable() for c in n["code"] + n["say"]["plain"] + n["say"]["kid"])


# ----------------------------------------------------------------- line numbers and size
def test_every_node_knows_its_lines():
    g = build(IF_ELSE)
    for n in g["nodes"]:
        if n["kind"] not in ("start", "end"):
            assert n["line"] >= 2 and n["end"] >= n["line"]


def test_a_huge_function_is_cut_off_with_a_note():
    body = "".join(f"    if x == {i}:\n        a = {i}\n" for i in range(60))
    g = build("def f(x):\n" + body + "    return 0\n")
    assert g["truncated"] is True and len(g["nodes"]) <= flowgraph.MAX_NODES + 2
    assert len(by_kind(g, "more")) == 1 and "more" in by_kind(g, "more")[0]["say"]["plain"]


def test_small_functions_are_not_marked_truncated():
    assert build(IF_ELSE)["truncated"] is False


def test_the_graph_is_plain_json():
    import json
    g = build(TRY)
    assert json.loads(json.dumps(g)) == g
