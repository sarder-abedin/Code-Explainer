"""The same step-by-step graphs for JavaScript and TypeScript functions (needs tree-sitter)."""
import pytest

from code_explainer.analysis import analyze_file, javascript_ts

pytestmark = pytest.mark.skipif(not javascript_ts.AVAILABLE, reason="tree-sitter not installed")


def flow(code, name="f", path="a.js", lang="JavaScript"):
    info = analyze_file(path, lang, code)
    sym = next(s for s in info.symbols if s.qual == name)
    return sym.flow


def by_kind(g, kind):
    return [n for n in g["nodes"] if n["kind"] == kind]


def out(g, node):
    return [(e["label"], g["nodes"][e["to"]]) for e in g["edges"] if e["from"] == node["id"]]


def one(g, kind):
    found = by_kind(g, kind)
    assert len(found) == 1, [n["kind"] for n in g["nodes"]]
    return found[0]


# ----------------------------------------------------------------- what gets a chart
def test_straight_line_functions_and_expression_arrows_have_none():
    assert flow("function f(a) {\n  const b = a + 1;\n  return b;\n}\n") is None
    assert flow("const f = (a) => a + 1;\n") is None


def test_function_declarations_arrows_methods_and_typescript_all_get_charts():
    body = "{\n  if (x) { return 1; }\n  return 2;\n}\n"
    assert flow("function f(x) " + body) is not None
    assert flow("const f = (x) => " + body) is not None
    assert flow("class A {\n  f(x) " + body + "}\n", name="A.f") is not None
    assert flow("function f(x: number): number " + body, path="a.ts", lang="TypeScript") is not None
    assert flow("export async function f(x) " + body) is not None


# ----------------------------------------------------------------- branches
def test_if_else_has_yes_and_no_ways_that_meet_again():
    g = flow("function f(x) {\n  let a;\n  if (x > 0) {\n    a = 1;\n  } else {\n    a = 2;\n  }\n  return a;\n}\n")
    d = one(g, "decision")
    ways = dict(out(g, d))
    assert d["code"] == "if (x > 0)" and ways["yes"]["line"] == 4 and ways["no"]["line"] == 6
    assert len([e for e in g["edges"] if e["to"] == one(g, "return")["id"]]) == 2


def test_else_if_chains_on_the_no_path():
    g = flow("function f(x) {\n  if (x > 1) {\n    return 'big';\n  } else if (x > 0) {\n    return 'small';\n  } else {\n    return 'none';\n  }\n}\n")
    ds = by_kind(g, "decision")
    assert [d["code"] for d in ds] == ["if (x > 1)", "else if (x > 0)"]
    assert dict(out(g, ds[0]))["no"]["id"] == ds[1]["id"] and by_kind(g, "end") == []


def test_if_without_braces_and_early_return():
    g = flow("function f(x) {\n  if (!x) return null;\n  const y = x * 2;\n  return y;\n}\n")
    early = by_kind(g, "return")[0]
    assert out(g, early) == [] and dict(out(g, one(g, "decision")))["no"]["line"] == 3


# ----------------------------------------------------------------- loops
def test_for_of_and_for_in_loops_go_round_and_then_on():
    g = flow("function f(xs) {\n  let t = 0;\n  for (const x of xs) {\n    t += x;\n  }\n  return t;\n}\n")
    loop = one(g, "loop")
    ways = dict(out(g, loop))
    assert set(ways) == {"each item", "done"} and loop["say"]["plain"] == "For each `x` in `xs`"
    assert any(e["to"] == loop["id"] and g["nodes"][e["from"]]["line"] == 4 for e in g["edges"])
    g2 = flow("function f(o) {\n  for (const k in o) {\n    use(k);\n  }\n}\n")
    assert one(g2, "loop")["say"]["plain"] == "For each `k` in `o`"


def test_c_style_for_and_while_and_do_while_ask_their_question():
    g = flow("function f(n) {\n  for (let i = 0; i < n; i++) {\n    use(i);\n  }\n}\n")
    loop = one(g, "loop")
    assert set(dict(out(g, loop))) == {"yes", "no"} and loop["code"].startswith("for (")
    g2 = flow("function f(n) {\n  while (n > 0) {\n    n--;\n  }\n  return n;\n}\n")
    loop2 = one(g2, "loop")
    assert loop2["code"] == "while (n > 0)" and set(dict(out(g2, loop2))) == {"yes", "no"}
    g3 = flow("function f(n) {\n  do {\n    n--;\n  } while (n > 0);\n  return n;\n}\n")
    loop3 = one(g3, "loop")
    body = next(n for n in g3["nodes"] if n["line"] == 3)
    assert loop3["code"] == "do … while (n > 0)" and dict(out(g3, loop3))["yes"]["id"] == body["id"]


def test_break_and_continue():
    g = flow("function f(xs) {\n  for (const x of xs) {\n    if (x < 0) break;\n    if (x === 0) continue;\n    use(x);\n  }\n  return 1;\n}\n")
    loop, ret = one(g, "loop"), one(g, "return")
    brk = next(n for n in g["nodes"] if n["line"] == 3 and n["code"] == "break")
    cont = next(n for n in g["nodes"] if n["code"] == "continue")
    assert [t["id"] for _, t in out(g, brk)] == [ret["id"]] and [t["id"] for _, t in out(g, cont)] == [loop["id"]]


def test_continue_in_a_do_while_goes_to_its_question():
    g = flow("function f(n) {\n  do {\n    if (n) continue;\n    use(n);\n  } while (n > 0);\n}\n")
    loop = one(g, "loop")
    cont = next(n for n in g["nodes"] if n["code"] == "continue")
    assert [t["id"] for _, t in out(g, cont)] == [loop["id"]]


# ----------------------------------------------------------------- errors and switches
def test_try_catch_finally():
    g = flow("function f(p) {\n  try {\n    read(p);\n  } catch (e) {\n    log(e);\n    throw e;\n  } finally {\n    close();\n  }\n  return 1;\n}\n")
    t = one(g, "try")
    assert [l for l, _ in out(g, t)] == ["", "on error"]
    h = one(g, "handler")
    assert h["code"] == "catch (e)" and h["say"]["plain"] == "If it fails (`e`)"
    assert len(by_kind(g, "raise")) == 1 and out(g, by_kind(g, "raise")[0]) == []
    fin = next(n for n in g["nodes"] if n["line"] == 8)
    assert any(g["nodes"][e["from"]]["line"] == 3 for e in g["edges"] if e["to"] == fin["id"])


def test_a_finally_block_is_still_drawn_when_every_other_path_leaves():
    g = flow("function f(p) {\n  try {\n    return read(p);\n  } catch (e) {\n    throw e;\n  } finally {\n    close();\n  }\n}\n")
    fin = next(n for n in g["nodes"] if n["line"] == 7)
    assert out(g, fin) == []
    assert ("always at the end", fin["id"]) in [(e["label"], e["to"]) for e in g["edges"] if e["from"] == one(g, "try")["id"]]


def test_switch_has_one_way_out_per_case_and_empty_cases_share_a_body():
    code = ("function f(c) {\n  let x;\n  switch (c) {\n    case 'a':\n    case 'b':\n      x = 1;\n      break;\n"
            "    case 'c':\n      return 3;\n    default:\n      x = 2;\n  }\n  return x;\n}\n")
    g = flow(code)
    m = one(g, "branch")
    assert sorted(l for l, _ in out(g, m)) == ["'a' / 'b'", "'c'", "default"]
    ret_final = [n for n in by_kind(g, "return") if n["line"] == 13][0]
    assert len([e for e in g["edges"] if e["to"] == ret_final["id"]]) == 2           # the break and the default both end up here


def test_a_switch_without_default_has_a_no_match_way():
    g = flow("function f(c) {\n  switch (c) {\n    case 1:\n      use(1);\n      break;\n  }\n  return c;\n}\n")
    assert "no match" in [l for l, _ in out(g, one(g, "branch"))]


def test_throw_ends_the_path():
    g = flow("function f(x) {\n  if (!x) {\n    throw new Error('no');\n  }\n  return x;\n}\n")
    r = one(g, "raise")
    assert out(g, r) == [] and r["say"]["plain"] == "Stop with an error: `new Error('no')`"


# ----------------------------------------------------------------- the words
@pytest.mark.parametrize("cond,phrase", [
    ("x > 0", "`x` is more than 0"),
    ("x >= 1", "`x` is at least 1"),
    ("x < limit", "`x` is less than `limit`"),
    ("x === 'a'", "`x` equals 'a'"),
    ("x !== 3", "`x` is not 3"),
    ("x === null", "`x` is missing (None)".replace("None", "null")),
    ("x == undefined", "`x` is missing (undefined)"),
    ("x !== undefined", "`x` is set"),
    ("items", "`items` has something in it"),
    ("!items", "`items` is empty"),
    ("!(x > 1)", "`x` is at most 1"),
    ("a && b", "`a` has something in it and `b` has something in it"),
    ("a || b", "`a` has something in it or `b` has something in it"),
    ("ready()", "`ready()` is true"),
    ("x instanceof Foo", "`x` is a `Foo`"),
])
def test_conditions_are_put_into_words(cond, phrase):
    g = flow(f"function f(x, items, a, b, limit, ready) {{\n  if ({cond}) {{\n    return 1;\n  }}\n  return 2;\n}}\n")
    d = one(g, "decision")
    assert d["say"]["plain"] == phrase + "?" and d["say"]["kid"] == phrase + "?" and d["code"] == f"if ({cond})"


def test_steps_are_described_for_beginners_too():
    g = flow("async function f(xs) {\n  let total = 0;\n  log(xs);\n  await send(total);\n  for (const x of xs) {\n    total += x;\n  }\n  if (total) {\n    return total;\n  }\n}\n")
    first = by_kind(g, "step")[0]
    assert first["say"]["plain"] == "Set `total`, then call `log`, then wait for `send`"
    assert first["say"]["kid"].startswith("Remember a value called `total`, then ask `log` to do its job")
    assert one(g, "loop")["say"]["kid"] == "Take each `x` from `xs`, one by one"


def test_nested_functions_are_one_step():
    g = flow("function f(x) {\n  function helper(y) {\n    if (y) { return 1; }\n    return 2;\n  }\n  if (x) { return helper(x); }\n  return 0;\n}\n")
    assert len(by_kind(g, "decision")) == 1 and any(n["say"]["plain"] == "Define `helper`" for n in g["nodes"])


def test_use_strict_and_comments_are_not_steps():
    g = flow("function f(x) {\n  'use strict';\n  // note\n  if (x) { return 1; }\n  return 2;\n}\n")
    assert not any("strict" in n["code"] or "note" in n["code"] for n in g["nodes"])


def test_every_call_in_the_function_lies_inside_a_box():
    code = "function f(xs) {\n  for (const x of xs) {\n    if (ok(x)) {\n      keep(x);\n    }\n  }\n  return done();\n}\n"
    info = analyze_file("a.js", "JavaScript", code)
    sym = next(s for s in info.symbols if s.qual == "f")
    for chain, line in sym.raw_calls:
        assert any(n["line"] <= line <= n["end"] for n in sym.flow["nodes"]), (chain, line)
