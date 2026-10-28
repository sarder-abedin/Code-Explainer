"""A deterministic top-to-bottom layout for boxes and arrows."""
import itertools
import random

import pytest

from code_explainer import flowlayout
from code_explainer.flowlayout import layout

BOX = (100, 40)


def sizes(n, w=100, h=40):
    return {i: (w, h) for i in range(n)}


def rect(res, i, s):
    x, y = res["pos"][i]
    return x, y, x + s[i][0], y + s[i][1]


def centre_x(res, i, s):
    return res["pos"][i][0] + s[i][0] / 2


# ----------------------------------------------------------------- basic shapes
def test_an_empty_graph_has_no_size_to_speak_of():
    r = layout({}, [])
    assert r["pos"] == {} and r["paths"] == {} and r["back"] == set()


def test_a_single_box_sits_inside_the_margin():
    r = layout({7: (120, 36)}, [])
    assert r["pos"][7] == (flowlayout.MARGIN, flowlayout.MARGIN)
    assert r["width"] == 120 + 2 * flowlayout.MARGIN and r["height"] == 36 + 2 * flowlayout.MARGIN


def test_a_chain_goes_straight_down():
    s = sizes(3)
    r = layout(s, [(0, 1), (1, 2)])
    ys = [r["pos"][i][1] for i in range(3)]
    assert ys == sorted(ys) and len(set(ys)) == 3
    assert len({centre_x(r, i, s) for i in range(3)}) == 1
    assert [len(r["paths"][e]) for e in [(0, 1), (1, 2)]] == [2, 2]


def test_a_split_and_merge_is_symmetrical():
    s = sizes(4)
    r = layout(s, [(0, 1), (0, 2), (1, 3), (2, 3)])
    assert r["pos"][1][1] == r["pos"][2][1] and r["pos"][1][0] < r["pos"][2][0]
    top, bottom = centre_x(r, 0, s), centre_x(r, 3, s)
    mid = (centre_x(r, 1, s) + centre_x(r, 2, s)) / 2
    assert top == pytest.approx(mid, abs=1) and bottom == pytest.approx(mid, abs=1)


def test_the_order_given_decides_which_branch_is_on_the_left():
    s = sizes(4)
    left_first = layout(s, [(0, 1), (0, 2), (1, 3), (2, 3)], order=[0, 1, 2, 3])
    right_first = layout(s, [(0, 2), (0, 1), (2, 3), (1, 3)], order=[0, 2, 1, 3])
    assert left_first["pos"][1][0] < left_first["pos"][2][0]
    assert right_first["pos"][2][0] < right_first["pos"][1][0]


def test_a_box_with_nothing_to_do_with_the_rest_still_gets_a_place():
    s = sizes(3)
    r = layout(s, [(0, 1)])
    assert set(r["pos"]) == {0, 1, 2}


# ----------------------------------------------------------------- loops
def test_a_loop_goes_round_by_a_back_edge_that_comes_up_again():
    s = sizes(3)
    r = layout(s, [(0, 1), (1, 2), (2, 1)])
    assert r["back"] == {(2, 1)}
    path = r["paths"][(2, 1)]
    assert path[0][1] == r["pos"][2][1]                                   # leaves the top of the lower box
    assert path[-1][1] == r["pos"][1][1] + s[1][1]                        # and comes into the bottom of the upper one
    assert r["pos"][2][1] > r["pos"][1][1]


def test_a_box_that_calls_itself_gets_a_small_loop_at_its_side():
    s = sizes(2)
    r = layout(s, [(0, 1), (1, 1)])
    assert r["back"] == {(1, 1)}
    x, y = r["pos"][1]
    pts = r["paths"][(1, 1)]
    assert pts[0][0] == x + s[1][0] and pts[-1][0] == x + s[1][0]           # starts and ends on the right-hand border
    assert max(px for px, _ in pts) == x + s[1][0] + flowlayout.LOOP_REACH


def test_forward_edges_always_run_downwards_and_back_edges_upwards():
    rng = random.Random(3)
    for _ in range(40):
        n = rng.randint(2, 12)
        edges = list({(rng.randrange(n), rng.randrange(n)) for _ in range(rng.randint(1, 3 * n))})
        r = layout(sizes(n), edges)
        for a, b in edges:
            if a == b:
                continue
            ya, yb = r["pos"][a][1], r["pos"][b][1]
            assert (ya > yb) if (a, b) in r["back"] else (ya < yb), (a, b)


# ----------------------------------------------------------------- nothing collides
def collides(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def segment_hits(p, q, box):
    for t in [i / 60 for i in range(61)]:
        x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
        if box[0] + 1 < x < box[2] - 1 and box[1] + 1 < y < box[3] - 1:
            return True
    return False


@pytest.mark.parametrize("seed", range(25))
def test_random_graphs_never_overlap_boxes_or_run_lines_through_them(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 14)
    s = {i: (rng.choice([100, 140, 230]), rng.choice([36, 52, 76])) for i in range(n)}
    edges = list({(rng.randrange(n), rng.randrange(n)) for _ in range(rng.randint(1, 3 * n))})
    r = layout(s, edges)
    boxes = {i: rect(r, i, s) for i in range(n)}
    for i, j in itertools.combinations(range(n), 2):
        assert not collides(boxes[i], boxes[j]), (seed, i, j)
    for (a, b), pts in r["paths"].items():
        for p, q in zip(pts, pts[1:]):
            for k, box in boxes.items():
                assert not segment_hits(p, q, box), (seed, (a, b), k)
    for box in boxes.values():
        assert box[0] >= flowlayout.MARGIN and box[1] >= flowlayout.MARGIN
        assert box[2] <= r["width"] - flowlayout.MARGIN + 1 and box[3] <= r["height"] - flowlayout.MARGIN + 1
    for pts in r["paths"].values():
        assert all(0 <= x <= r["width"] and 0 <= y <= r["height"] for x, y in pts)


def test_a_long_edge_is_routed_around_the_boxes_between():
    s = sizes(4)
    r = layout(s, [(0, 1), (1, 2), (2, 3), (0, 3)])
    assert len(r["paths"][(0, 3)]) > 2
    xs = {p[0] for p in r["paths"][(0, 3)][1:-1]}
    mids = [r["pos"][1][0], r["pos"][1][0] + 100]
    assert not any(mids[0] < x < mids[1] for x in xs)


def test_edges_start_and_end_on_the_borders_of_their_boxes():
    s = sizes(4)
    r = layout(s, [(0, 1), (0, 2), (1, 3), (2, 3)])
    for (a, b), pts in r["paths"].items():
        assert pts[0][1] == r["pos"][a][1] + s[a][1]
        assert pts[-1][1] == r["pos"][b][1]
        assert r["pos"][a][0] <= pts[0][0] <= r["pos"][a][0] + s[a][0]
        assert r["pos"][b][0] <= pts[-1][0] <= r["pos"][b][0] + s[b][0]


def test_two_arrows_leaving_one_box_start_at_different_points():
    s = sizes(3)
    r = layout(s, [(0, 1), (0, 2)])
    assert r["paths"][(0, 1)][0][0] != r["paths"][(0, 2)][0][0]


# ----------------------------------------------------------------- determinism and size
def test_the_same_input_gives_exactly_the_same_picture():
    rng = random.Random(9)
    s = sizes(12)
    edges = list({(rng.randrange(12), rng.randrange(12)) for _ in range(30)})
    assert layout(s, edges) == layout(s, list(edges))


def test_duplicate_edges_are_drawn_once():
    r = layout(sizes(2), [(0, 1), (0, 1)])
    assert list(r["paths"]) == [(0, 1)]


def test_a_hundred_boxes_lay_out_quickly():
    import time
    rng = random.Random(1)
    n = 120
    edges = [(i, rng.randrange(i + 1, n)) for i in range(n - 1)] + [(rng.randrange(n), rng.randrange(n)) for _ in range(60)]
    t = time.time()
    layout(sizes(n), edges)
    assert time.time() - t < 2.0


# ----------------------------------------------------------------- quality, not just correctness
def crossings(r, edges):
    """Pairs of arrows between the same two layers that swap sides (their endpoints are in opposite orders)."""
    pairs = [(e, r["paths"][e]) for e in edges if e not in r["back"]]
    n = 0
    for (e1, p1), (e2, p2) in itertools.combinations(pairs, 2):
        if p1[0][1] == p2[0][1] and p1[-1][1] == p2[-1][1] and len(p1) == 2 and len(p2) == 2:
            if (p1[0][0] - p2[0][0]) * (p1[-1][0] - p2[-1][0]) < 0:
                n += 1
    return n


def test_boxes_are_reordered_so_arrows_do_not_cross():
    s = sizes(4)
    edges = [(0, 3), (1, 2)]                       # listed in an order that would cross if left alone
    r = layout(s, edges, order=[0, 1, 2, 3])
    assert crossings(r, edges) == 0


def test_a_box_sits_under_its_only_parent():
    s = sizes(4)
    r = layout(s, [(0, 1), (0, 2), (1, 3)])
    assert centre_x(r, 3, s) == pytest.approx(centre_x(r, 1, s), abs=1)


def test_a_loop_arrow_does_not_run_into_the_box_beside_it():
    s = sizes(3)
    r = layout(s, [(0, 1), (0, 2), (1, 1)], order=[0, 1, 2])
    reach = max(x for x, _ in r["paths"][(1, 1)])
    assert reach < r["pos"][2][0]


def test_an_arrow_leaving_a_short_box_beside_a_tall_one_drops_down_first():
    s = {0: (100, 20), 1: (100, 80), 2: (100, 40)}
    r = layout(s, [(0, 2)], order=[0, 1, 2])
    pts = r["paths"][(0, 2)]
    layer_bottom = r["pos"][1][1] + 80
    assert pts[0] == (pts[0][0], r["pos"][0][1] + 20) and pts[1] == (pts[0][0], layer_bottom)
    box1 = rect(r, 1, s)
    assert not any(segment_hits(p, q, box1) for p, q in zip(pts, pts[1:]))
