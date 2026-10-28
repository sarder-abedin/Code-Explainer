// The Map tab: the parts of the project, how they connect, and what they talk to.
// Spliced into app.js at build time (see report.py), so it shares app.js's scope: h(), s(), D, LEVEL, openFile...

const ARCH = { sel: null, overlay: "none", mermaid: false, allFiles: false, k: 1, tx: 0, ty: 0, cw: 900, ch: 560, svg: null };
const AR_VERBS = { kid: ["borrows tools from", "lends tools to"], plain: ["uses", "is used by"], tech: ["imports", "is imported by"] };
const AR_SHOWN = 3;
const AR_LEGEND = "Boxes are the parts of the project (found from its folders), arrows show which part uses which, and the dashed boxes are things "
  + "outside it. Red arrows are parts that need each other. Scroll to zoom, drag to move.";

const archPlural = (n, word) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;
const archById = id => (D.architecture.components.find(c => c.id === id) || D.architecture.externals.find(x => x.id === id));

function archOverlay(c) {
  if (ARCH.overlay === "tests") {
    if (!c.tests.symbols) return " ov-none";
    const pct = 100 * c.tests.reached / c.tests.symbols;
    return pct >= 80 ? " ov-good" : pct >= 40 ? " ov-mid" : " ov-low";
  }
  if (ARCH.overlay === "safety") return c.findings.high ? " ov-low" : c.findings.medium ? " ov-mid" : " ov-good";
  return "";
}

function archLink(f) {
  const A = D.architecture, id = A && A.fileMap[f.path];
  if (!id) return null;
  const c = archById(id);
  return h("div", { class: "dsec" }, h("button", { class: "ar-partof", onclick: () => { ARCH.sel = { type: "comp", id }; state.comp = id; setTab("map"); } },
    `Part of “${c.name}” — show it on the map`));
}

function renderArch(view) {
  const A = D.architecture;
  if (!A || !A.components.length) { view.append(h("div", { id: "arch", class: "empty" }, "There is no code to draw a map of yet.")); return; }
  if (!ARCH.sel && state.comp && archById(state.comp)) ARCH.sel = { type: archById(state.comp).kind ? "ext" : "comp", id: state.comp };
  ARCH.mermaid = false; ARCH.allFiles = false;
  const svg = s("svg", { class: "ar-svg", role: "img", "aria-label": "Map of the project", viewBox: "0 0 10 10" },
    s("defs", {}, s("marker", { id: "ar-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 9, markerHeight: 9, markerUnits: "userSpaceOnUse", orient: "auto-start-reverse" },
      s("path", { d: "M0 0L10 5L0 10z", class: "ar-arrowhead" }))),
    s("g", { class: "ar-g" }, s("g", { class: "ar-edges" }, A.edges.map(archEdgeEl)),
      s("g", { class: "ar-nodes" }, A.components.map(archCompEl), A.externals.map(archExtEl))));
  ARCH.svg = svg;
  svg.addEventListener("click", () => archSelect(null));
  archPanZoom(svg);
  const sum = A.summary;
  const how = { folders: "grouped by folder", files: "one box per file", clusters: "grouped by which files import each other" }[sum.grouping] || "";
  const bar = h("div", { class: "ar-bar" },
    h("label", {}, "Colour by ", h("select", { id: "ar-overlay", onchange: e => { ARCH.overlay = e.target.value; archPaintOverlay(); } },
      [["none", "nothing"], ["tests", "how much is tested"], ["safety", "safety findings"]].map(([v, l]) => h("option", { value: v, selected: ARCH.overlay === v }, l)))),
    h("button", { id: "ar-fit", onclick: archFit }, "Fit"),
    h("button", { id: "ar-mermaid", onclick: () => { ARCH.mermaid = !ARCH.mermaid; archInfo(); } }, "Copy as Mermaid"),
    h("button", { id: "ar-svg", onclick: archDownload }, "Download SVG"),
    h("span", { class: "hint" }, `${archPlural(sum.components, "part")}, ${archPlural(sum.externals, "outside system")}, ${how}`
      + (sum.cycles ? `, ${archPlural(sum.cycles, "loop")} between parts` : "")));
  const canvas = h("div", { class: "ar-canvas" }, svg);
  view.append(h("div", { id: "arch", class: "arch" }, bar, h("div", { class: "ar-body" }, canvas, h("aside", { class: "ar-info", "aria-live": "polite" })),
    h("p", { class: "hint" }, AR_LEGEND + " The grouping is a best guess from the folder layout.")));
  const r = canvas.getBoundingClientRect();
  ARCH.cw = Math.max(320, r.width || 900); ARCH.ch = Math.max(300, r.height || 560);
  svg.setAttribute("viewBox", `0 0 ${ARCH.cw} ${ARCH.ch}`);
  archFit();
  archPaintSelection();
  archInfo();
}

// ---------- drawing ----------
function archCompEl(c) {
  const el = s("g", { class: "ar-comp" + (c.cycle ? " cycle" : "") + (c.entry ? " entry" : "") + archOverlay(c) + (ARCH.sel && ARCH.sel.id === c.id ? " sel" : ""),
    "data-id": c.id, "data-name": c.name, transform: `translate(${+c.x},${+c.y})`, role: "button", tabindex: "0",
    "aria-label": `${c.name}: ${archPlural(c.files, "file")}${c.doc ? ". " + c.doc : ""}`,
    onclick: ev => { ev.stopPropagation(); archSelect({ type: "comp", id: c.id }); },
    onkeydown: ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); archSelect({ type: "comp", id: c.id }); } } },
    s("title", {}, c.doc ? `${c.name}: ${c.doc}` : c.name), s("rect", { width: +c.w, height: +c.h, rx: 8 }),
    s("text", { class: "ar-name", "text-anchor": "middle" }, s("tspan", { x: c.w / 2, y: 30 }, c.name.length > 24 ? c.name.slice(0, 23) + "…" : c.name),
      s("tspan", { class: "ar-sub", x: c.w / 2, y: 52 }, `${archPlural(c.files, "file")} · ${fmt(c.loc)} lines`)),
    c.entry ? s("text", { class: "ar-entry", x: c.w - 8, y: 14, "text-anchor": "end" }, "▶ starts here") : null);
  return el;
}

function archExtEl(x) {
  return s("g", { class: "ar-ext" + (ARCH.sel && ARCH.sel.id === x.id ? " sel" : ""), "data-id": x.id, "data-kind": x.kind, transform: `translate(${+x.x},${+x.y})`,
    role: "button", tabindex: "0", "aria-label": `${x.label}: ${x.modules.join(", ")}`,
    onclick: ev => { ev.stopPropagation(); archSelect({ type: "ext", id: x.id }); },
    onkeydown: ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); archSelect({ type: "ext", id: x.id }); } } },
    s("title", {}, `${x.label} (${x.modules.join(", ")})`), s("rect", { width: +x.w, height: +x.h, rx: 14 }),
    s("text", { class: "ar-name", "text-anchor": "middle" }, s("tspan", { x: x.w / 2, y: 22 }, x.label),
      s("tspan", { class: "ar-sub", x: x.w / 2, y: 40 }, x.modules.slice(0, 2).join(", ") + (x.modules.length > 2 ? "…" : ""))));
}

function archEdgeEl(e) {
  const d = e.path.map(([x, y], i) => `${i ? "L" : "M"}${+x} ${+y}`).join(" ");
  const toExt = e.to.startsWith("x:"), n = e.imports + e.calls;
  const label = toExt ? archPlural(e.files, "file") : e.imports ? String(e.imports) : `${e.calls} calls`;
  return s("g", { class: "ar-edge" + (e.cycle ? " ar-cycle" : "") + (toExt ? " ar-xedge" : ""), "data-from": e.from, "data-to": e.to },
    s("path", { class: "ar-line", d, "marker-end": "url(#ar-arrow)", style: `stroke-width:${(1.2 + Math.min(2.2, Math.log2(n + 1) * 0.7)).toFixed(1)}px` }),
    s("path", { class: "ar-hit", d, onclick: ev => { ev.stopPropagation(); archSelect({ type: "edge", from: e.from, to: e.to }); } }),
    s("text", { class: "ar-elabel", x: +e.lx + 6, y: +e.ly - 3 }, label));
}

function archPaintOverlay() {
  for (const el of document.querySelectorAll("#arch .ar-comp")) {
    const c = archById(el.dataset.id);
    el.setAttribute("class", "ar-comp" + (c.cycle ? " cycle" : "") + (c.entry ? " entry" : "") + archOverlay(c) + (ARCH.sel && ARCH.sel.id === c.id ? " sel" : ""));
  }
}

function archPaintSelection() {
  for (const el of document.querySelectorAll("#arch .ar-comp, #arch .ar-ext")) el.classList.toggle("sel", !!ARCH.sel && ARCH.sel.id === el.dataset.id);
  for (const el of document.querySelectorAll("#arch .ar-edge")) el.classList.toggle("sel", !!ARCH.sel && ARCH.sel.type === "edge" && ARCH.sel.from === el.dataset.from && ARCH.sel.to === el.dataset.to);
}

function archSelect(sel) {
  ARCH.sel = sel; ARCH.mermaid = false; ARCH.allFiles = false;
  state.comp = sel && sel.type !== "edge" ? sel.id : "";
  writeHash();
  archPaintSelection();
  archInfo();
}

// ---------- pan and zoom ----------
function archApply() {
  const g = ARCH.svg && ARCH.svg.querySelector(".ar-g");
  if (g) g.setAttribute("transform", `translate(${+ARCH.tx.toFixed(2)},${+ARCH.ty.toFixed(2)}) scale(${+ARCH.k.toFixed(4)})`);
}
function archFit() {
  const A = D.architecture, m = 16;
  ARCH.k = Math.max(0.15, Math.min(1.25, (ARCH.cw - 2 * m) / A.w, (ARCH.ch - 2 * m) / A.h));
  ARCH.tx = (ARCH.cw - A.w * ARCH.k) / 2; ARCH.ty = Math.max(m, (ARCH.ch - A.h * ARCH.k) / 2);
  archApply();
}
function archPanZoom(svg) {
  svg.addEventListener("wheel", ev => {
    ev.preventDefault();
    const r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) * (ARCH.cw / r.width), sy = (ev.clientY - r.top) * (ARCH.ch / r.height);
    const nk = Math.max(0.15, Math.min(4, ARCH.k * (ev.deltaY < 0 ? 1.15 : 1 / 1.15)));
    ARCH.tx = sx - (sx - ARCH.tx) / ARCH.k * nk; ARCH.ty = sy - (sy - ARCH.ty) / ARCH.k * nk; ARCH.k = nk;
    archApply();
  }, { passive: false });
  svg.addEventListener("pointerdown", ev => {
    if (ev.target.closest && ev.target.closest(".ar-comp, .ar-ext, .ar-hit")) return;
    const x0 = ev.clientX, y0 = ev.clientY, tx0 = ARCH.tx, ty0 = ARCH.ty, r = svg.getBoundingClientRect();
    svg.setPointerCapture(ev.pointerId);
    const move = e2 => { ARCH.tx = tx0 + (e2.clientX - x0) * (ARCH.cw / r.width); ARCH.ty = ty0 + (e2.clientY - y0) * (ARCH.ch / r.height); archApply(); };
    const up = () => { svg.removeEventListener("pointermove", move); svg.removeEventListener("pointerup", up); };
    svg.addEventListener("pointermove", move); svg.addEventListener("pointerup", up);
  });
}

// ---------- the side panel ----------
function archGo(id) { archSelect({ type: archById(id).kind ? "ext" : "comp", id }); }

function archFileLink(p) {
  return h("a", { class: "link ar-file", tabindex: 0, onclick: () => openFile(p), onkeydown: e => { if (e.key === "Enter") openFile(p); } }, p);
}

function archInfo() {
  const box = document.querySelector("#arch .ar-info");
  if (!box) return;
  if (ARCH.mermaid) { box.replaceChildren(archMermaidPanel()); return; }
  const sel = ARCH.sel, A = D.architecture;
  let parts;
  if (!sel) parts = [h("p", { class: "hint" }, "Click a part to see what it does, or an arrow to see the imports behind it.")];
  else if (sel.type === "comp") parts = archCompInfo(archById(sel.id));
  else if (sel.type === "ext") parts = archExtInfo(archById(sel.id));
  else parts = archEdgeInfo(A.edges.find(e => e.from === sel.from && e.to === sel.to));
  box.replaceChildren(...parts.filter(Boolean));
}

function archList(title, items) {
  return items.length ? h("div", {}, h("h3", {}, title), h("ul", { class: "ar-list" }, items.map(i => h("li", {}, i)))) : null;
}

function archCompInfo(c) {
  const A = D.architecture, [verbOut, verbIn] = AR_VERBS[LEVEL];
  const go = (id, label) => h("a", { class: "link ar-go", tabindex: 0, onclick: () => archGo(id) }, label);
  const count = e => (LEVEL === "tech" ? `×${e.imports || e.calls}` : e.imports ? archPlural(e.imports, "import") : archPlural(e.calls, "call"));
  const out = A.edges.filter(e => e.from === c.id && !e.to.startsWith("x:")), inn = A.edges.filter(e => e.to === c.id && !e.from.startsWith("x:"));
  const outside = A.edges.filter(e => e.from === c.id && e.to.startsWith("x:"));
  const t = c.tests, f = c.findings;
  const size = LEVEL === "kid" ? `This room has ${archPlural(c.files, "file")}.` : LEVEL === "plain" ? `${archPlural(c.files, "file")}, ${fmt(c.loc)} lines of code.`
    : `${archPlural(c.files, "file")} · ${fmt(c.loc)} LOC · complexity ${c.complexity}`;
  const shown = ARCH.allFiles ? Object.keys(A.fileMap).filter(p => A.fileMap[p] === c.id).sort() : c.top.slice(0, AR_SHOWN);
  return [
    h("h3", { class: "ar-title" }, c.name), c.entry ? h("span", { class: "badge ok" }, "starts here") : null, c.cycle ? h("span", { class: "badge warn" }, "in a loop") : null,
    c.doc ? h("p", {}, c.doc) : null, h("p", { class: "hint" }, size),
    c.cycle ? h("p", { class: "ar-warn" }, "This part and others need each other (an import circle), which makes them hard to change separately.") : null,
    archList(`${c.name} ${verbOut}:`, out.map(e => [go(e.to, archById(e.to).name), ` · ${count(e)}`])),
    archList(`${c.name} ${verbIn}:`, inn.map(e => [go(e.from, archById(e.from).name), ` · ${count(e)}`])),
    archList(LEVEL === "kid" ? "It talks to the outside world:" : "Talks to:", outside.map(e => { const x = archById(e.to); return [go(e.to, x.label), ` (${x.modules.join(", ")})`]; })),
    t.symbols ? h("p", {}, `Tests reach ${t.reached} of ${archPlural(t.symbols, "function")} (${Math.round(100 * t.reached / t.symbols)}%).`) : null,
    f.total ? h("p", {}, `${archPlural(f.total, "safety finding")} worth a look` + (f.high ? ` (${f.high} serious)` : "") + ". See the Health tab.") : null,
    archList(ARCH.allFiles ? "All files" : "Most used files", shown.map(archFileLink)),
    !ARCH.allFiles && c.files > shown.length ? h("button", { class: "ar-allfiles", onclick: () => { ARCH.allFiles = true; archInfo(); } }, `Show all ${c.files} files`) : null,
  ];
}

function archExtInfo(x) {
  const A = D.architecture, users = A.edges.filter(e => e.to === x.id);
  return [h("h3", { class: "ar-title" }, x.label), h("span", { class: "badge" }, "outside the project"),
    h("p", { class: "hint" }, `Libraries: ${x.modules.join(", ")}`),
    archList("Used by:", users.map(e => [h("a", { class: "link ar-go", tabindex: 0, onclick: () => archGo(e.from) }, archById(e.from).name),
      ` · ${archPlural(e.files, "file")}: `, e.examples.slice(0, 3).map(([p], i) => [i ? ", " : "", archFileLink(p)])]))];
}

function archEdgeInfo(e) {
  if (!e) return [h("p", { class: "hint" }, "That arrow is gone; the project changed.")];
  const [verb] = AR_VERBS[LEVEL], a = archById(e.from), b = archById(e.to);
  const what = e.to.startsWith("x:") ? archPlural(e.files, "file") : [e.imports ? archPlural(e.imports, "import") : null, e.calls ? archPlural(e.calls, "call") : null].filter(Boolean).join(", ");
  return [h("h3", { class: "ar-title" }, `${a.name} → ${b.name || b.label}`), h("p", {}, `${a.name} ${verb} ${b.name || b.label}: ${what}.`),
    e.cycle ? h("p", { class: "ar-warn" }, "These parts are in an import circle.") : null,
    archList(e.to.startsWith("x:") ? "Examples (file and library)" : "Examples (importing file → imported file)",
      e.examples.map(([p, q]) => [archFileLink(p), e.to.startsWith("x:") ? ` → ${q}` : [" → ", archFileLink(q)]]))];
}

// ---------- Mermaid and SVG export ----------
function archMermaid() {
  const A = D.architecture, id = {};
  A.components.forEach((c, i) => { id[c.id] = `c${i}`; });
  A.externals.forEach((x, i) => { id[x.id] = `x${i}`; });
  const lines = ["flowchart TB"];
  for (const c of A.components) lines.push(`  ${id[c.id]}["${mermaidSafe(`${c.name} (${archPlural(c.files, "file")})`)}"]`);
  for (const x of A.externals) lines.push(x.kind === "database" ? `  ${id[x.id]}[("${mermaidSafe(x.label)}")]` : `  ${id[x.id]}(["${mermaidSafe(x.label)}"])`);
  for (const e of A.edges) {
    const label = e.to.startsWith("x:") ? archPlural(e.files, "file") : e.imports ? archPlural(e.imports, "import") : archPlural(e.calls, "call");
    lines.push(`  ${id[e.from]} -- "${label}" --> ${id[e.to]}`);
  }
  return lines.join("\n");
}
function archMermaidPanel() {
  const ta = h("textarea", { readonly: "readonly", rows: 14, "aria-label": "Mermaid text" });
  ta.value = archMermaid();
  return h("div", { class: "ar-mermaid" }, h("h3", {}, "Mermaid text"),
    h("p", { class: "hint" }, "Paste this into a ```mermaid block in GitHub, a README or a pull request to show the same map."),
    ta, h("button", { onclick: () => { ta.select(); try { navigator.clipboard.writeText(ta.value); } catch (e) { /* the text is selected; copy by hand */ } } }, "Copy"));
}
const AR_EXPORT_CSS = `.ar-line{fill:none;stroke:#6b7385}.ar-cycle .ar-line{stroke:#c2410c;stroke-dasharray:5 4}.ar-hit{display:none}.ar-arrowhead{fill:#6b7385}
.ar-elabel{font:11px sans-serif;fill:#6b7385}.ar-comp rect{fill:#ffffff;stroke:#3b6fe0;stroke-width:1.6}.ar-comp.cycle rect{stroke:#c2410c}
.ar-ext rect{fill:#f1f3f7;stroke:#6b7385;stroke-dasharray:5 3}.ar-name{font:600 13px sans-serif;fill:#1d2330}.ar-sub{font:11px sans-serif;font-weight:400;fill:#6b7385}
.ar-entry{font:10px sans-serif;fill:#15803d}.ov-good rect{fill:#e5f6ea}.ov-mid rect{fill:#fdf3d6}.ov-low rect{fill:#fbe3dc}.ov-none rect{fill:#f1f3f7}`;
function archDownload() {
  const svg = ARCH.svg.cloneNode(true), A = D.architecture;
  svg.querySelector(".ar-g").removeAttribute("transform");
  for (const el of svg.querySelectorAll("[tabindex],[role]")) { el.removeAttribute("tabindex"); el.removeAttribute("role"); }
  svg.setAttribute("xmlns", SVGNS); svg.setAttribute("viewBox", `0 0 ${A.w} ${A.h}`); svg.setAttribute("width", A.w); svg.setAttribute("height", A.h);
  const style = document.createElementNS(SVGNS, "style");
  style.textContent = AR_EXPORT_CSS;
  svg.insertBefore(style, svg.firstChild);
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml" }));
  const a = h("a", { href: url, download: "architecture.svg" });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
