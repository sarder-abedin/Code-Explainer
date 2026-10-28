"""A deterministic top-to-bottom layout for boxes and arrows (a small layered graph drawing).

1. Arrows that would close a loop (found by a depth-first walk) are set aside and drawn as "back" arrows.
2. Every box gets the lowest layer that keeps all forward arrows pointing down.
3. An arrow that spans several layers is given an invisible stand-in box in each layer it crosses, so it has its own
   lane and never runs through a real box.
4. Boxes in a layer are ordered to reduce crossings, then spread out near the middle of the boxes they connect to.

The same input always gives the same picture. Lines only ever travel in the gaps between layers, or straight up and
down inside a box's own column, so they never cross a box.
"""

from __future__ import annotations

MARGIN = 24
GAP_X = 40
GAP_Y = 48
DUMMY_W = 18
LOOP_REACH = 22          # how far a box's arrow to itself sticks out; the gap between boxes is wider
SWEEPS = 8
PASSES = 4
_BACK_PORT = 0.88


def _back_edges(ids: list[int], succ: dict[int, list[int]], has_in: set[int]) -> set[tuple[int, int]]:
    back, state = set(), {}
    roots = [i for i in ids if i not in has_in] + [i for i in ids if i in has_in]
    for root in roots:
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(succ[root]))]
        while stack:
            node, it = stack[-1]
            for nxt in it:
                if state.get(nxt) == 1:
                    back.add((node, nxt))
                elif nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, iter(succ[nxt])))
                    break
            else:
                state[node] = 2
                stack.pop()
    return back


def _layers(ids: list[int], fwd: list[tuple[int, int]]) -> dict[int, int]:
    indeg = {i: 0 for i in ids}
    out: dict[int, list[int]] = {i: [] for i in ids}
    for a, b in fwd:
        indeg[b] += 1
        out[a].append(b)
    layer = {i: 0 for i in ids}
    ready = [i for i in ids if indeg[i] == 0]
    while ready:
        node = ready.pop(0)
        for nxt in out[node]:
            layer[nxt] = max(layer[nxt], layer[node] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    return layer


def layout(sizes: dict[int, tuple[int, int]], edges: list[tuple[int, int]], order: list[int] | None = None) -> dict:
    ids = list(order) if order is not None else sorted(sizes)
    if not ids:
        return {"pos": {}, "paths": {}, "back": set(), "width": 0, "height": 0}
    seen: set = set()
    unique = [e for e in edges if not (e in seen or seen.add(e))]
    loops = {a for a, b in unique if a == b}
    unique = [e for e in unique if e[0] != e[1]]
    succ: dict[int, list[int]] = {i: [] for i in ids}
    for a, b in unique:
        succ[a].append(b)
    back = _back_edges(ids, succ, {b for _, b in unique})
    fwd = [e for e in unique if e not in back]
    layer_of = _layers(ids, fwd)

    # --- virtual chains: every arrow becomes a path through stand-in boxes, always running downwards
    next_id = max(ids) + 1
    dummy_layer: dict[int, int] = {}
    chains: list[tuple[tuple[int, int], list[int], bool]] = []     # (original edge, boxes from top to bottom, is_back)
    for a, b in unique:
        is_back = (a, b) in back
        top, bottom = (b, a) if is_back else (a, b)
        mid = []
        for L in range(layer_of[top] + 1, layer_of[bottom]):
            dummy_layer[next_id] = L
            mid.append(next_id)
            next_id += 1
        chains.append(((a, b), [top, *mid, bottom], is_back))
    node_layer = {**layer_of, **dummy_layer}
    nlayers = max(node_layer.values()) + 1
    layers: list[list[int]] = [[] for _ in range(nlayers)]
    for i in ids:
        layers[layer_of[i]].append(i)
    for d in sorted(dummy_layer):
        layers[dummy_layer[d]].append(d)
    up: dict[int, list[int]] = {n: [] for n in node_layer}
    down: dict[int, list[int]] = {n: [] for n in node_layer}
    for _, boxes, _ in chains:
        for p, q in zip(boxes, boxes[1:]):
            down[p].append(q)
            up[q].append(p)

    # --- order inside layers
    index = {n: k for lay in layers for k, n in enumerate(lay)}

    def reorder(L: int, neighbours: dict[int, list[int]]) -> None:
        def key(n):
            nb = neighbours[n]
            return (sum(index[m] for m in nb) / len(nb) if nb else float(index[n]), index[n])
        layers[L].sort(key=key)
        for k, n in enumerate(layers[L]):
            index[n] = k
    for s in range(SWEEPS):
        if s % 2 == 0:
            for L in range(1, nlayers):
                reorder(L, up)
        else:
            for L in range(nlayers - 2, -1, -1):
                reorder(L, down)

    # --- horizontal positions (centres)
    def width(n):
        return sizes[n][0] if n in sizes else DUMMY_W

    left = {n: width(n) / 2 for n in node_layer}
    right = left
    cx: dict[int, float] = {}
    for lay in layers:
        x = 0.0
        for k, n in enumerate(lay):
            x = left[n] if k == 0 else x + right[lay[k - 1]] + GAP_X + left[n]
            cx[n] = x
        total = cx[lay[-1]] + right[lay[-1]] if lay else 0
        shift = -(total - left[lay[0]]) / 2 if lay else 0
        for n in lay:
            cx[n] += shift

    def resolve(lay: list[int], neighbours: dict[int, list[int]]) -> None:
        desired = [sum(cx[m] for m in neighbours[n]) / len(neighbours[n]) if neighbours[n] else cx[n] for n in lay]
        placed: list[float] = []
        for k, n in enumerate(lay):
            lowest = placed[-1] + right[lay[k - 1]] + GAP_X + left[n] if k else desired[0]
            placed.append(max(desired[k], lowest))
        shift = sum(d - p for d, p in zip(desired, placed)) / len(lay)
        for n, p in zip(lay, placed):
            cx[n] = p + shift
    for _ in range(PASSES):
        for L in range(1, nlayers):
            resolve(layers[L], up)
        for L in range(nlayers - 2, -1, -1):
            resolve(layers[L], down)
    offset = MARGIN - min(cx[n] - left[n] for n in node_layer)
    for n in cx:
        cx[n] += offset

    # --- vertical positions
    height = [max([sizes[n][1] for n in lay if n in sizes] or [0]) for lay in layers]
    top = []
    y = float(MARGIN)
    for h in height:
        top.append(y)
        y += h + GAP_Y
    bottom_of = [top[L] + height[L] for L in range(nlayers)]
    pos = {}
    for i in ids:
        L = layer_of[i]
        pos[i] = (round(cx[i] - sizes[i][0] / 2), round(top[L] + (height[L] - sizes[i][1]) / 2))
    total_w = round(max(cx[n] + right[n] + (LOOP_REACH if n in loops else 0) for n in node_layer) + MARGIN)
    total_h = round(bottom_of[-1] + MARGIN)

    # --- arrows
    def spread(count: int, k: int) -> float:
        return 0.5 if count == 1 else 0.2 + 0.6 * k / (count - 1)
    out_ports: dict[int, list] = {}
    in_ports: dict[int, list] = {}
    for edge, boxes, is_back in chains:
        if is_back:
            continue
        out_ports.setdefault(boxes[0], []).append((cx[boxes[1]], edge))
        in_ports.setdefault(boxes[-1], []).append((cx[boxes[-2]], edge))
    out_x: dict[tuple, float] = {}
    in_x: dict[tuple, float] = {}
    for n, lst in out_ports.items():
        lst.sort(key=lambda t: (t[0], t[1]))
        for k, (_, edge) in enumerate(lst):
            out_x[edge] = pos[n][0] + sizes[n][0] * spread(len(lst), k)
    for n, lst in in_ports.items():
        lst.sort(key=lambda t: (t[0], t[1]))
        for k, (_, edge) in enumerate(lst):
            in_x[edge] = pos[n][0] + sizes[n][0] * spread(len(lst), k)

    paths: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for edge, boxes, is_back in chains:
        first, last = boxes[0], boxes[-1]
        px = (pos[first][0] + sizes[first][0] * _BACK_PORT) if is_back else out_x[edge]
        qx = (pos[last][0] + sizes[last][0] * _BACK_PORT) if is_back else in_x[edge]
        pts = [(px, pos[first][1] + sizes[first][1])]
        if bottom_of[layer_of[first]] > pts[-1][1]:
            pts.append((px, bottom_of[layer_of[first]]))
        for d in boxes[1:-1]:
            L = node_layer[d]
            pts += [(cx[d], top[L]), (cx[d], bottom_of[L])]
        if top[layer_of[last]] < pos[last][1]:
            pts.append((qx, top[layer_of[last]]))
        pts.append((qx, pos[last][1]))
        pts = [(round(x), round(y)) for x, y in pts]
        paths[edge] = pts[::-1] if is_back else pts
    for n in loops:
        x, y = pos[n]
        w, h = sizes[n]
        mid = y + h // 2
        paths[(n, n)] = [(x + w, mid - 8), (x + w + LOOP_REACH, mid - 8), (x + w + LOOP_REACH, mid + 8), (x + w, mid + 8)]
    return {"pos": pos, "paths": paths, "back": back | {(n, n) for n in loops}, "width": total_w, "height": total_h}
