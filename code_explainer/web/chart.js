// Function flowcharts: the step-by-step picture of one function, with zoom, diving into calls and export.
// Spliced into app.js at build time (see report.py), so it shares app.js's scope: h(), s(), SYMS, LEVEL, openFile...

const CHART = { path: [], sel: -1, k: 1, tx: 0, ty: 0, cw: 900, ch: 560, mermaid: false, opener: null, onKey: null, w: 0, h: 0 };
const FC_LABEL = { start: "start", end: "finish", step: "step", decision: "choice", loop: "loop", try: "try", handler: "if it fails",
  branch: "pick a path", return: "hands back", raise: "stops with an error", more: "not shown" };
const FC_LEGEND = "Diamond: a choice. Hexagon: a loop or a pick-a-path. Dashed arrow: goes back round. Green: finishes with an answer. Red: stops with an error.";

function chartButton(f, sym) {
  if (!sym.flow) return null;
  return h("button", { class: "chartbtn", title: "Show this function as a flowchart",
    onclick: e => { e.stopPropagation(); openChart(`${f.path}::${sym.qual}`, e.currentTarget); } }, "▦ Flowchart");
}

function chartCurrent() {
  const hit = SYMS.get(CHART.path[CHART.path.length - 1]);
  return hit && hit.sym.flow ? hit : null;
}

function openChart(sid, opener) {
  const hit = SYMS.get(sid);
  if (!hit || !hit.sym.flow) return false;
  if (!document.getElementById("chart")) {
    CHART.opener = opener || null;
    CHART.onKey = e => { if (e.key === "Escape") closeChart(); };
    document.addEventListener("keydown", CHART.onKey);
    document.body.append(h("div", { id: "chart", class: "chart-overlay", role: "dialog", "aria-modal": "true", "aria-label": "Flowchart",
      onclick: e => { if (e.target.id === "chart") closeChart(); } }, h("div", { class: "chart-box" })));
  }
  CHART.path = [sid];
  state.chart = sid; writeHash();
  chartRender();
  return true;
}

function chartDive(sid) {
  if (!SYMS.get(sid) || !SYMS.get(sid).sym.flow) return;
  CHART.path.push(sid);
  state.chart = sid; writeHash();
  chartRender();
}

function chartBack(index) {
  CHART.path = CHART.path.slice(0, index + 1);
  state.chart = CHART.path[CHART.path.length - 1]; writeHash();
  chartRender();
}

function closeChart() {
  const el = document.getElementById("chart");
  if (el) el.remove();
  if (CHART.onKey) document.removeEventListener("keydown", CHART.onKey);
  CHART.onKey = null; CHART.path = []; CHART.mermaid = false; CHART.sel = -1;
  if (state.chart) { state.chart = ""; writeHash(); }
  if (CHART.opener && document.contains(CHART.opener)) CHART.opener.focus();
  CHART.opener = null;
}

// Keep the dialog in step with the address bar (opened from a link, or edited by hand).
function chartSync() {
  const open = !!document.getElementById("chart");
  if (state.chart && SYMS.has(state.chart) && SYMS.get(state.chart).sym.flow) {
    if (!open || CHART.path[CHART.path.length - 1] !== state.chart) openChart(state.chart);
  } else if (open) { state.chart = ""; closeChart(); }
  else state.chart = "";
}

// ---------- the words in a box ----------
function chartText(n) {
  const plain = !["start", "end", "more"].includes(n.kind);
  const raw = LEVEL === "tech" && plain && n.code ? n.code : (n.say[LEVEL === "kid" ? "kid" : "plain"] || n.say.plain);
  return String(raw).replace(/`/g, "");
}

function chartWrap(text, width, maxLines) {
  const words = text.split(/\s+/).filter(Boolean), lines = [];
  let cur = "";
  for (let w of words) {
    while (w.length > width) { if (cur) { lines.push(cur); cur = ""; } lines.push(w.slice(0, width)); w = w.slice(width); }
    if (!cur) cur = w; else if ((cur + " " + w).length <= width) cur += " " + w; else { lines.push(cur); cur = w; }
  }
  if (cur) lines.push(cur);
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    kept[maxLines - 1] = kept[maxLines - 1].slice(0, Math.max(0, width - 1)).replace(/\s+$/, "") + "…";
    return kept;
  }
  return lines;
}

// ---------- drawing ----------
function chartShape(n) {
  const w = +n.w, hgt = +n.h, k = n.kind;
  if (k === "decision" || k === "branch") return s("polygon", { points: `${w / 2},0 ${w},${hgt / 2} ${w / 2},${hgt} 0,${hgt / 2}` });
  if (k === "loop") return s("polygon", { points: `14,0 ${w - 14},0 ${w},${hgt / 2} ${w - 14},${hgt} 14,${hgt} 0,${hgt / 2}` });
  if (["start", "end", "return", "raise"].includes(k)) return s("rect", { width: w, height: hgt, rx: hgt / 2 });
  return s("rect", { width: w, height: hgt, rx: 6 });
}

function chartNodeEl(n) {
  const text = chartText(n), diamond = n.kind === "decision" || n.kind === "branch";
  const lines = chartWrap(text, diamond ? 24 : 31, 3), step = 15, top = (n.h - lines.length * step) / 2 + 11;
  const el = s("g", { class: `fc-node fc-${n.kind}` + (CHART.sel === n.id ? " sel" : ""), "data-id": n.id, "data-kind": n.kind, "data-line": n.line,
    transform: `translate(${+n.x},${+n.y})`, role: "button", tabindex: "0", "aria-label": `${FC_LABEL[n.kind] || "step"}: ${text}`,
    onclick: ev => { ev.stopPropagation(); chartSelect(n.id); },
    onkeydown: ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); chartSelect(n.id); } } },
    s("title", {}, [text, n.code && n.code !== text ? n.code : ""].filter(Boolean).join("\n")), chartShape(n),
    s("text", { x: n.w / 2, "text-anchor": "middle" }, lines.map((l, i) => s("tspan", { x: n.w / 2, y: top + i * step }, l))));
  return el;
}

function chartEdgeEl(e) {
  const d = e.path.map(([x, y], i) => `${i ? "L" : "M"}${+x} ${+y}`).join(" ");
  return s("g", { class: "fc-edge" + (e.back ? " fc-back" : "") },
    s("path", { d, "marker-end": "url(#fc-arrow)" }),
    e.label ? s("text", { class: "fc-elabel", x: +e.lx + 6, y: +e.ly - 3 }, e.label) : null);
}

function chartRender() {
  const box = document.querySelector("#chart .chart-box"), hit = chartCurrent();
  if (!box || !hit) return;
  const { sym, file } = hit, flow = sym.flow, sid = CHART.path[CHART.path.length - 1];
  CHART.sel = -1; CHART.w = flow.w; CHART.h = flow.h; CHART.nodes = flow.nodes;
  const crumbs = CHART.path.length > 1 ? h("div", { class: "fc-crumbs" }, CHART.path.map((id, i) => {
    const label = symLabel(SYMS.get(id).sym);
    return [i ? " › " : "", i === CHART.path.length - 1 ? h("b", {}, label) : h("button", { onclick: () => chartBack(i) }, label)];
  })) : null;
  const svg = s("svg", { class: "fc-svg", role: "img", "aria-label": `Flowchart of ${sym.qual}`, viewBox: "0 0 10 10" },
    s("defs", {}, s("marker", { id: "fc-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" },
      s("path", { d: "M0 0L10 5L0 10z", class: "fc-arrowhead" }))),
    s("g", { class: "fc-g" }, s("g", { class: "fc-edges" }, flow.edges.map(chartEdgeEl)), s("g", { class: "fc-nodes" }, flow.nodes.map(chartNodeEl))));
  CHART.svg = svg;
  svg.addEventListener("click", () => chartSelect(-1));
  chartPanZoom(svg);
  const canvas = h("div", { class: "chart-canvas" }, svg);
  box.replaceChildren(...[
    h("div", { class: "chart-head" },
      h("div", {}, h("h2", { id: "chart-title" }, `Flowchart: ${symLabel(sym)}`), h("div", { class: "hint" }, `${file.path}:${sym.line}`), crumbs),
      h("div", { class: "chart-tools" },
        h("button", { id: "chart-fit", onclick: chartFit, title: "Fit the whole chart in view" }, "Fit"),
        h("button", { id: "chart-mermaid", onclick: () => { CHART.mermaid = !CHART.mermaid; chartInfo(); }, title: "Get this chart as Mermaid text" }, "Copy as Mermaid"),
        h("button", { id: "chart-svg", onclick: () => chartDownload(sym), title: "Save the picture as an SVG file" }, "Download SVG"),
        h("button", { id: "chart-close", onclick: closeChart, "aria-label": "Close" }, "✕"))),
    h("div", { class: "chart-body" }, canvas, h("aside", { class: "fc-info", "aria-live": "polite" })),
    flow.truncated ? h("div", { class: "fc-note" }, "This function is long: only the first steps are drawn. Open the code for the rest.") : null,
    h("div", { class: "hint chart-foot" }, FC_LEGEND, " Scroll to zoom, drag to move, press Esc to close.")].filter(Boolean));
  const r = canvas.getBoundingClientRect();
  CHART.cw = Math.max(320, r.width || 900); CHART.ch = Math.max(240, r.height || 560);
  svg.setAttribute("viewBox", `0 0 ${CHART.cw} ${CHART.ch}`);
  chartOpening();
  chartInfo();
  const close = document.getElementById("chart-close");
  if (close) close.focus({ preventScroll: true });
}

// ---------- pan and zoom ----------
function chartApply() {
  const g = CHART.svg && CHART.svg.querySelector(".fc-g");
  if (g) g.setAttribute("transform", `translate(${+CHART.tx.toFixed(2)},${+CHART.ty.toFixed(2)}) scale(${+CHART.k.toFixed(4)})`);
}
const FC_READABLE = 0.7;   // below this the words are too small to read comfortably
function chartFit() {
  const m = 16;
  CHART.k = Math.max(0.15, Math.min(1.25, (CHART.cw - 2 * m) / CHART.w, (CHART.ch - 2 * m) / CHART.h));
  CHART.tx = (CHART.cw - CHART.w * CHART.k) / 2; CHART.ty = Math.max(m, (CHART.ch - CHART.h * CHART.k) / 2);
  chartApply();
}
// Where it opens: the whole chart when that is readable, otherwise a readable size starting at the Start box.
function chartOpening() {
  chartFit();
  if (CHART.k >= FC_READABLE) return;
  const m = 16, start = CHART.nodes[0];
  CHART.k = Math.min(FC_READABLE, Math.max(CHART.k, (CHART.cw - 2 * m) / CHART.w));
  const mid = (+start.x + +start.w / 2) * CHART.k;
  CHART.tx = CHART.w * CHART.k <= CHART.cw ? (CHART.cw - CHART.w * CHART.k) / 2 : CHART.cw / 2 - mid;
  CHART.ty = m;
  chartApply();
}
function chartPanZoom(svg) {
  svg.addEventListener("wheel", ev => {
    ev.preventDefault();
    const r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) * (CHART.cw / r.width), sy = (ev.clientY - r.top) * (CHART.ch / r.height);
    const nk = Math.max(0.15, Math.min(4, CHART.k * (ev.deltaY < 0 ? 1.15 : 1 / 1.15)));
    CHART.tx = sx - (sx - CHART.tx) / CHART.k * nk; CHART.ty = sy - (sy - CHART.ty) / CHART.k * nk; CHART.k = nk;
    chartApply();
  }, { passive: false });
  svg.addEventListener("pointerdown", ev => {
    if (ev.target.closest && ev.target.closest(".fc-node")) return;
    const x0 = ev.clientX, y0 = ev.clientY, tx0 = CHART.tx, ty0 = CHART.ty, r = svg.getBoundingClientRect();
    svg.setPointerCapture(ev.pointerId);
    const move = e2 => { CHART.tx = tx0 + (e2.clientX - x0) * (CHART.cw / r.width); CHART.ty = ty0 + (e2.clientY - y0) * (CHART.ch / r.height); chartApply(); };
    const up = () => { svg.removeEventListener("pointermove", move); svg.removeEventListener("pointerup", up); };
    svg.addEventListener("pointermove", move); svg.addEventListener("pointerup", up);
  });
}

// ---------- the side panel ----------
function chartSelect(id) {
  CHART.sel = id; CHART.mermaid = false;
  for (const el of document.querySelectorAll("#chart .fc-node")) el.classList.toggle("sel", +el.dataset.id === id);
  chartInfo();
}

function chartInfo() {
  const box = document.querySelector("#chart .fc-info"), hit = chartCurrent();
  if (!box || !hit) return;
  if (CHART.mermaid) { box.replaceChildren(chartMermaidPanel(hit)); return; }
  const n = hit.sym.flow.nodes.find(x => x.id === CHART.sel);
  if (!n) { box.replaceChildren(h("p", { class: "hint" }, "Click a box to see what it does, the exact code, and the functions it calls.")); return; }
  const same = n.line === n.end;
  const calls = [], seen = new Set();
  for (const c of hit.sym.callees || []) {
    if (c.line < n.line || c.line > n.end || seen.has(c.id) || !SYMS.has(c.id)) continue;
    seen.add(c.id);
    const target = SYMS.get(c.id), guess = c.conf !== "certain" ? " (a guess)" : "";
    calls.push(h("li", {}, target.sym.flow
      ? h("button", { class: "fc-dive", onclick: () => chartDive(c.id) }, `Open the flowchart of ${symLabel(target.sym)}${guess}`)
      : [h("span", {}, `${symLabel(target.sym)} has no choices inside${guess} `),
        h("button", { class: "fc-open", onclick: () => { closeChart(); openFile(target.file.path, target.sym.line); } }, "Show its code")]));
  }
  box.replaceChildren(...[
    h("span", { class: "badge" }, FC_LABEL[n.kind] || n.kind),
    ["start", "end", "more"].includes(n.kind) ? null : h("p", {}, rich(n.say[LEVEL === "kid" ? "kid" : "plain"] || n.say.plain)),
    n.code ? h("pre", { class: "snip" }, n.code) : null,
    n.kind === "start" || n.kind === "end" ? null : h("div", { class: "hint" }, same ? `Line ${n.line}` : `Lines ${n.line}–${n.end}`),
    n.kind === "start" || n.kind === "end" ? null : h("button", { class: "fc-showcode", onclick: () => { const p = hit.file.path, line = n.line; closeChart(); openFile(p, line); } }, "Show this in the code"),
    calls.length ? h("div", {}, h("h3", {}, "Calls here"), h("ul", { class: "fc-calls" }, calls)) : null].filter(Boolean));
}

// ---------- Mermaid and SVG export ----------
function mermaidSafe(text) {
  return String(text).replace(/[\r\n]+/g, " ").replace(/"/g, "#quot;").replace(/</g, "#lt;").replace(/>/g, "#gt;").slice(0, 80);
}
function chartMermaid(flow) {
  const lines = ["flowchart TD"];
  for (const n of flow.nodes) {
    const t = mermaidSafe(chartText(n));
    const [open, close] = n.kind === "decision" || n.kind === "loop" || n.kind === "branch" ? ["{", "}"]
      : ["start", "end", "return", "raise"].includes(n.kind) ? ["([", "])"] : ["[", "]"];
    lines.push(`  n${n.id}${open}"${t}"${close}`);
  }
  for (const e of flow.edges) lines.push(e.label ? `  n${e.from} -- "${mermaidSafe(e.label)}" --> n${e.to}` : `  n${e.from} --> n${e.to}`);
  return lines.join("\n");
}
function chartMermaidPanel(hit) {
  const ta = h("textarea", { readonly: "readonly", rows: 12, "aria-label": "Mermaid text" });
  ta.value = chartMermaid(hit.sym.flow);
  return h("div", { class: "fc-mermaid" }, h("h3", {}, "Mermaid text"),
    h("p", { class: "hint" }, "Paste this into a ```mermaid block in GitHub, a README or a pull request to show the same chart."),
    ta, h("button", { onclick: () => { ta.select(); try { navigator.clipboard.writeText(ta.value); } catch (e) { /* the text is selected; copy by hand */ } } }, "Copy"));
}

const FC_EXPORT_CSS = `.fc-edge path{fill:none;stroke:#6b7385;stroke-width:1.4}.fc-back path{stroke-dasharray:5 4}.fc-arrowhead{fill:#6b7385}
.fc-elabel{font:11px sans-serif;fill:#6b7385}.fc-node polygon,.fc-node rect{fill:#ffffff;stroke:#3b6fe0;stroke-width:1.4}
.fc-node text{font:12px sans-serif;fill:#1d2330}.fc-start rect,.fc-end rect{fill:#f1f3f7;stroke:#6b7385}.fc-return rect{stroke:#15803d;fill:#eefaf1}
.fc-raise rect{stroke:#c2410c;fill:#fff3ec}.fc-handler rect,.fc-try rect{stroke-dasharray:4 3}.fc-more rect{stroke-dasharray:2 3;stroke:#6b7385}`;
function chartSvgString() {
  const svg = CHART.svg.cloneNode(true);
  svg.querySelector(".fc-g").removeAttribute("transform");
  for (const el of svg.querySelectorAll("[tabindex],[role]")) { el.removeAttribute("tabindex"); el.removeAttribute("role"); }
  svg.setAttribute("xmlns", SVGNS);
  svg.setAttribute("viewBox", `0 0 ${CHART.w} ${CHART.h}`);
  svg.setAttribute("width", CHART.w); svg.setAttribute("height", CHART.h);
  const style = document.createElementNS(SVGNS, "style");
  style.textContent = FC_EXPORT_CSS;
  svg.insertBefore(style, svg.firstChild);
  return new XMLSerializer().serializeToString(svg);
}
function chartDownload(sym) {
  const blob = new Blob([chartSvgString()], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: `${sym.qual}-flowchart.svg` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
