// ---------- the code reader: colours, links from calls to their definitions, margin markers, "who uses this" ----------
const RD_MAX_COLOURED_LINES = 4000;
const RD_PY_KW = new Set("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case self cls".split(" "));
const RD_JS_KW = new Set("abstract any as async await break case catch class const continue debugger default delete do else enum export extends false finally for from function get if implements import in instanceof interface let new null of private protected public readonly return set static super switch this throw true try type typeof undefined var void while with yield".split(" "));
const RD_PY = /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|#[^\n]*|\b\d[\d_]*(?:\.\d+)?\b|[A-Za-z_]\w*|\n|[^\S\n]+|[\s\S])/g;
const RD_JS = /(\/\*[\s\S]*?\*\/|\/\/[^\n]*|`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|\b\d[\d_]*(?:\.\d+)?\b|[A-Za-z_$][\w$]*|\n|[^\S\n]+|[\s\S])/g;
let RD_CALLERS = null;

// Who calls whom, from the certain edges only: target id -> [{from, line}] (one entry per call site).
function rdCallers() {
  if (RD_CALLERS) return RD_CALLERS;
  RD_CALLERS = new Map();
  for (const [sid, { sym }] of SYMS) {
    for (const c of sym.callees || []) {
      if (c.conf !== "certain" || !SYMS.has(c.id)) continue;
      if (!RD_CALLERS.has(c.id)) RD_CALLERS.set(c.id, []);
      RD_CALLERS.get(c.id).push({ from: sid, line: c.line });
    }
  }
  return RD_CALLERS;
}
const rdNamesOf = t => {
  const names = new Set([t.sym.name, t.sym.qual.split(".").pop()]);
  if (["__init__", "constructor"].includes(t.sym.name) && t.sym.qual.includes(".")) names.add(t.sym.qual.split(".").slice(-2)[0]);
  return names;
};

// Calls on each line of this file that lead to a definition we know: line -> [{names, id}]
function rdCallLinks(f) {
  const byLine = new Map();
  for (const sym of f.symbols) {
    for (const c of sym.callees || []) {
      const t = SYMS.get(c.id);
      if (c.conf !== "certain" || !t) continue;
      if (!byLine.has(c.line)) byLine.set(c.line, []);
      byLine.get(c.line).push({ names: rdNamesOf(t), id: c.id });
    }
  }
  return byLine;
}

function rdChangedLines(patch) {
  const out = new Set();
  let n = 0;
  for (const l of String(patch || "").split("\n")) {
    const m = /^@@ -\d+(?:,\d+)? \+(\d+)/.exec(l);
    if (m) { n = +m[1]; continue; }
    if (!n) continue;
    if (l.startsWith("+")) { out.add(n); n++; } else if (!l.startsWith("-") && !l.startsWith("\\")) n++;
  }
  return out;
}

// The source as an array of nodes: plain text, coloured spans, links for calls.
function rdTokens(f, lineCount) {
  const py = f.lang === "Python", js = f.lang === "JavaScript" || f.lang === "TypeScript";
  if ((!py && !js) || lineCount > RD_MAX_COLOURED_LINES) return f.source;
  const kw = py ? RD_PY_KW : RD_JS_KW, links = rdCallLinks(f);
  const out = [];
  let buf = "", line = 1, prevWord = "";
  const flush = () => { if (buf) { out.push(buf); buf = ""; } };
  for (const t of f.source.matchAll(py ? RD_PY : RD_JS)) {
    const s = t[0];
    let cls = "";
    if (/^(?:"""|'''|"|'|`)/.test(s)) cls = "tk-str";
    else if (s.startsWith("#") && py || s.startsWith("//") || s.startsWith("/*")) cls = "tk-cm";
    else if (/^\d/.test(s)) cls = "tk-num";
    else if (/^[A-Za-z_$]/.test(s) && kw.has(s)) cls = "tk-kw";
    if (cls) { flush(); out.push(h("span", { class: cls }, s)); }
    else if (/^[A-Za-z_$]/.test(s) && links.has(line) && !["def", "class", "function"].includes(prevWord)
      && links.get(line).some(l => l.names.has(s))) {
      const hit = links.get(line).find(l => l.names.has(s)), t2 = SYMS.get(hit.id);
      flush();
      out.push(h("a", { class: "cl-call", href: "#", title: `Go to ${t2.sym.qual} in ${t2.file.path}`,
        onclick: e => { e.preventDefault(); openFile(t2.file.path, t2.sym.line, t2.sym.qual); } }, s));
    } else buf += s;
    if (/^[A-Za-z_$]/.test(s)) prevWord = s; else if (s.trim()) prevWord = "";
    line += s.split("\n").length - 1;
  }
  flush();
  return out;
}

function rdUseLabel(n) { return n === 0 ? "no uses" : n === 1 ? "1 use" : `${n} uses`; }

// The use-count buttons, placed after the end of each definition line.
function rdDecorate(f, lines) {
  const callers = rdCallers(), tail = new Map();
  if (f.isTest) return tail;
  for (const sym of f.symbols) {
    if (sym.kind === "module") continue;
    const sid = f.path + "::" + sym.qual, n = (callers.get(sid) || []).length;
    if (!n && sym.name.startsWith("__")) continue;
    const btn = h("button", { class: "rd-uses-btn" + (n ? "" : " none"), "aria-pressed": String(state.uses === sid),
      title: n ? "Show where this is used" : "Nothing in the repository calls this (it may still be used from outside)",
      onclick: () => { state.uses = state.uses === sid ? "" : sid; renderDetail(); } }, rdUseLabel(n));
    tail.set(sym.line, btn);
  }
  return tail;
}

// The code with colours, links, and markers beside the line numbers.
function readerCode(f) {
  const lines = f.source.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  const code = h("div", { class: "code", id: "code" });
  const finds = new Map();
  for (const x of (D.hygiene && D.hygiene.findings) || []) if (x.path === f.path && !finds.has(x.line)) finds.set(x.line, x);
  const te = D.tests && D.tests.files && D.tests.files[f.path];
  const untested = new Set(te && !f.isTest ? te.untested : []);
  const noTestLines = new Map(f.symbols.filter(s => untested.has(s.qual) && s.kind !== "class" && s.kind !== "module").map(s => [s.line, s]));
  const chg = CH && CH.files[f.path];
  const changed = chg ? rdChangedLines(chg.patch) : new Set();
  const gutter = lines.map((_, i) => {
    const n = i + 1, marks = [];
    if (finds.has(n)) marks.push(h("span", { class: "gm gm-find", "data-line": n, title: `${finds.get(n).title} (${finds.get(n).sev})` }, "▲"));
    if (noTestLines.has(n)) marks.push(h("span", { class: "gm gm-notest", "data-line": n, title: "No test reaches this function" }, "○"));
    if (changed.has(n)) marks.push(h("span", { class: "gm gm-chg", "data-line": n, title: "Changed in this review" }, "●"));
    return h("span", { class: "gl" }, marks.length ? marks : h("span", { class: "gm" }, " "), String(n).padStart(String(lines.length).length, " "), "\n");
  });
  const tail = rdDecorate(f, lines);
  const toks = rdTokens(f, lines.length);
  let body = toks;
  if (tail.size) {
    // Put each button at the end of its line: split the coloured nodes at newlines.
    const parts = [];
    let line = 1;
    const push = node => {
      if (typeof node === "string") {
        const segs = node.split("\n");
        segs.forEach((seg, i) => {
          if (seg) parts.push(seg);
          if (i < segs.length - 1) { if (tail.has(line)) parts.push(" ", tail.get(line)); parts.push("\n"); line++; }
        });
      } else {
        parts.push(node);
        line += (node.textContent.match(/\n/g) || []).length;
      }
    };
    (Array.isArray(toks) ? toks : [toks]).forEach(push);
    if (tail.has(line)) parts.push(" ", tail.get(line));
    body = parts;
  }
  code.append(h("pre", { class: "g" }, gutter), h("pre", { class: "s" }, body));
  if (state.line) code.append(h("div", { class: "hl", style: `top:${6 + (state.line - 1) * 18}px` }));
  return code;
}

// The list that opens under the code when a use-count is clicked.
function readerUses(f) {
  if (!state.uses || !state.uses.startsWith(f.path + "::")) return null;
  const t = SYMS.get(state.uses);
  if (!t) return null;
  const calls = rdCallers().get(state.uses) || [];
  const rows = calls.map(c => ({ ...c, at: SYMS.get(c.from) })).filter(c => c.at)
    .sort((a, b) => (a.at.file.isTest - b.at.file.isTest) || a.at.file.path.localeCompare(b.at.file.path) || a.line - b.line);
  const src = r => (r.at.file.source || "").split("\n")[r.line - 1];
  return h("div", { class: "dsec rd-uses" },
    h("div", { class: "rd-uses-head" }, h("h3", {}, `Used like this: ${symLabel(t.sym)}`),
      h("button", { class: "rd-uses-close", "aria-label": "Close the list of uses", onclick: () => { state.uses = ""; renderDetail(); } }, "✕")),
    rows.length ? h("ul", { class: "rd-use-list" }, rows.slice(0, 12).map(r => h("li", {},
      h("a", { href: "#", onclick: e => { e.preventDefault(); openFile(r.at.file.path, r.line, r.at.sym.qual); } }, `${r.at.file.path}:${r.line}`),
      r.at.file.isTest ? h("span", { class: "badge ok" }, "test") : null,
      h("code", {}, (src(r) || "").trim().slice(0, 120)))))
      : h("p", { class: "hint" }, "Nothing in the repository calls this. It may still be used from outside, such as by a framework or another project."),
    rows.length > 12 ? h("p", { class: "hint" }, `…and ${rows.length - 12} more.`) : null);
}
