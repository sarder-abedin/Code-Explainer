// The Health tab: which tests reach the code, and a safety check.
// This file is spliced into app.js at build time (see report.py), so it shares app.js's scope: h(), D, BY, LEVEL, openFile...

const HEALTH = { sev: new Set(), tests: false };
const SEV_ORDER = ["high", "medium", "low", "info"];
const SEV_LABEL = { high: "serious", medium: "worth fixing", low: "minor", info: "for your information" };
const TEST_STATE = {
  tested: ["fully tested", "ok"], partial: ["partly tested", ""], untested: ["no test seen", "warn"],
  imported: ["only imported by tests", "warn"], unknown: ["cannot tell for this language", ""],
};
const HEALTH_INTRO = {
  kid: "Tests are little helpers that try out the program to see that it works. Here you can see which parts have a helper looking after them, and which parts do not. "
    + "We also look for places where a trick or a mistake could get in.",
  plain: "Tests are small programs that check the code works. This page shows which functions no test was seen reaching, "
    + "and common safety problems such as passwords left in the code.",
  tech: "Static approximation: functions reachable from test code through the resolved call graph, plus pattern-based security and hygiene checks. "
    + "Nothing is executed, so dynamic dispatch, fixtures and mocks are invisible to it.",
};

function testInfo(path) { return (D.tests && D.tests.files[path]) || null; }

function findingEl(f) {
  return h("li", { class: "finding" + (f.test ? " is-test" : ""), "data-sev": f.sev, "data-rule": f.id },
    h("div", { class: "fhead" },
      h("span", { class: `badge sev-${f.sev}` }, SEV_LABEL[f.sev]), h("b", {}, f.title), " ",
      h("a", { class: "link", tabindex: 0, onclick: () => BY.has(f.path) && openFile(f.path, f.line),
        onkeydown: e => { if (e.key === "Enter" && BY.has(f.path)) openFile(f.path, f.line); } }, `${f.path}:${f.line}`)),
    f.code ? h("pre", { class: "fcode" }, f.code) : null,
    h("div", { class: "fwhy" }, f.why),
    h("div", { class: "ffix" }, h("b", {}, "How to fix: "), f.fix));
}

function renderHealth(view) {
  const T0 = D.tests, S0 = D.hygiene;
  if (!T0 || !S0) { view.append(h("div", { class: "empty" }, "This report was made by an older version and has no health check.")); return; }
  const ts = T0.summary, hs = S0.summary;
  const only = state.hfile;           // set by the file panel's Health button: show just this file
  const card = (n, l) => h("div", { class: "card" }, h("div", { class: "n" }, String(n)), h("div", { class: "l" }, l));
  view.append(
    h("div", { class: "health" },
      h("p", { class: "intro hint", style: "margin-top:0" }, HEALTH_INTRO[LEVEL] || HEALTH_INTRO.plain),
      ts.testFiles === 0 ? h("div", { class: "card warn-card" }, h("b", {}, "This project has no tests. "),
        "Nothing can check that a change did not break something, so every function below counts as untested.") : null,
      h("div", { class: "grid" },
        card(`${ts.pct}%`, `of ${fmt(ts.symbols)} functions a test was seen reaching`),
        card(fmt(ts.untested), "functions no test was seen reaching"),
        card(hs.high + hs.medium, "safety findings worth a look"),
        card(S0.cycles.length, "groups of files that import each other in a circle"),
        card(fmt(S0.todos.total), "TODO / FIXME notes left in the code")),
      only ? h("div", { class: "h-filter card" }, h("span", {}, "Showing only ", h("b", {}, only), " (",
        h("a", { class: "link", style: "display:inline", onclick: () => openFile(only) }, "open the file"), ")"),
        h("button", { id: "h-clear", onclick: () => { state.hfile = ""; writeHash(); renderAll(); } }, "Show everything")) : null,
      healthHotspots(T0), healthFiles(T0), healthSafety(S0), healthCycles(S0), healthTodos(S0),
      h("p", { class: "hint" }, "These checks read the code; they never run it. A function with “no test seen” may still be tested in a way a "
        + "static check cannot follow, and a finding is a place worth a look, not proof of a bug.")));
}

function healthHotspots(T0) {
  const hot = T0.hotspots.filter(x => !state.hfile || x.path === state.hfile);
  return h("div", {}, h("h2", {}, "Risky code with no test", h("small", {}, "complex, widely used, and nothing seen testing it")),
    hot.length ? h("ul", { class: "list", id: "h-hotspots" }, hot.map(x =>
      h("li", { tabindex: 0, onclick: () => BY.has(x.path) && openFile(x.path, x.line), onkeydown: e => { if (e.key === "Enter" && BY.has(x.path)) openFile(x.path, x.line); } },
        h("span", {}, h("span", { class: "p" }, x.qual), h("span", { class: "why" }, `${x.path}:${x.line} · complexity ${x.complexity}`
          + (x.callers ? ` · used by ${x.callers} other ${x.callers === 1 ? "function" : "functions"}` : "")))))) :
      h("ul", { class: "list", id: "h-hotspots" }, h("li", { class: "empty" }, "No complex function is missing a test.")));
}

function healthFiles(T0) {
  const rank = { untested: 0, imported: 1, partial: 2 };
  const rows = Object.entries(T0.files).filter(([path, e]) => e.state in rank && (!state.hfile || path === state.hfile))
    .sort((a, b) => rank[a[1].state] - rank[b[1].state] || (BY.get(b[0]) || {}).loc - (BY.get(a[0]) || {}).loc).slice(0, 20);
  return h("div", {}, h("h2", {}, "Files that are not fully tested", h("small", {}, "least tested first")),
    rows.length ? h("ul", { class: "list", id: "h-files" }, rows.map(([path, e]) => h("li", { tabindex: 0, "data-path": path, onclick: () => BY.has(path) && openFile(path) },
      h("span", {}, h("span", { class: `badge ${TEST_STATE[e.state][1]}` }, TEST_STATE[e.state][0]), h("span", { class: "p" }, path),
        h("span", { class: "why" }, `${e.untested.length + (e.untestedMore || 0)} of ${e.direct.length + e.indirect.length + e.maybe.length + e.untested.length + (e.untestedMore || 0)} functions with no test seen`))))) :
      h("div", { class: "list empty" }, "Every function in every file was seen reached by a test."));
}

function healthSafety(S0) {
  const shown = S0.findings.filter(f => (HEALTH.tests || !f.test) && (!state.hfile || f.path === state.hfile));
  const counts = Object.fromEntries(SEV_ORDER.map(s => [s, shown.filter(f => f.sev === s).length]));
  const list = shown.filter(f => !HEALTH.sev.size || HEALTH.sev.has(f.sev));
  const head = h("h2", {}, "Safety check", h("small", {}, `${S0.summary.total} thing${S0.summary.total === 1 ? "" : "s"} found`));
  const box = h("div", { class: "safety" }, head);
  if (!S0.findings.length) { box.append(h("div", { class: "card ok-card" }, h("b", {}, "Nothing suspicious found. "), "None of the known risky patterns showed up.")); return box; }
  box.append(h("div", { class: "sevbar" },
    SEV_ORDER.filter(s => counts[s]).map(s => h("button", { class: `sevchip sev-${s}` + (HEALTH.sev.has(s) ? " on" : ""), "data-sev": s,
      onclick: () => { HEALTH.sev.has(s) ? HEALTH.sev.delete(s) : HEALTH.sev.add(s); renderAll(); } }, `${SEV_LABEL[s]} (${counts[s]})`)),
    h("label", { class: "hint", style: "margin-left:12px" }, h("input", { type: "checkbox", id: "h-tests", checked: HEALTH.tests,
      onchange: e => { HEALTH.tests = e.target.checked; renderAll(); } }), " include test files")));
  box.append(h("ul", { class: "list findings" }, list.map(findingEl)));
  if (S0.summary.truncated) box.append(h("p", { class: "hint" }, `Showing the first ${S0.findings.length} of ${S0.summary.total}, worst first.`));
  return box;
}

function healthCycles(S0) {
  const cycles = S0.cycles.filter(c => !state.hfile || c.files.includes(state.hfile));
  if (!cycles.length) return null;
  return h("div", {}, h("h2", {}, "Import circles", h("small", {}, "files that need each other")),
    h("ul", { class: "list" }, cycles.map(c => h("li", { class: "cycle", tabindex: 0, onclick: () => BY.has(c.files[0]) && openFile(c.files[0]) },
      h("span", {}, h("span", { class: "p" }, c.files.map(base).join(" ↔ ") + (c.size > c.files.length ? " …" : "")),
        h("span", { class: "why" }, c.benign ? "involves a package __init__ file, which is usually harmless" : "move the shared part into a third file"))))));
}

function healthTodos(S0) {
  const todos = S0.todos.files.filter(f => !state.hfile || f.path === state.hfile);
  if (!S0.todos.total || !todos.length) return null;
  return h("div", {}, h("h2", {}, "Unfinished notes", h("small", {}, "TODO, FIXME, HACK, XXX")),
    h("ul", { class: "list" }, todos.map(f => h("li", { tabindex: 0, onclick: () => BY.has(f.path) && openFile(f.path) },
      h("span", {}, h("span", { class: "p" }, f.path), h("span", { class: "why" }, `${f.count} note${f.count === 1 ? "" : "s"}`))))));
}

// Sections for the file panel: which tests reach this file, and what the safety check found in it.
function healthDetail(f) {
  const out = [];
  const t = testInfo(f.path);
  if (t) {
    const [label, cls] = TEST_STATE[t.state];
    const list = (title, names, more) => names.length ? h("div", { class: "trow" }, h("b", {}, title + " "),
      names.join(", ") + (more ? ` and ${more} more` : "")) : null;
    out.push(h("div", { class: "dsec testsec" }, h("h3", {}, "Tests"), h("span", { class: `badge ${cls}` }, label),
      t.tests.length ? h("div", { class: "trow" }, "Reached by: ", t.tests.map((p, i) => [i ? ", " : "", h("a", { class: "link", tabindex: 0,
        onclick: () => openFile(p), onkeydown: e => { if (e.key === "Enter") openFile(p); } }, p)]), t.testsMore ? ` and ${t.testsMore} more` : "") : null,
      list("No test seen reaching:", t.untested, t.untestedMore),
      list("Only reached through a guess:", t.maybe, t.maybeMore)));
  }
  const found = D.hygiene ? D.hygiene.findings.filter(x => x.path === f.path) : [];
  if (found.length) out.push(h("div", { class: "dsec safesec" }, h("h3", {}, `Worth a look (${found.length})`), h("ul", { class: "list findings" }, found.map(findingEl))));
  return out;
}

function healthSymbolBadge(f, sym) {
  const t = testInfo(f.path);
  return t && t.untested.includes(sym.qual) ? h("span", { class: "badge warn nt", title: "No test was seen reaching this function" }, "no test") : null;
}
