(() => {
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const FILES = D.files;
const BY = new Map(FILES.map(f => [f.path, f]));
const SVGNS = "http://www.w3.org/2000/svg";

// ---------- helpers ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null) el.append(kid);
  return el;
}
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null) el.append(kid);
  return el;
}
const fmt = n => n.toLocaleString();
const base = p => p.slice(p.lastIndexOf("/") + 1);
const dirOf = p => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
const PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#b07aa1", "#edc948", "#76b7b2", "#e15759", "#9c755f", "#af7aa1", "#86bcb6", "#bab0ac"];
const langs = Object.entries(D.repo.languages).sort((a, b) => b[1].loc - a[1].loc);
const LANG_COLOR = Object.fromEntries(langs.map(([n], i) => [n, PALETTE[i % PALETTE.length]]));
const maxOf = k => Math.max(1, ...FILES.map(f => k(f)));

const SYMS = new Map();
for (const f of FILES) for (const sym of f.symbols) SYMS.set(`${f.path}::${sym.qual}`, { sym, file: f });
const EX = D.explain || { folders: {}, story: [], glossary: [] };
let LEVEL = "kid";
try { LEVEL = localStorage.getItem("ce.level") || "kid"; } catch (e) { /* storage may be blocked */ }
if (!["kid", "plain", "tech"].includes(LEVEL)) LEVEL = "kid";
// Text objects look like {kid, plain}; Technical level reads the plain variant where text is needed.
const T = o => (o ? (LEVEL === "kid" ? o.kid : o.plain) || "" : "");
const firstSentence = (t, n = 140) => { const m = t.match(/^.*?[.!?](\s|$)/); const x = (m ? m[0] : t).trim(); return x.length > n ? x.slice(0, n - 1) + "…" : x; };
// `code` spans in generated text become <code> elements.
const rich = t => String(t).split(/`([^`]+)`/).map((part, i) => (i % 2 ? h("code", {}, part) : part));
const CH = D.changes || null;
const state = { tab: "story", storyStep: 0, symOpen: "", selected: null, colorBy: CH ? "changed" : "lang", tmRoot: "", expanded: new Set(),
  flowRoot: "", flowDepth: 4, flowGuess: true, flowOpen: new Set(), flowStep: -1 };

// ---------- navigation ----------
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("tab")) state.tab = p.get("tab");
  if (p.get("step")) state.storyStep = Math.max(0, +p.get("step") || 0);
  const f = p.get("f");
  state.selected = f && BY.has(f) ? f : null;
  if (p.get("line")) state.line = +p.get("line"); else state.line = 0;
  if (p.get("root") && SYMS.has(p.get("root"))) state.flowRoot = p.get("root");
}
function writeHash() {
  const p = new URLSearchParams();
  p.set("tab", state.tab);
  if (state.selected) p.set("f", state.selected);
  if (state.line) p.set("line", state.line);
  if (state.tab === "flow" && state.flowRoot) p.set("root", state.flowRoot);
  if (state.tab === "story") p.set("step", state.storyStep);
  history.replaceState(null, "", "#" + p);
}
function openFile(path, line) {
  state.selected = path; state.line = line || 0;
  writeHash(); renderDetail(); markSelected();
}
function setTab(t) { state.tab = t; writeHash(); renderAll(); }

// ---------- shell ----------
function renderShell() {
  const r = D.repo;
  const brand = document.getElementById("brand");
  brand.replaceChildren(r.name, h("small", {}, `${fmt(r.files)} files · ${fmt(r.loc)} lines` + (r.git && r.git.head ? ` · ${r.git.head}` : "")));
  for (const b of document.querySelectorAll("#tabs button")) b.onclick = () => setTab(b.dataset.tab);
  document.querySelector('#tabs [data-tab="changes"]').hidden = !CH;
  const sel = document.getElementById("level");
  sel.value = LEVEL;
  sel.onchange = () => { LEVEL = sel.value; try { localStorage.setItem("ce.level", LEVEL); } catch (e) { /* ignore */ } renderAll(); };
}
function renderAll() {
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("on", b.dataset.tab === state.tab);
  const view = document.getElementById("view");
  view.replaceChildren();
  ({ story: renderStory, changes: renderChanges, words: renderWords, overview: renderOverview, flow: renderFlow, treemap: renderTreemap, graph: renderGraph }[state.tab] || renderOverview)(view);
  renderDetail();
}
function markSelected() {
  for (const el of document.querySelectorAll("[data-path]")) el.classList.toggle("sel", el.dataset.path === state.selected);
}

// ---------- story (guided tour) ----------
function renderStory(view) {
  const steps = EX.story;
  if (!steps.length) { view.append(h("div", { class: "empty" }, "Nothing to tell yet.")); return; }
  state.storyStep = Math.max(0, Math.min(steps.length - 1, state.storyStep));
  const st = steps[state.storyStep];
  const go = i => { state.storyStep = i; writeHash(); renderAll(); if (steps[i].path) openFile(steps[i].path, steps[i].line); };
  const dots = h("div", { class: "dots", role: "tablist", "aria-label": "Tour steps" }, steps.map((x, i) =>
    h("button", { class: "dot2" + (i === state.storyStep ? " on" : i < state.storyStep ? " done" : ""), title: T(x.title), "aria-label": `Step ${i + 1}: ${T(x.title)}`, onclick: () => go(i) }, String(i + 1))));
  const paras = T(st.text).split("\n").map(line => h("p", {}, rich(line)));
  const hit = st.symbol && SYMS.get(st.symbol);
  const src = hit && hit.file.source ? hit.file.source.split("\n").slice(hit.sym.line - 1, Math.min(hit.sym.end_line, hit.sym.line + 24)).join("\n") : "";
  const card = h("article", { class: "card story" + (LEVEL === "kid" ? " kid" : "") },
    h("div", { class: "kicker" }, `Step ${state.storyStep + 1} of ${steps.length}`),
    h("h1", {}, T(st.title)), paras,
    src ? h("pre", { class: "snip" }, src + (hit.sym.end_line - hit.sym.line >= 25 ? "\n…" : "")) : null,
    h("div", { class: "nav2" },
      h("button", { onclick: () => go(state.storyStep - 1), disabled: state.storyStep === 0 }, "◀ Back"),
      st.path ? h("button", { onclick: () => openFile(st.path, st.line) }, "Show me the code") : null,
      h("button", { class: "primary", onclick: () => go(state.storyStep + 1), disabled: state.storyStep === steps.length - 1 }, "Next ▶")));
  view.append(dots, card, h("p", { class: "hint" },
    "Everything here is worked out from the code itself: names, comments, who borrows from whom, who asks whom for help. "
    + "When something is only a guess from a name, the text says so. Use the “Explain” menu at the top to change how it is told."));
  markSelected();
}

// ---------- changes (review of a diff) ----------
const STATUS = { A: ["new", "ok"], M: ["changed", ""], D: ["removed", "warn"], R: ["moved", ""] };
function diffEl(patch) {
  return h("pre", { class: "patch" }, patch.split("\n").map(line => h("span", { class: line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "" }, line + "\n")));
}
function renderChanges(view) {
  const s = CH.summary;
  const card = (n, l) => h("div", { class: "card" }, h("div", { class: "n" }, String(n)), h("div", { class: "l" }, l));
  view.append(
    h("p", { class: "hint", style: "margin-top:0" }, `Comparing ${CH.base} → ${CH.head}`),
    h("div", { class: "grid" },
      card(s.files, `files touched (${s.added} new, ${s.modified} changed, ${s.deleted} removed)`),
      card(`+${fmt(s.linesAdded)} / −${fmt(s.linesDeleted)}`, "lines"),
      card(s.impactedFiles, "other files that depend on the change"),
      card((s.complexityDelta > 0 ? "+" : "") + s.complexityDelta, "complexity change"),
      card(s.newDead + s.risks + s.newDuplicates, "things to double-check")));
  const flags = [];
  for (const r of CH.risks) flags.push(h("li", { onclick: () => BY.has(r.what.split("::")[0]) ? openFile(r.what.split("::")[0]) : null },
    h("span", { class: "badge warn" }, "removed but used"), h("span", {}, h("span", { class: "p" }, r.what), h("span", { class: "why" }, `${r.why}: ${r.users.join(", ")}`))));
  for (const d of CH.newDead) flags.push(h("li", { onclick: () => BY.has(d.path) && openFile(d.path, d.line) },
    h("span", { class: "badge warn" }, d.kind === "orphaned" ? "lost its caller" : d.kind === "unused-file" ? "unused file" : "unused"), h("span", {}, h("span", { class: "p" }, `${d.path}${d.qual ? " → " + d.qual : ""}`), h("span", { class: "why" }, d.why))));
  for (const g of CH.newDuplicates) flags.push(h("li", { onclick: () => BY.has(g.members[0].path) && openFile(g.members[0].path, g.members[0].line) },
    h("span", { class: "badge warn" }, "copy-paste"), h("span", {}, h("span", { class: "p" }, g.members.map(m => m.qual).join(" = ")), h("span", { class: "why" }, `${g.lines} identical lines in ${g.members.map(m => base(m.path)).join(", ")}`))));
  if (flags.length) view.append(h("h2", {}, "Double-check these", h("small", {}, "possible leftovers and risks")), h("ul", { class: "list" }, flags));
  if (CH.reach.length) view.append(h("h2", {}, "Runs from", h("small", {}, "starting points that can reach the changed code")),
    h("ul", { class: "list" }, CH.reach.map(r => h("li", { onclick: () => { state.flowRoot = r.root; state.flowOpen.clear(); setTab("flow"); } },
      h("span", {}, h("span", { class: "p" }, r.name), h("span", { class: "why" }, `${r.why.replace(/`/g, "")} · reaches ${r.count} changed ${r.count === 1 ? "function" : "functions"}`))))));
  view.append(h("h2", {}, "Read in this order", h("small", {}, "the riskiest and most connected first, tests last")));
  CH.reviewOrder.forEach((path, i) => {
    const e = CH.files[path], st = STATUS[e.status] || ["changed", ""];
    const open = state.patchOpen && state.patchOpen.has(path);
    const sy = e.symbols;
    view.append(h("div", { class: "card chg" },
      h("div", { class: "chead" }, h("span", { class: "num" }, String(i + 1)), h("span", { class: "badge " + st[1] }, st[0]),
        h("a", { class: "link", style: "display:inline", onclick: () => BY.has(path) && openFile(path) }, e.oldPath ? `${e.oldPath} → ${path}` : path),
        h("span", { class: "loc" }, `+${e.added} / −${e.deleted}`)),
      h("div", { class: "chips" }, e.why.map(w => h("span", { class: "badge" }, w))),
      LEVEL !== "tech" ? h("div", { class: "simple" }, rich(T(e.explain))) : h("div", { class: "hint" },
        ["added", "modified", "removed"].filter(k => sy[k].length).map(k => `${k}: ${sy[k].join(", ")}`).join(" · ")),
      e.blast.length ? h("div", { class: "hint" }, "Affects: ", e.blast.slice(0, 8).map((b, j) => [j ? ", " : "", h("a", { class: "link", style: "display:inline", onclick: () => BY.has(b.path) && openFile(b.path) }, base(b.path) + (b.distance > 1 ? ` (${b.distance} steps away)` : ""))]), e.blast.length > 8 ? ` and ${e.blast.length - 8} more` : "") : null,
      h("button", { onclick: () => { state.patchOpen = state.patchOpen || new Set(); open ? state.patchOpen.delete(path) : state.patchOpen.add(path); renderAll(); } }, open ? "Hide the changes" : "Show the changes"),
      open ? (e.binary ? h("div", { class: "hint" }, "Binary file.") : diffEl(e.patch)) : null));
  });
  markSelected();
}

// ---------- words (glossary) ----------
function renderWords(view) {
  const q = h("input", { type: "search", placeholder: "Find a word…", "aria-label": "Filter words", oninput: () => draw() });
  const box = h("div", { class: "words" });
  function draw() {
    const f = q.value.trim().toLowerCase();
    box.replaceChildren(...EX.glossary.filter(g => !f || g.term.toLowerCase().includes(f) || g.kid.toLowerCase().includes(f) || g.plain.toLowerCase().includes(f))
      .map(g => h("div", { class: "card" }, h("h3", { style: "margin:0 0 4px;font-size:15px" }, g.term), h("div", {}, LEVEL === "kid" ? g.kid : g.plain),
        LEVEL === "kid" ? h("div", { class: "hint" }, "Grown-up version: " + g.plain) : null)));
  }
  view.append(h("div", { class: "toolbar" }, q), box); draw();
}

// ---------- overview ----------
function fileRow(f, i, why) {
  return h("li", { tabindex: 0, "data-path": f.path, onclick: () => openFile(f.path), onkeydown: e => { if (e.key === "Enter") openFile(f.path); } },
    i != null ? h("span", { class: "num" }, String(i)) : null,
    h("span", {}, h("span", { class: "p" }, f.path), why ? h("span", { class: "why" }, why) : null));
}
function listOf(title, sub, rows) {
  return h("div", {}, h("h2", {}, title, h("small", {}, sub)),
    rows.length ? h("ul", { class: "list" }, rows) : h("div", { class: "list empty" }, "Nothing to show."));
}
function renderOverview(view) {
  const r = D.repo;
  const total = Math.max(1, r.loc);
  const bar = h("div", { class: "bar" }, langs.filter(([, v]) => v.loc).map(([n, v]) =>
    h("span", { title: `${n}: ${fmt(v.loc)} lines`, style: `width:${(100 * v.loc / total).toFixed(2)}%;background:${LANG_COLOR[n]}` })));
  const legend = h("div", { class: "legend" }, langs.filter(([, v]) => v.loc).slice(0, 8).map(([n, v]) =>
    h("span", {}, h("i", { class: "dot", style: `background:${LANG_COLOR[n]}` }), `${n} ${Math.round(100 * v.loc / total)}%`)));
  const code = FILES.filter(f => f.analyzed);
  const edges = D.edges.length;
  view.append(
    h("div", { class: "grid" },
      [[fmt(r.files), "files"], [fmt(r.loc), "lines of code"], [fmt(code.length), "files parsed"], [fmt(edges), "internal imports"],
       [r.git && r.git.commits != null ? fmt(r.git.commits) : "n/a", "commits"]].map(([n, l]) =>
        h("div", { class: "card" }, h("div", { class: "n" }, n), h("div", { class: "l" }, l)))),
    bar, legend,
    h("div", { class: "cols" },
      listOf("Start here", "read in this order",
        D.readingOrder.map((o, i) => fileRow(BY.get(o.path), i + 1, o.why))),
      listOf("Hotspots", "most complex files",
        FILES.filter(f => f.analyzed && !f.isTest).sort((a, b) => b.complexity - a.complexity).slice(0, 10)
          .map(f => fileRow(f, null, `complexity ${f.complexity} · ${fmt(f.loc)} lines`))),
      listOf("Possibly unused", "no file imports these and they are not entry points",
        FILES.filter(f => f.unused).sort((a, b) => b.loc - a.loc).slice(0, 10)
          .map(f => fileRow(f, null, `${fmt(f.loc)} lines`))),
      D.duplicates && D.duplicates.length ? listOf("Copy-pasted code", "identical function bodies in several places",
        D.duplicates.slice(0, 8).map(g => h("li", { tabindex: 0, onclick: () => openFile(g.members[0].path, g.members[0].line) },
          h("span", {}, h("span", { class: "p" }, g.members.map(m => m.qual).join(" = ")),
            h("span", { class: "why" }, `${g.lines} lines × ${g.members.length} copies · ${g.members.map(m => base(m.path)).join(", ")}`))))) : null,
      r.hasHistory ? listOf("Most changed", "commits touching the file",
        FILES.filter(f => f.churn).sort((a, b) => b.churn - a.churn).slice(0, 10)
          .map(f => fileRow(f, null, `${f.churn} commits`))) : null),
    h("p", { class: "hint" }, "Imports are resolved statically, so dynamic imports and framework magic can make a used file look unused. Treat \"possibly unused\" as a place to look, not a verdict."));
  markSelected();
}

// ---------- treemap ----------
const METRICS = {
  lang: { label: "Language" },
  loc: { label: "Size (lines)", val: f => f.loc },
  complexity: { label: "Complexity", val: f => f.complexity },
  churn: { label: "Churn (commits)", val: f => f.churn },
  fanin: { label: "Imported by", val: f => f.importedBy.length },
  unused: { label: "Possibly unused" },
};
if (CH) METRICS.changed = { label: "Changed in this review" };
function buildTree(prefix) {
  const root = { name: prefix || D.repo.name, path: prefix, children: new Map(), dir: true };
  for (const f of FILES) {
    if (prefix && !f.path.startsWith(prefix + "/")) continue;
    const parts = f.path.slice(prefix ? prefix.length + 1 : 0).split("/");
    let node = root;
    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1;
      let next = node.children.get(part);
      if (!next) {
        next = { name: part, path: (node.path ? node.path + "/" : "") + part, children: new Map(), dir: !isFile, file: isFile ? f : null };
        node.children.set(part, next);
      }
      node = next;
    });
  }
  const total = n => (n.value = n.file ? Math.max(n.file.loc, 1) : [...n.children.values()].reduce((a, c) => a + total(c), 0));
  total(root);
  return root;
}
function worst(row, sum, side) {
  const s2 = side * side; let mx = 0, mn = Infinity;
  for (const r of row) { mx = Math.max(mx, r.area); mn = Math.min(mn, r.area); }
  return Math.max(s2 * mx / (sum * sum), (sum * sum) / (s2 * mn));
}
function squarify(items, x, y, w, hgt) {
  const out = []; let rest = items.slice();
  while (rest.length) {
    const side = Math.min(w, hgt); const row = []; let sum = 0, best = Infinity;
    while (rest.length) {
      const it = rest[0];
      const wr = worst([...row, it], sum + it.area, side);
      if (row.length && wr > best) break;
      row.push(it); sum += it.area; best = wr; rest.shift();
    }
    if (w >= hgt) {
      const rw = sum / hgt; let cy = y;
      for (const it of row) { const ih = it.area / rw; out.push({ it, x, y: cy, w: rw, h: ih }); cy += ih; }
      x += rw; w -= rw;
    } else {
      const rh = sum / w; let cx = x;
      for (const it of row) { const iw = it.area / rh; out.push({ it, x: cx, y, w: iw, h: rh }); cx += iw; }
      y += rh; hgt -= rh;
    }
  }
  return out;
}
function tileColor(f) {
  const m = state.colorBy;
  if (m === "lang") return { fill: LANG_COLOR[f.lang] || "#999", op: 0.85 };
  if (m === "changed") {
    const c = CH.files[f.path];
    return c ? { fill: c.status === "A" ? "#2e9e5b" : "#e0a030", op: 0.95 } : { fill: "#888", op: 0.12 };
  }
  if (m === "unused") return f.unused ? { fill: "#e15759", op: 0.9 } : { fill: "#888", op: 0.15 };
  const mx = METRICS[m].max;
  const t = Math.sqrt(Math.min(1, METRICS[m].val(f) / mx));
  return { fill: "var(--accent)", op: 0.1 + 0.85 * t };
}
function renderTreemap(view) {
  for (const k of Object.keys(METRICS)) if (METRICS[k].val) METRICS[k].max = maxOf(METRICS[k].val);
  const sel = h("select", { "aria-label": "Colour by", onchange: e => { state.colorBy = e.target.value; renderAll(); } },
    Object.entries(METRICS).map(([k, v]) => h("option", { value: k, selected: k === state.colorBy }, v.label)));
  const crumbs = h("span", { class: "crumbs" });
  const parts = state.tmRoot ? state.tmRoot.split("/") : [];
  crumbs.append(h("button", { onclick: () => { state.tmRoot = ""; renderAll(); } }, D.repo.name));
  parts.forEach((p, i) => {
    crumbs.append(h("span", {}, " / "), h("button", { onclick: () => { state.tmRoot = parts.slice(0, i + 1).join("/"); renderAll(); } }, p));
  });
  view.append(h("div", { class: "toolbar" }, h("label", {}, "Colour by ", sel), crumbs,
    h("span", {}, "Box area = lines of code. Click a folder to zoom in, a file to inspect it.")));
  const fx = state.tmRoot && EX.folders[state.tmRoot];
  if (fx && LEVEL !== "tech") view.append(h("div", { class: "simple box" }, (LEVEL === "kid" ? fx.emoji + " " : ""), rich(T(fx))));
  const W = Math.max(480, view.clientWidth - 34), H = Math.max(360, Math.min(window.innerHeight - 170, W * 0.62));
  const svg = s("svg", { id: "tm", viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Treemap of the repository" });
  const root = buildTree(state.tmRoot);
  const HEAD = 15;
  const tip = (g, text) => g.append(s("title", {}, text));
  function layout(node, x, y, w, hgt, depth) {
    const kids = [...node.children.values()].filter(c => c.value > 0).sort((a, b) => b.value - a.value);
    const area = w * hgt, tot = node.value || 1;
    const items = kids.map(c => ({ c, area: Math.max(c.value / tot * area, 0.0001) }));
    for (const r of squarify(items, x, y, w, hgt)) {
      const c = r.it.c; if (r.w < 1.5 || r.h < 1.5) continue;
      if (c.file) {
        const col = tileColor(c.file);
        const g = s("g", { class: "tile", "data-path": c.file.path, onclick: () => openFile(c.file.path) },
          s("rect", { x: r.x, y: r.y, width: r.w, height: r.h, fill: col.fill, "fill-opacity": col.op }));
        tip(g, `${c.file.path}\n${LEVEL !== "tech" && c.file.explain ? firstSentence(T(c.file.explain), 160) + "\n" : ""}${fmt(c.file.loc)} lines · complexity ${c.file.complexity}` + (c.file.churn ? ` · ${c.file.churn} commits` : "") + (c.file.unused ? "\npossibly unused" : ""));
        if (r.w > 46 && r.h > 14) g.append(s("text", { x: r.x + 4, y: r.y + 12, "font-size": 11, "clip-path": "none" }, clip(c.name, r.w)));
        svg.append(g);
      } else {
        const g = s("g", { class: "tile" , onclick: () => { state.tmRoot = c.path; renderAll(); } },
          s("rect", { x: r.x, y: r.y, width: r.w, height: r.h, fill: "var(--hover)", "fill-opacity": 0.6 }));
        tip(g, `${c.path}/\n${fmt(c.value)} lines` + (EX.folders[c.path] && LEVEL !== "tech" ? "\n" + T(EX.folders[c.path]) : ""));
        svg.append(g);
        const showHead = r.w > 40 && r.h > HEAD + 8;
        if (showHead) {
          svg.append(s("text", { x: r.x + 4, y: r.y + 11, "font-size": 11, "font-weight": 600, "pointer-events": "none", fill: "var(--ink)" }, clip(c.name + "/", r.w)));
        }
        if (depth < 3) layout(c, r.x + 1, r.y + (showHead ? HEAD : 1), r.w - 2, r.h - (showHead ? HEAD : 1) - 1, depth + 1);
      }
    }
  }
  if (root.value) layout(root, 0, 0, W, H, 0);
  view.append(svg, legendFor());
  markSelected();
}
function clip(text, w) { const n = Math.max(1, Math.floor((w - 8) / 6.2)); return text.length > n ? text.slice(0, Math.max(1, n - 1)) + "…" : text; }
function legendFor() {
  if (state.colorBy === "lang") return h("div", { class: "legend hint" }, langs.slice(0, 10).map(([n]) => h("span", {}, h("i", { class: "dot", style: `background:${LANG_COLOR[n]}` }), n)));
  if (state.colorBy === "changed") return h("div", { class: "hint legend" }, h("span", {}, h("i", { class: "dot", style: "background:#2e9e5b" }), "new file"), h("span", {}, h("i", { class: "dot", style: "background:#e0a030" }), "changed or moved"), h("span", {}, "grey = untouched"));
  if (state.colorBy === "unused") return h("div", { class: "hint" }, "Red = no other file imports it and it is not an entry point.");
  return h("div", { class: "hint" }, `Darker = higher ${METRICS[state.colorBy].label.toLowerCase()} (max ${fmt(METRICS[state.colorBy].max)}).` +
    (state.colorBy === "churn" && !D.repo.hasHistory ? " No git history was available." : ""));
}

// ---------- dependency graph ----------
const pos = new Map(); // node id -> {x,y}
function visibleNode(path) {
  const parts = path.split("/");
  for (let i = 1; i < parts.length; i++) {
    const dir = parts.slice(0, i).join("/");
    if (!state.expanded.has(dir)) return { id: dir, dir: true };
  }
  return { id: path, dir: false };
}
function renderGraph(view) {
  const nodes = new Map();
  const bump = (n, f) => {
    let e = nodes.get(n.id);
    if (!e) { e = { id: n.id, dir: n.dir, loc: 0, files: 0, lang: f.lang, x: 0, y: 0, vx: 0, vy: 0 }; nodes.set(n.id, e); }
    e.loc += f.loc; e.files++;
    return e;
  };
  const rel = FILES.filter(f => f.imports.length || f.importedBy.length);
  const showIsolated = state.showIsolated;
  for (const f of showIsolated ? FILES : rel) bump(visibleNode(f.path), f);
  const edgeMap = new Map();
  for (const [a, b] of D.edges) {
    const na = visibleNode(a).id, nb = visibleNode(b).id;
    if (na === nb) continue;
    const k = na + "\0" + nb;
    edgeMap.set(k, (edgeMap.get(k) || 0) + 1);
  }
  const edges = [...edgeMap].map(([k, w]) => { const [a, b] = k.split("\0"); return { a: nodes.get(a), b: nodes.get(b), w }; }).filter(e => e.a && e.b);
  const list = [...nodes.values()];

  const W = Math.max(480, view.clientWidth - 34), H = Math.max(380, window.innerHeight - 190);
  // seed positions: reuse old, else near parent folder, else on a circle
  list.forEach((n, i) => {
    const old = pos.get(n.id);
    if (old) { n.x = old.x; n.y = old.y; return; }
    const par = [...pos].find(([id]) => n.id.startsWith(id + "/"));
    const a = (i / list.length) * Math.PI * 2;
    n.x = par ? par[1].x + Math.random() * 30 - 15 : W / 2 + Math.cos(a) * W * 0.3;
    n.y = par ? par[1].y + Math.random() * 30 - 15 : H / 2 + Math.sin(a) * H * 0.3;
  });
  const iters = Math.max(30, Math.min(300, Math.floor(40000000 / Math.max(1, list.length * list.length))));
  const radius = n => (n.dir ? 9 : 5) + Math.min(16, Math.sqrt(n.loc) / (n.dir ? 4 : 5));
  for (let it = 0; it < iters; it++) {
    const cool = 1 - it / iters;
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      const a = list[i], b = list[j]; let dx = a.x - b.x, dy = a.y - b.y; let d2 = dx * dx + dy * dy;
      if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.5; }
      if (d2 > 90000) continue;
      const f = 1800 / d2, d = Math.sqrt(d2);
      a.vx += dx / d * f; a.vy += dy / d * f; b.vx -= dx / d * f; b.vy -= dy / d * f;
    }
    for (const e of edges) {
      const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - 70) * 0.02;
      e.a.vx += dx / d * f; e.a.vy += dy / d * f; e.b.vx -= dx / d * f; e.b.vy -= dy / d * f;
    }
    for (const n of list) {
      n.vx += (W / 2 - n.x) * 0.002; n.vy += (H / 2 - n.y) * 0.002;
      n.x += Math.max(-30, Math.min(30, n.vx)) * cool; n.y += Math.max(-30, Math.min(30, n.vy)) * cool;
      n.vx *= 0.6; n.vy *= 0.6;
    }
  }
  list.forEach(n => pos.set(n.id, { x: n.x, y: n.y }));

  const topDirs = [...new Set(FILES.map(f => f.path.split("/")[0]).filter(x => x))];
  const expandAll = h("button", { onclick: () => { for (const f of FILES) { const p = f.path.split("/"); for (let i = 1; i < p.length; i++) state.expanded.add(p.slice(0, i).join("/")); } renderAll(); } }, "Expand all");
  const collapseAll = h("button", { onclick: () => { state.expanded.clear(); renderAll(); } }, "Collapse all");
  const iso = h("label", {}, h("input", { type: "checkbox", checked: !!showIsolated, onchange: e => { state.showIsolated = e.target.checked; renderAll(); } }), " show files with no imports");
  view.append(h("div", { class: "toolbar" }, expandAll, collapseAll, iso,
    h("span", {}, `${list.length} nodes · ${edges.length} links. Click a folder to expand it, a file to inspect it. Arrows point from importer to imported.`)));
  if (!list.length) { view.append(h("div", { class: "empty" }, "No internal imports were found in this repository.")); return; }

  const svg = s("svg", { id: "gr", viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Module dependency graph" });
  svg.append(s("defs", {}, s("marker", { id: "arr", viewBox: "0 0 10 10", refX: 10, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" },
    s("path", { d: "M0 0L10 5L0 10z", fill: "var(--muted)" }))));
  const g = s("g", {});
  svg.append(g);
  const edgeEls = edges.map(e => {
    const el = s("line", { class: "edge", "stroke-width": Math.min(4, 0.8 + Math.log2(1 + e.w)), "marker-end": "url(#arr)" });
    g.append(el); e.el = el; return el;
  });
  function place() {
    for (const e of edges) {
      const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const ra = radius(e.a), rb = radius(e.b) + 3;
      e.el.setAttribute("x1", e.a.x + dx / d * ra); e.el.setAttribute("y1", e.a.y + dy / d * ra);
      e.el.setAttribute("x2", e.b.x - dx / d * rb); e.el.setAttribute("y2", e.b.y - dy / d * rb);
    }
    for (const n of list) n.el.setAttribute("transform", `translate(${n.x},${n.y})`);
  }
  for (const n of list) {
    const r = radius(n);
    const shape = n.dir ? s("rect", { x: -r, y: -r * 0.75, width: r * 2, height: r * 1.5, rx: 3, fill: "var(--accent)", "fill-opacity": 0.75 })
                        : s("circle", { r, fill: LANG_COLOR[n.lang] || "#999" });
    const label = n.dir ? `${base(n.id)}/ (${n.files})` : base(n.id);
    const el = s("g", { class: "node", "data-path": n.dir ? "" : n.id, tabindex: 0 }, shape,
      s("text", { x: r + 3, y: 4 }, label), s("title", {}, n.dir ? `${n.id}/ · ${n.files} files · ${fmt(n.loc)} lines (click to expand)` + (EX.folders[n.id] && LEVEL !== "tech" ? "\n" + T(EX.folders[n.id]) : "") : n.id));
    n.el = el; g.append(el);
    let moved = false;
    el.addEventListener("pointerdown", ev => {
      ev.stopPropagation(); el.setPointerCapture(ev.pointerId); moved = false;
      const move = e2 => { const p = toLocal(e2); n.x = p.x; n.y = p.y; moved = true; pos.set(n.id, { x: n.x, y: n.y }); place(); };
      const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up);
        if (!moved) { if (n.dir) { state.expanded.add(n.id); pos.set(n.id, { x: n.x, y: n.y }); renderAll(); } else openFile(n.id); } };
      el.addEventListener("pointermove", move); el.addEventListener("pointerup", up);
    });
    el.addEventListener("keydown", ev => { if (ev.key === "Enter") { if (n.dir) { state.expanded.add(n.id); renderAll(); } else openFile(n.id); } });
    el.addEventListener("mouseenter", () => highlight(n)); el.addEventListener("mouseleave", () => highlight(null));
  }
  function highlight(n) {
    const near = new Set(n ? [n] : []);
    for (const e of edges) { const on = n && (e.a === n || e.b === n); e.el.classList.toggle("hl", !!on); if (on) { near.add(e.a); near.add(e.b); } }
    for (const m of list) m.el.classList.toggle("dim", !!n && !near.has(m));
    for (const e of edges) e.el.classList.toggle("dim", !!n && !(e.a === n || e.b === n));
  }
  // pan & zoom
  let tx = 0, ty = 0, k = 1;
  {
    const xs = list.map(n => n.x), ys = list.map(n => n.y);
    const x0 = Math.min(...xs) - 60, x1 = Math.max(...xs) + 120, y0 = Math.min(...ys) - 40, y1 = Math.max(...ys) + 40;
    k = Math.max(0.2, Math.min(1.8, W / (x1 - x0), H / (y1 - y0)));
    tx = (W - (x1 - x0) * k) / 2 - x0 * k; ty = (H - (y1 - y0) * k) / 2 - y0 * k;
  }
  const apply = () => g.setAttribute("transform", `translate(${tx},${ty}) scale(${k})`);
  function toLocal(ev) {
    const r = svg.getBoundingClientRect();
    const sx = (ev.clientX - r.left) * (W / r.width), sy = (ev.clientY - r.top) * (H / r.height);
    return { x: (sx - tx) / k, y: (sy - ty) / k };
  }
  svg.addEventListener("wheel", ev => {
    ev.preventDefault();
    const p = toLocal(ev), nk = Math.max(0.2, Math.min(5, k * (ev.deltaY < 0 ? 1.15 : 1 / 1.15)));
    const r = svg.getBoundingClientRect(); const sx = (ev.clientX - r.left) * (W / r.width), sy = (ev.clientY - r.top) * (H / r.height);
    k = nk; tx = sx - p.x * k; ty = sy - p.y * k; apply();
  }, { passive: false });
  svg.addEventListener("pointerdown", ev => {
    const x0 = ev.clientX, y0 = ev.clientY, tx0 = tx, ty0 = ty; svg.setPointerCapture(ev.pointerId);
    const r = svg.getBoundingClientRect();
    const move = e2 => { tx = tx0 + (e2.clientX - x0) * (W / r.width); ty = ty0 + (e2.clientY - y0) * (H / r.height); apply(); };
    const up = () => { svg.removeEventListener("pointermove", move); svg.removeEventListener("pointerup", up); };
    svg.addEventListener("pointermove", move); svg.addEventListener("pointerup", up);
  });
  apply(); place(); view.append(svg, h("div", { class: "hint" }, "Scroll to zoom, drag the background to pan, drag a node to move it. Hover to highlight a node's neighbours."));
  markSelected();
}

// ---------- flow explorer ----------
function symLabel(sym) { return sym.kind === "module" ? "(file's own top-level code)" : sym.qual + (sym.kind === "class" ? "" : "()"); }
function buildFlow(rootId) {
  const rows = [], seen = new Set();
  (function walk(id, depth, via, conf, parentKey, stack) {
    const hit = SYMS.get(id); if (!hit) return;
    const key = parentKey + ">" + id;
    const repeat = seen.has(id), cyc = stack.includes(id);
    const kids = (hit.sym.callees || []).filter(c => SYMS.has(c.id) && (state.flowGuess || c.conf === "certain"));
    const row = { key, id, depth, sym: hit.sym, file: hit.file, via, conf, repeat, cyc, nKids: kids.length, cut: false };
    rows.push(row); seen.add(id);
    if (repeat || cyc) return;
    if (depth >= state.flowDepth) { row.cut = kids.length > 0; return; }
    for (const c of kids) walk(c.id, depth + 1, c.line, c.conf, key, stack.concat(id));
  })(rootId, 0, 0, "certain", "", []);
  return rows;
}
function visibleFlow(rows) {
  const out = []; let hideBelow = Infinity;
  for (const r of rows) {
    if (r.depth > hideBelow) continue;
    hideBelow = Infinity; out.push(r);
    if (state.flowOpen.has("c:" + r.key)) hideBelow = r.depth;
  }
  return out;
}
function renderFlow(view) {
  const roots = D.flowRoots;
  if (!roots.length) {
    view.append(h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "No starting points found"),
      h("p", {}, "This tab follows what runs, step by step, from a starting point such as a command, a web route, or a script's `main()`. None were found with call information."),
      FILES.some(f => (f.lang === "JavaScript" || f.lang === "TypeScript") && !f.hasCalls)
        ? h("p", {}, "JavaScript/TypeScript call tracing needs tree-sitter: ", h("code", {}, "pip install 'code-explainer[treesitter]'"), ".") : null));
    return;
  }
  if (!SYMS.has(state.flowRoot)) state.flowRoot = roots[0].id;
  const rows = buildFlow(state.flowRoot), vis = visibleFlow(rows);
  if (state.flowStep >= vis.length) state.flowStep = vis.length - 1;
  const sel = h("select", { "aria-label": "Starting point", onchange: e => { state.flowRoot = e.target.value; state.flowOpen.clear(); state.flowStep = -1; writeHash(); renderAll(); } },
    roots.map(r => h("option", { value: r.id, selected: r.id === state.flowRoot }, `${r.name}  —  ${r.why}`)));
  const depth = h("select", { "aria-label": "Depth", onchange: e => { state.flowDepth = +e.target.value; state.flowStep = -1; renderAll(); } },
    [2, 3, 4, 5, 6, 8].map(n => h("option", { value: n, selected: n === state.flowDepth }, `${n} levels`)));
  const guess = h("label", {}, h("input", { type: "checkbox", checked: state.flowGuess, onchange: e => { state.flowGuess = e.target.checked; state.flowStep = -1; renderAll(); } }), " show guesses");
  const go = d => { state.flowStep = Math.max(0, Math.min(vis.length - 1, (state.flowStep < 0 ? (d > 0 ? 0 : 0) : state.flowStep + d))); renderAll(); };
  const nav = h("span", { class: "nav" },
    h("button", { onclick: () => go(-1), disabled: state.flowStep <= 0 }, "◀ Back"),
    h("button", { onclick: () => go(1), disabled: state.flowStep >= vis.length - 1 }, state.flowStep < 0 ? "▶ Step through" : "Next ▶"),
    state.flowStep >= 0 ? h("span", {}, ` step ${state.flowStep + 1} of ${vis.length}`) : null);
  view.append(h("div", { class: "toolbar" }, h("label", {}, "Start from ", sel), depth, guess, nav));
  view.append(h("p", { class: "hint", style: "margin-top:0" },
    "Read top to bottom: each line is a function that the line above calls, in the order the calls appear in the code. Indented lines run inside their parent. ",
    h("span", { class: "badge warn" }, "guess"), " means the link is inferred from the name only."));
  if (state.flowStep >= 0) view.append(stepCard(vis[state.flowStep], vis));
  const list = h("div", { class: "flow", role: "tree" });
  vis.forEach((r, i) => {
    const open = !state.flowOpen.has("c:" + r.key);
    const toggle = r.nKids && !r.repeat && !r.cyc && !r.cut
      ? h("button", { class: "tg", "aria-label": open ? "Collapse" : "Expand", onclick: e => { e.stopPropagation(); const k = "c:" + r.key; state.flowOpen.has(k) ? state.flowOpen.delete(k) : state.flowOpen.add(k); renderAll(); } }, open ? "▾" : "▸")
      : h("span", { class: "tg" }, "");
    list.append(h("div", { class: "frow" + (i === state.flowStep ? " cur" : ""), role: "treeitem", style: `padding-left:${8 + r.depth * 22}px`, tabindex: 0,
      onclick: () => { state.flowStep = i; openFile(r.file.path, r.sym.line); renderAll(); } },
      toggle, h("span", { class: "n" }, String(i + 1)),
      h("span", { class: "nm" }, symLabel(r.sym)),
      r.conf === "inferred" ? h("span", { class: "badge warn", title: "Linked by name only; may be wrong" }, "guess") : null,
      r.repeat ? h("span", { class: "badge" }, "↻ shown above") : null,
      r.cyc ? h("span", { class: "badge" }, "↺ calls itself") : null,
      r.cut ? h("span", { class: "badge" }, `+${r.nKids} deeper`) : null,
      (LEVEL !== "tech" && r.sym.explain ? firstSentence(T(r.sym.explain), 110) : r.sym.doc) ? h("span", { class: "d" }, (LEVEL !== "tech" && r.sym.explain ? firstSentence(T(r.sym.explain), 110) : r.sym.doc).replace(/`/g, "")) : null,
      h("span", { class: "loc" }, `${base(r.file.path)}:${r.sym.line}`)));
  });
  view.append(list);
  const roots0 = SYMS.get(state.flowRoot);
  if (roots0 && !rows[0].nKids) view.append(h("p", { class: "hint" }, "This starting point doesn't call anything else inside the repository."));
}
function stepCard(r, vis) {
  const src = r.file.source ? r.file.source.split("\n").slice(r.sym.line - 1, Math.min(r.sym.end_line, r.sym.line + 29)) : [];
  const parent = vis.slice(0, vis.indexOf(r)).reverse().find(x => x.depth === r.depth - 1);
  return h("div", { class: "card step" },
    h("div", { class: "t" }, "Now running: ", h("b", {}, symLabel(r.sym)), h("span", { class: "loc" }, ` ${r.file.path}:${r.sym.line}`)),
    parent ? h("div", { class: "hint" }, `Called by ${symLabel(parent.sym)} at line ${r.via}.`) : h("div", { class: "hint" }, "This is where it starts."),
    LEVEL !== "tech" && r.sym.explain ? h("div", { class: "simple" }, rich(T(r.sym.explain))) : (r.sym.doc ? h("div", {}, r.sym.doc) : null),
    src.length ? h("pre", { class: "snip" }, src.join("\n") + (r.sym.end_line - r.sym.line >= 30 ? "\n…" : "")) : null);
}

// ---------- detail panel ----------
function renderDetail() {
  const el = document.getElementById("detail");
  const f = state.selected && BY.get(state.selected);
  if (!f) { el.hidden = true; return; }
  el.hidden = false;
  const badges = [h("span", { class: "badge" }, f.lang)];
  if (f.entry.length) badges.push(h("span", { class: "badge ok", title: f.entry.join("; ") }, "entry point"));
  if (f.isTest) badges.push(h("span", { class: "badge" }, "test"));
  if (f.unused) badges.push(h("span", { class: "badge warn", title: "No file imports this and it is not an entry point" }, "possibly unused"));
  const chg = CH && CH.files[f.path];
  if (chg) badges.push(h("span", { class: "badge ok", title: chg.why.join("; ") }, "changed in this review"));
  if (f.parseError) badges.push(h("span", { class: "badge warn", title: f.parseError }, "parse error"));
  const m = (n, l) => h("div", {}, h("b", {}, String(n)), h("span", {}, l));
  const links = (arr, empty) => arr.length ? arr.map(p => h("a", { class: "link", tabindex: 0, onclick: () => openFile(p), onkeydown: e => { if (e.key === "Enter") openFile(p); } }, p)) : h("span", { class: "empty", style: "padding:0" }, empty);
  const syms = f.symbols.length ? f.symbols.map(sym => h("div", { class: "symwrap" },
    h("div", { class: "sym", title: sym.doc, onclick: () => { state.symOpen = state.symOpen === f.path + "::" + sym.qual ? "" : f.path + "::" + sym.qual; openFile(f.path, sym.line); } },
      h("span", { class: "k" }, sym.kind), h("span", {}, h("span", { class: "nm" }, sym.kind === "module" ? "(top-level code)" : sym.qual), sym.doc ? h("div", { class: "d" }, sym.doc) : null),
      h("span", { class: "ln" }, `L${sym.line}` + (sym.complexity > 1 ? ` · cx ${sym.complexity}` : ""))),
    LEVEL !== "tech" && state.symOpen === f.path + "::" + sym.qual && sym.explain ? h("div", { class: "simple box" }, rich(T(sym.explain))) : null)) : h("span", { class: "empty", style: "padding:0" }, f.analyzed ? "No functions or classes found." : "Symbols are not extracted for this language yet.");
  const code = h("div", { class: "code", id: "code" });
  const lines = f.source ? f.source.split("\n") : [];
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  if (f.source) {
    code.append(h("pre", { class: "g" }, lines.map((_, i) => i + 1).join("\n")), h("pre", { class: "s" }, f.source));
    if (state.line) { code.append(h("div", { class: "hl", style: `top:${6 + (state.line - 1) * 18}px` })); }
  }
  el.replaceChildren(
    h("div", { class: "dhead" }, h("button", { class: "x", "aria-label": "Close", onclick: () => { state.selected = null; writeHash(); renderDetail(); markSelected(); } }, "✕"),
      h("div", { class: "t" }, f.path), h("div", {}, badges), f.doc ? h("div", { class: "d", style: "color:var(--muted);margin-top:4px" }, f.doc) : null),
    h("div", { class: "dbody" },
      LEVEL !== "tech" && f.explain ? h("div", { class: "dsec" }, h("h3", {}, LEVEL === "kid" ? "In simple words" : "Summary"), h("div", { class: "simple" }, rich(T(f.explain)))) : null,
      h("div", { class: "dsec" }, h("div", { class: "metrics" }, m(fmt(f.loc), "lines"), m(f.analyzed ? f.complexity : "–", "complexity"), m(D.repo.hasHistory ? f.churn : "–", "commits"), m(f.importedBy.length, "used by"))),
      f.entry.length ? h("div", { class: "dsec" }, h("h3", {}, "Why it's an entry point"), f.entry.join("; ")) : null,
      chg ? h("div", { class: "dsec" }, h("h3", {}, `What changed (+${chg.added} / −${chg.deleted})`), diffEl(chg.patch)) : null,
      h("div", { class: "dsec" }, h("h3", {}, `Symbols (${f.symbols.length})`), syms),
      h("div", { class: "dsec" }, h("h3", {}, `Imports (${f.imports.length})`), links(f.imports, "Imports nothing from this repo.")),
      h("div", { class: "dsec" }, h("h3", {}, `Imported by (${f.importedBy.length})`), links(f.importedBy, "Nothing imports this file.")),
      h("div", { class: "dsec" }, h("h3", {}, "Source" + (f.truncated ? " (too large to embed)" : "")), f.source ? code : h("span", { class: "empty", style: "padding:0" }, "Source not embedded."))));
  if (state.line && f.source) {
    const c = document.getElementById("code");
    c.scrollTop = Math.max(0, (state.line - 6) * 18);
    c.scrollIntoView({ block: "nearest" });
  }
  document.querySelector(".dbody").scrollTop = state.line ? document.querySelector(".dbody").scrollTop : 0;
}

// ---------- search ----------
function initSearch() {
  const input = document.getElementById("search"), box = document.getElementById("results");
  const index = [];
  for (const f of FILES) {
    index.push({ label: base(f.path), path: f.path, kind: "file", f });
    for (const sym of f.symbols) index.push({ label: sym.name, path: f.path, kind: sym.kind, line: sym.line, f });
  }
  let hits = [], cur = 0;
  const close = () => { box.hidden = true; hits = []; };
  const choose = i => { const x = hits[i]; if (!x) return; close(); input.blur(); openFile(x.path, x.line); };
  const draw = () => {
    box.replaceChildren(...hits.map((x, i) => h("div", { class: "item" + (i === cur ? " on" : ""), onmousedown: e => { e.preventDefault(); choose(i); } },
      h("span", { class: "badge" }, x.kind), h("span", {}, x.label), h("span", { class: "path" }, x.path))));
    box.hidden = !hits.length;
  };
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    if (!q) return close();
    const scored = [];
    for (const x of index) {
      const l = x.label.toLowerCase(), p = x.path.toLowerCase();
      let sc = l === q ? 0 : l.startsWith(q) ? 1 : l.includes(q) ? 2 : p.includes(q) && x.kind === "file" ? 3 : -1;
      if (sc >= 0) scored.push([sc + (x.kind === "file" ? 0 : 0.5), x]);
    }
    scored.sort((a, b) => a[0] - b[0] || a[1].label.length - b[1].label.length);
    hits = scored.slice(0, 40).map(x => x[1]); cur = 0; draw();
  });
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { cur = Math.min(hits.length - 1, cur + 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { cur = Math.max(0, cur - 1); draw(); e.preventDefault(); }
    else if (e.key === "Enter") choose(cur);
    else if (e.key === "Escape") { close(); input.blur(); }
  });
  input.addEventListener("blur", () => setTimeout(close, 100));
  document.addEventListener("keydown", e => {
    if (e.key === "/" && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); input.focus(); input.select(); }
  });
}

// ---------- boot ----------
readHash();
renderShell();
initSearch();
renderAll();
window.addEventListener("hashchange", () => { readHash(); renderAll(); });
let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (state.tab !== "overview") renderAll(); }, 200); });
})();
