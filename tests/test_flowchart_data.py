"""Function flowcharts as they appear in the analysis: laid out, linked to calls, and kept to a size budget."""
import json

import pytest

from code_explainer import flowgraph
from code_explainer.core import analyze_repo
from conftest import write

CODE = '''def clean(items):
    """Drop the bad ones."""
    out = []
    for item in items:
        if item is None:
            continue
        if check(item):
            out.append(item)
    return out


def check(item):
    if item > 0:
        return True
    return False


def straight(a):
    b = a + 1
    return b


class Box:
    def put(self, x):
        if x:
            self.items.append(x)
        else:
            raise ValueError("nothing")
'''


def symbols(tmp_path, files=None):
    write(tmp_path, files or {"a.py": CODE})
    data = analyze_repo(tmp_path, "x")
    return {s["qual"]: s for f in data["files"] for s in f["symbols"]}, data


def test_functions_with_choices_carry_a_laid_out_chart(tmp_path):
    syms, _ = symbols(tmp_path)
    flow = syms["clean"]["flow"]
    assert flow["w"] > 0 and flow["h"] > 0 and flow["truncated"] is False
    start = flow["nodes"][0]
    assert start["kind"] == "start" and all(k in start for k in ("x", "y", "w", "h", "say", "code", "line", "end"))
    assert {n["kind"] for n in flow["nodes"]} >= {"loop", "decision", "return"}
    for e in flow["edges"]:
        assert len(e["path"]) >= 2 and all(len(p) == 2 for p in e["path"]) and isinstance(e["back"], bool)
        assert "lx" in e and "ly" in e


def test_methods_get_charts_too_and_straight_functions_do_not(tmp_path):
    syms, _ = symbols(tmp_path)
    assert "flow" in syms["Box.put"] and "flow" in syms["check"]
    assert "flow" not in syms["straight"] and "flow" not in syms["Box"]


def test_loops_come_back_up_as_back_edges(tmp_path):
    syms, _ = symbols(tmp_path)
    flow = syms["clean"]["flow"]
    assert any(e["back"] for e in flow["edges"])
    head = next(n for n in flow["nodes"] if n["kind"] == "loop")
    for e in flow["edges"]:
        if e["back"]:
            assert e["to"] == head["id"] and e["path"][-1][1] == head["y"] + head["h"]


def test_nodes_do_not_overlap_in_a_real_chart(tmp_path):
    syms, _ = symbols(tmp_path)
    for s in syms.values():
        if "flow" not in s:
            continue
        ns = s["flow"]["nodes"]
        for i, a in enumerate(ns):
            for b in ns[i + 1:]:
                assert a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"]


def test_every_call_the_graph_knows_about_lies_inside_a_box(tmp_path):
    syms, _ = symbols(tmp_path)
    flow = syms["clean"]["flow"]
    for c in syms["clean"]["callees"]:
        assert any(n["line"] <= c["line"] <= n["end"] for n in flow["nodes"]), c


def test_the_chart_is_plain_json_and_the_same_every_time(tmp_path):
    a, _ = symbols(tmp_path / "one")
    b, _ = symbols(tmp_path / "two")
    assert json.loads(json.dumps(a["clean"]["flow"])) == a["clean"]["flow"]
    assert a["clean"]["flow"] == b["clean"]["flow"]


def test_the_module_pseudo_symbol_and_classes_never_get_one(tmp_path):
    syms, _ = symbols(tmp_path, {"a.py": "import os\nif os.name:\n    print(1)\nelse:\n    print(2)\n"})
    assert all("flow" not in s for s in syms.values())


def test_the_budget_keeps_the_most_complex_functions(tmp_path, monkeypatch):
    files = {"a.py": "def tiny(x):\n    if x:\n        return 1\n    return 2\n\n\n" + CODE}      # the simple one comes first in the file
    syms, _ = symbols(tmp_path / "full", files)
    charted = {q: s for q, s in syms.items() if "flow" in s}
    top = max(charted, key=lambda q: charted[q]["complexity"])
    monkeypatch.setattr(flowgraph, "FLOW_BUDGET_BYTES", len(json.dumps(charted[top]["flow"], separators=(",", ":"))) + 10)
    kept, data = symbols(tmp_path / "tight", files)
    have = [q for q, s in kept.items() if "flow" in s]
    assert have == [top]
    assert data["flowTrimmed"] == len(charted) - 1


def test_two_arrows_between_the_same_boxes_share_one_line_and_both_labels():
    g = {"nodes": [{"id": 0, "kind": "decision", "code": "", "say": {"kid": "", "plain": ""}, "line": 1, "end": 1},
                   {"id": 1, "kind": "step", "code": "", "say": {"kid": "", "plain": ""}, "line": 2, "end": 2}],
         "edges": [{"from": 0, "to": 1, "label": "yes"}, {"from": 0, "to": 1, "label": "no"}], "truncated": False}
    out = flowgraph.finalize(g)
    assert len(out["edges"]) == 1 and out["edges"][0]["label"] == "yes / no"


def test_nothing_is_reported_trimmed_when_everything_fits(tmp_path):
    _, data = symbols(tmp_path)
    assert data["flowTrimmed"] == 0


def test_a_huge_function_is_shown_cut_off(tmp_path):
    body = "".join(f"    if x == {i}:\n        a = {i}\n" for i in range(60))
    syms, _ = symbols(tmp_path, {"a.py": "def big(x):\n" + body + "    return 0\n"})
    flow = syms["big"]["flow"]
    assert flow["truncated"] is True and any(n["kind"] == "more" for n in flow["nodes"])


def test_the_size_of_each_box_depends_only_on_its_kind(tmp_path):
    syms, _ = symbols(tmp_path)
    seen = {}
    for s in syms.values():
        for n in s.get("flow", {}).get("nodes", []):
            seen.setdefault(n["kind"], set()).add((n["w"], n["h"]))
    assert all(len(v) == 1 for v in seen.values())
