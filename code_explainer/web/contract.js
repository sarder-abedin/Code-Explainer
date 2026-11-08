// ---------- function contracts: what goes in, what comes out, what can go wrong, what it touches ----------
// The facts come from the analysis (Symbol.contract); only the wording is decided here, per reading level.
const CT_VALUE = { number: "a number", text: "text", bool: "yes or no", none: "nothing", list: "a list", dict: "a dictionary",
  set: "a set", tuple: "a group of values", generator: "a stream of values", function: "a function", computed: "a computed value", other: "a value" };
const CT_KIND = { database: "a database", "web-server": "web requests it serves", "web-client": "web services", files: "files on disk",
  processes: "other programs", queue: "a message queue", cloud: "cloud services", ai: "an AI service", "command-line": "the command line",
  email: "email", network: "network connections" };

function ctValue(v) {
  if (v.startsWith("call:")) return LEVEL === "tech" ? `${v.slice(5)}(…)` : `what ${v.slice(5)}() gives back`;
  if (v.startsWith("name:") || v.startsWith("attr:")) return v.slice(5);
  return CT_VALUE[v] || "a value";
}
function ctList(a) { return a.length < 2 ? a.join("") : a.slice(0, -1).join(", ") + " and " + a[a.length - 1]; }
function ctParam(p, tech) {
  const star = p.k === "var" ? "*" : p.k === "varkw" ? "**" : "";
  if (!tech) return star ? `${p.n} (any number of extras)` : p.d !== undefined ? `${p.n} (optional)` : p.n;
  return star + p.n + (p.t ? `: ${p.t}` : "") + (p.d !== undefined ? ` = ${p.d}` : "");
}
function ctReturns(c) {
  const r = c.returns, vals = r.v.map(ctValue);
  if (r.none) vals.push(CT_VALUE.none);
  const uniq = [...new Set(vals)];
  const more = r.moreValues ? ` (and ${r.moreValues} other ${r.moreValues === 1 ? "case" : "cases"})` : "";
  if (r.gen) return (r.t || "a stream of values");
  return (uniq.length ? ctList(uniq) : CT_VALUE.none) + more;
}

// {takes, gives, fails, changes, shows, touches} in words, or null when there is nothing worth saying.
function contractFacts(sym) {
  const c = sym.contract;
  if (!c || sym.kind === "class" || sym.kind === "module") return null;
  const tech = LEVEL === "tech";
  const params = c.params.filter(p => !(c.method && ["self", "cls"].includes(p.n) && c.params[0] === p));
  const takes = params.length ? params.map(p => ctParam(p, tech)).join(", ") + (c.moreParams ? ` and ${c.moreParams} more` : "") : "";
  const fails = c.raises.map(e => e === "re-raise" ? "an error it passes on" : e === "a caught error" ? "an error it passes on" : e);
  const changes = c.changes.map(x => x.replace("argument ", "the given "));
  const shows = c.outputs.map(o => o === "prints" ? "prints text" : o === "logs" ? "writes a log message" : "asks the user to type");
  const touches = c.touches.map(t => CT_KIND[t.kind] || t.kind);
  const indirect = c.indirect.map(t => (CT_KIND[t.kind] || t.kind) + ` (through ${t.via})`);
  return { takes, gives: ctReturns(c), fails, changes, shows, touches, indirect, async: c.async };
}

// One line for the list of functions: "Takes a, b → gives back a number".
function contractLine(sym) {
  const f = contractFacts(sym);
  if (!f) return "";
  const tech = LEVEL === "tech";
  const out = [];
  out.push(tech ? `(${f.takes}) → ${f.gives}` : `${f.takes ? "Needs " + f.takes : "Needs nothing"}, gives back ${f.gives}`);
  if (f.fails.length) out.push(tech ? `raises ${f.fails.join(", ")}` : `can fail with ${ctList(f.fails)}`);
  if (f.touches.length) out.push(`${tech ? "uses" : "uses"} ${ctList(f.touches)}`);
  return out.join(" · ");
}

// A card with one labelled row per fact. Rows with nothing to say are left out.
function contractCard(sym) {
  const f = contractFacts(sym);
  if (!f) return null;
  const kid = LEVEL === "kid";
  const row = (label, text, cls) => h("div", { class: "ct-row " + (cls || "") }, h("b", {}, label), h("span", {}, text));
  const rows = [
    row(kid ? "You give it" : "Takes", f.takes || "nothing"),
    row(kid ? "You get back" : "Gives back", f.gives + (f.async ? " (later, it is asynchronous)" : ""))];
  if (f.fails.length) rows.push(row(kid ? "It can break with" : "Can fail with", ctList(f.fails), "warn"));
  if (f.changes.length) rows.push(row(kid ? "It changes" : "Changes", ctList(f.changes), "warn"));
  if (f.shows.length) rows.push(row(kid ? "It shows" : "Shows", ctList(f.shows)));
  if (f.touches.length) rows.push(row(kid ? "It talks to" : "Touches", ctList(f.touches)));
  if (f.indirect.length) rows.push(row(kid ? "Through others, it talks to" : "Reaches indirectly", ctList(f.indirect)));
  return h("div", { class: "ct-card", role: "group", "aria-label": "What this function takes, gives back and touches" }, rows);
}

// Collapsible block for lists and panels: the one-line summary is the toggle.
function contractBlock(sym) {
  const line = contractLine(sym);
  if (!line) return null;
  const open = state.ctOpen === sym.qual;
  const d = h("details", { class: "ct", open }, h("summary", { title: "Show what this function takes, gives back and touches" }, line), contractCard(sym));
  d.addEventListener("toggle", () => { if (d.open) state.ctOpen = sym.qual; else if (state.ctOpen === sym.qual) state.ctOpen = ""; });
  return d;
}
