// ---------- AI tab: model picker, tuning panel and chat ----------
// This file is spliced into app.js at build time (see report.py), so it shares app.js's scope: h(), state, SERVE, BY, LEVEL, toast...
// Everything the model or a server says is shown with textContent / text nodes. Nothing here uses innerHTML.

const AI = {
  ready: false, loading: false, settings: null, schema: null, providers: [], status: { state: "off" },
  selected: { provider: "", endpoint: "", model: "" }, showAll: false, saved: false, savedTimer: null,
  pending: null, saveTimer: null, poll: null, useContext: true, ground: true, addError: "", startError: "", advancedOpen: false,
  form: { provider: "ollama", url: "", name: "", key: "" },
  chat: { messages: [], streaming: false, ctl: null, cur: null, draft: "", sentOpen: new Set(), nextId: 1 },
};
const AI_NAMES = { ollama: "Ollama", lmstudio: "LM Studio", openai: "this server" };
const AI_PARAM_ORDER = ["temperature", "top_p", "top_k", "min_p", "max_tokens", "context_size", "repeat_penalty", "seed"];

async function aiApi(path, body) {
  const r = await fetch(path, body === undefined ? undefined
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw Object.assign(new Error((data && data.error) || `HTTP ${r.status}`), { data, status: r.status });
  return data;
}
const aiBytes = n => (n >= 1e9 ? (n / 1e9).toFixed(1) + " GB" : n >= 1e6 ? Math.round(n / 1e6) + " MB" : n ? Math.round(n / 1e3) + " KB" : "");
const aiNum = v => (v === null || v === undefined || v === "" ? "" : String(+Number(v).toFixed(4)));
const aiClock = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const aiEl = id => document.getElementById(id);

// ---------- entry point ----------
function renderAI(view) {
  if (!SERVE) {
    view.append(h("div", { class: "card ai-static" }, h("h2", { style: "margin-top:0" }, "AI Explainer"),
      h("p", {}, "The AI Explainer talks to a model running on your own computer (Ollama, LM Studio or any OpenAI-compatible server). "
        + "A saved report is just a file, so it cannot start or talk to one."),
      h("p", {}, "To use it, run ", h("code", {}, "code-explainer serve <your repo>"), " and open the AI tab there."),
      D.aiExplain ? h("p", { class: "aix-static-note" }, `This report already contains ${Object.keys(D.aiExplain.items).length} explanations written ahead of time by `,
        h("strong", {}, D.aiExplain.model), ". Look for the “AI explanation” sections on files and changes.")
        : h("p", { class: "hint" }, "Tip: ", h("code", {}, "code-explainer analyze <repo> --ai"), " writes AI explanations of the most important files into the report ahead of time.")));
    return;
  }
  view.append(h("div", { id: "ai-root", class: "ai" },
    h("div", { class: "ai-left" }, h("div", { id: "ai-status-box" }), h("div", { id: "ai-providers" }), h("div", { id: "ai-add" })),
    h("div", { class: "ai-right" }, h("div", { id: "ai-chat" }), h("div", { id: "ai-settings" }))));
  aiPaintAll();
  aiLoad();
  aiStartPolling();
}

async function aiLoad() {
  if (AI.loading) return;
  AI.loading = true;
  try {
    const [st, prov, status] = await Promise.all([aiApi("/api/ai/settings"), aiApi("/api/ai/providers"), aiApi("/api/ai/status")]);
    AI.settings = st.settings; AI.schema = st.schema; AI.providers = prov.providers; AI.status = status;
    const sel = status.state !== "off" && status.model ? { provider: status.provider, endpoint: status.endpoint, model: status.model } : prov.selected;
    AI.selected = { provider: sel.provider || "", endpoint: sel.endpoint || "", model: sel.model || "" };
    AI.ready = true;
  } catch (e) {
    AI.loadError = e.message;
  }
  AI.loading = false;
  aiPaintAll();
}

function aiPaintAll() {
  if (!aiEl("ai-root")) return;
  aiPaintStatus(); aiPaintProviders(); aiPaintAdd(); aiPaintChat(); aiPaintSettings();
}

function aiStartPolling() {
  clearInterval(AI.poll);
  AI.poll = setInterval(async () => {
    if (state.tab !== "ai" || !aiEl("ai-root")) { clearInterval(AI.poll); AI.poll = null; return; }
    try {
      AI.status = await aiApi("/api/ai/status");
      aiPaintStatus(); aiPaintChatControls();
    } catch (e) { /* server briefly busy; try again next tick */ }
  }, 1000);
}

// ---------- status ----------
function aiSelectedInfo() {
  const p = AI.providers.find(x => x.endpoint === AI.selected.endpoint && x.provider === AI.selected.provider);
  return p || null;
}
const aiProviderName = p => (p ? p.name : "") || AI_NAMES[AI.selected.provider] || "";

function aiPaintStatus() {
  const box = aiEl("ai-status-box");
  if (!box) return;
  const st = AI.status, busy = st.state === "starting" || st.state === "loading";
  const lines = [];
  let title = "";
  if (AI.loadError) { title = "Could not reach Code Explainer's server."; lines.push(AI.loadError); }
  else if (st.state === "off") title = "The AI Explainer is off. Choose a model, then press Start.";
  else if (busy) title = st.step || "Working…";
  else if (st.state === "ready") {
    title = `Ready: ${st.model} on ${st.label || AI_NAMES[st.provider] || st.provider}`;
    if (st.startedBackend) lines.push("Code Explainer started the server for you and will stop it afterwards.");
    if (st.loadedByUs) lines.push("It loaded the model for you and will unload it afterwards.");
    else lines.push("The model was already loaded by you, so it will be left loaded.");
    if (st.secondsToIdle !== null && st.secondsToIdle !== undefined)
      lines.push(`Unloads when idle in ${aiClock(st.secondsToIdle)} (${Math.round(st.idleSeconds / 60)} minutes without use).`);
  } else if (st.state === "error") { title = st.error || "Something went wrong."; if (st.hint) lines.push(st.hint); }
  for (const w of st.warnings || []) lines.push(w);
  if (AI.startError) lines.push(AI.startError);
  const sel = AI.selected.model;
  const canStart = !!sel && !busy && !(st.state === "ready" && st.model === sel && st.endpoint === AI.selected.endpoint);
  box.replaceChildren(h("div", { id: "ai-status", class: "card ai-statuscard", "data-state": AI.loadError ? "error" : st.state,
      "data-idle": st.state === "ready" && st.secondsToIdle != null ? String(st.secondsToIdle) : "", "aria-live": "polite" },
    h("div", { class: "ai-title" }, (busy ? "⏳ " : st.state === "ready" ? "● " : st.state === "error" ? "⚠ " : "○ ") + title),
    lines.map(l => h("div", { class: "hint" }, l)),
    h("div", { class: "ai-buttons" },
      h("button", { id: "ai-start", class: "primary", disabled: !canStart, onclick: aiStart },
        st.state === "ready" && sel && st.model !== sel ? "Switch to this model" : "Start AI Explainer"),
      h("button", { id: "ai-stop", disabled: st.state === "off" && !busy, onclick: aiStop }, "Stop AI"),
      h("button", { id: "ai-refresh", onclick: () => { AI.loading = false; aiLoad(); } }, "Refresh models"))));
}

async function aiStart() {
  AI.startError = "";
  try {
    AI.status = await aiApi("/api/ai/start", AI.selected);
  } catch (e) {
    AI.startError = e.message;
    AI.status = { ...AI.status, state: AI.status.state === "ready" ? "ready" : "off" };
  }
  aiPaintStatus(); aiPaintChatControls();
}

async function aiStop() {
  try { AI.status = await aiApi("/api/ai/stop", {}); } catch (e) { toast(e.message); }
  aiPaintStatus(); aiPaintChatControls();
}

// ---------- model picker ----------
function aiPaintProviders() {
  const box = aiEl("ai-providers");
  if (!box) return;
  if (!AI.ready) { box.replaceChildren(h("div", { class: "hint" }, AI.loadError ? "" : "Looking for model servers on this computer…")); return; }
  const cards = AI.providers.map(p => {
    const models = p.models.filter(m => AI.showAll || m.kind === "llm");
    const running = p.reachable;
    const badges = [h("span", { class: "badge " + (running ? "ok" : "warn") }, running ? "Running" : "Not running")];
    if (!running && (p.provider === "ollama" || p.provider === "lmstudio"))
      badges.push(h("span", { class: "badge" }, p.installed ? "Installed" : "Not installed"));
    const body = running || models.length ? models.map(m => h("label", { class: "ai-model", "data-model-id": m.id },
      h("input", { type: "radio", name: "ai-model", checked: AI.selected.model === m.id && AI.selected.endpoint === p.endpoint,
        onchange: () => aiSelect(p, m) }),
      h("span", { class: "ai-mname" }, m.name || m.id),
      h("span", { class: "hint" }, [m.params, m.quant, aiBytes(m.size_bytes)].filter(Boolean).join(" · ")),
      m.loaded ? h("span", { class: "badge ok" }, "loaded") : null,
      m.kind !== "llm" ? h("span", { class: "badge" }, m.kind) : null,
      p.modelsSource === "disk" || p.modelsSource === "cli" ? h("span", { class: "badge" }, "found on disk") : null)) : [];
    if (!models.length) body.push(h("div", { class: "hint" }, running ? "No models found. Download one first." : (p.error || "Not reachable.")));
    return h("div", { class: "card ai-provider", "data-provider": p.provider, "data-endpoint": p.endpoint },
      h("div", { class: "ai-phead" }, h("b", {}, p.name || AI_NAMES[p.provider] || p.provider), badges,
        p.source === "config" ? h("button", { class: "ai-remove", title: "Remove this server", onclick: () => aiRemoveServer(p) }, "Remove") : null),
      h("div", { class: "hint" }, p.endpoint + (p.version ? ` · v${p.version}` : "")), body);
  });
  box.replaceChildren(h("h2", {}, "Models on this computer"),
    h("label", { class: "ai-showall" }, h("input", { type: "checkbox", id: "ai-show-all", checked: AI.showAll,
      onchange: e => { AI.showAll = e.target.checked; aiPaintProviders(); } }), " Show embedding and other non-chat models"),
    ...(cards.length ? cards : [h("div", { class: "hint" }, "No model servers found. Install Ollama or LM Studio, or add a server below.")]));
}

function aiSelect(p, m) {
  AI.selected = { provider: p.provider, endpoint: p.endpoint, model: m.id };
  AI.startError = "";
  aiPaintStatus(); aiPaintSettings(); aiPaintChatControls();
  aiQueueSave({ selected: AI.selected });
}

// ---------- add / remove servers ----------
function aiPaintAdd() {
  const box = aiEl("ai-add");
  if (!box || !AI.ready) { if (box) box.replaceChildren(); return; }
  const st = AI.settings;
  // The form remembers what was typed, so a repaint (after a rejected address, say) never wipes it.
  const field = (id, key, attrs) => {
    const el = h("input", { id, type: "text", spellcheck: "false", ...attrs, oninput: e => { AI.form[key] = e.target.value; } });
    el.value = AI.form[key];
    return el;
  };
  const sel = h("select", { id: "ai-add-provider", "aria-label": "Server type", onchange: e => { AI.form.provider = e.target.value; } },
    [["ollama", "Ollama"], ["lmstudio", "LM Studio"], ["openai", "OpenAI-compatible"]].map(([v, l]) => h("option", { value: v }, l)));
  sel.value = AI.form.provider;
  const url = field("ai-add-url", "url", { placeholder: "http://127.0.0.1:8080", "aria-label": "Server address" });
  const name = field("ai-add-name", "name", { placeholder: "Name (optional)", "aria-label": "Name" });
  const key = field("ai-add-key", "key", { placeholder: "API key variable name (optional)", "aria-label": "Environment variable holding the API key" });
  const err = h("div", { id: "ai-add-error", class: "ai-error", hidden: !AI.addError }, AI.addError);
  box.replaceChildren(
    h("details", { class: "card", open: !!AI.addError },
      h("summary", {}, "Add a server"),
      h("div", { class: "ai-form" }, sel, url, name, key,
        h("button", { id: "ai-add-submit", onclick: () => aiAddServer(sel.value, url.value, name.value, key.value) }, "Add"),
        err, h("div", { class: "hint" }, "Any server that speaks the OpenAI chat API works (llama.cpp, Jan, BionicGPT, vLLM…). "
          + "Keys are never stored: give the name of an environment variable that holds the key."))),
    h("details", { class: "card", open: AI.advancedOpen || st.allow_remote, ontoggle: e => { AI.advancedOpen = e.target.open; } },
      h("summary", {}, "Advanced"),
      h("div", { class: "ai-adv" },
        h("label", {}, h("input", { type: "checkbox", id: "ai-autostart", checked: st.autostart,
          onchange: e => aiQueueSave({ autostart: e.target.checked }) }), " Start the server (Ollama or LM Studio) for me if it is not running"),
        h("label", {}, "Unload after ", h("input", { type: "number", id: "ai-idle", min: 1, max: 240, value: String(st.idle_minutes), style: "width:4.5em",
          onchange: e => aiQueueSave({ idle_minutes: +e.target.value }) }), " minutes without use"),
        h("label", {}, h("input", { type: "checkbox", id: "ai-allow-remote", checked: st.allow_remote,
          onchange: e => aiQueueSave({ allow_remote: e.target.checked }).then(aiPaintAdd) }), " Allow servers on other computers"),
        h("div", { id: "ai-remote-warning", class: "ai-error", hidden: !st.allow_remote },
          "Warning: with this on, Code Explainer can send your code to servers on other computers. Only add servers you trust."),
        h("div", { class: "ai-cacherow" }, h("span", { id: "ai-cache-info", class: "hint" }, "Saved explanations: …"),
          h("button", { id: "ai-cache-clear", onclick: aiClearCache }, "Clear"),
          h("div", { class: "hint" }, "Explanations are saved on this computer (not in your project) so coming back is instant. "
            + "They are reused only while the code, model, settings and reading level are unchanged.")))));
  aiCacheRefresh();
}

async function aiCacheRefresh() {
  try {
    const c = await aiApi("/api/ai/cache");
    const el = aiEl("ai-cache-info");
    if (el) el.textContent = `${c.entries} saved explanation${c.entries === 1 ? "" : "s"} (${aiBytes(c.bytes) || "0 KB"})`;
  } catch (e) { /* the server may be busy; the next repaint tries again */ }
}

async function aiClearCache() {
  try { await aiApi("/api/ai/cache/clear", {}); toast("Saved explanations cleared."); } catch (e) { toast(e.message); }
  aiCacheRefresh();
}

async function aiAddServer(provider, urlText, name, keyEnv) {
  const url = urlText.trim();
  if (!url) { AI.addError = "Enter the server's address first."; aiPaintAdd(); return; }
  const mine = AI.settings.endpoints.map(e => ({ ...e }));
  mine.push({ provider, url, name: name.trim(), api_key_env: keyEnv.trim() });
  try {
    const saved = (await aiApi("/api/ai/settings", { endpoints: mine })).settings;
    const norm = u => u.replace(/\/+$/, "").toLowerCase();
    if (!saved.endpoints.some(e => e.provider === provider && norm(e.url) === norm(url))) {
      AI.addError = "That address is not allowed. Only this computer (127.0.0.1 or localhost) is allowed unless you turn on “Allow servers on other computers” under Advanced. "
        + "Also check it starts with http:// and has no username or password in it.";
      aiPaintAdd();
      return;
    }
    AI.settings = saved; AI.addError = "";
    AI.form = { provider, url: "", name: "", key: "" };
    AI.loading = false; await aiLoad();
  } catch (e) { AI.addError = e.message; aiPaintAdd(); }
}

async function aiRemoveServer(p) {
  const keep = AI.settings.endpoints.filter(e => !(e.provider === p.provider && e.url.replace(/\/+$/, "") === p.endpoint.replace(/\/+$/, "")));
  try {
    AI.settings = (await aiApi("/api/ai/settings", { endpoints: keep })).settings;
    if (AI.selected.endpoint === p.endpoint) AI.selected = { provider: "", endpoint: "", model: "" };
    AI.loading = false; await aiLoad();
  } catch (e) { toast(e.message); }
}

// ---------- saving settings ----------
function aiQueueSave(patch) {
  AI.pending = AI.pending || {};
  for (const [k, v] of Object.entries(patch)) {
    if (k === "params") AI.pending.params = { ...(AI.pending.params || {}), ...v };
    else AI.pending[k] = v;
  }
  clearTimeout(AI.saveTimer);
  return new Promise(resolve => {
    AI.saveTimer = setTimeout(async () => { await aiFlushSave(); resolve(); }, 350);
  });
}

async function aiFlushSave() {
  if (!AI.pending) return;
  const body = AI.pending; AI.pending = null;
  try {
    const r = await aiApi("/api/ai/settings", body);
    AI.settings = r.settings;
    aiSyncSettingsInputs();
    AI.saved = true; clearTimeout(AI.savedTimer);
    const el = aiEl("ai-saved"); if (el) el.hidden = false;
    AI.savedTimer = setTimeout(() => { AI.saved = false; const e2 = aiEl("ai-saved"); if (e2) e2.hidden = true; }, 1800);
  } catch (e) { toast("Could not save the AI settings: " + e.message, 6000); }
}

// ---------- tuning panel ----------
function aiSupport() {
  const prov = AI.selected.provider;
  if (!prov || !AI.schema) return { all: true, supported: null, loadTime: new Set() };
  return { all: false, supported: new Set(AI.schema.supported[prov] || []), loadTime: new Set((AI.schema.loadTime || {})[prov] || []) };
}

function aiPaintSettings() {
  const box = aiEl("ai-settings");
  if (!box) return;
  if (!AI.ready) { box.replaceChildren(); return; }
  const sch = AI.schema, p = AI.settings.params;
  const rows = AI_PARAM_ORDER.map(name => {
    const spec = sch.params[name];
    const setParam = v => aiQueueSave({ params: { [name]: v } });
    let controls;
    if (name === "seed") {
      const inp = h("input", { type: "number", "data-param": "seed", min: spec.min, max: spec.max, step: 1, placeholder: "random", "aria-label": spec.label });
      inp.value = aiNum(p.seed);
      inp.addEventListener("change", () => setParam(inp.value === "" ? null : Math.round(+inp.value)));
      controls = [inp];
    } else {
      const range = h("input", { type: "range", "data-param": name, min: spec.min, max: spec.max, step: spec.step, "aria-label": spec.label });
      const num = h("input", { type: "number", "data-param-num": name, min: spec.min, max: spec.max, step: spec.step, "aria-label": spec.label + " (value)" });
      range.value = aiNum(p[name]); num.value = aiNum(p[name]);
      range.addEventListener("input", () => { num.value = range.value; setParam(+range.value); });
      num.addEventListener("change", () => {
        let v = parseFloat(num.value);
        if (!isFinite(v)) v = spec.default;
        v = Math.min(Math.max(v, spec.min), spec.max);
        if (spec.type === "int") v = Math.round(v);
        num.value = aiNum(v); range.value = aiNum(v); setParam(v);
      });
      controls = [range, num];
    }
    return h("div", { class: "ai-param", "data-row": name },
      h("label", { class: "ai-plabel", title: spec.help }, spec.label), controls,
      h("div", { class: "hint", "data-param-note": name }), h("div", { class: "hint ai-help" }, spec.help));
  });
  const presets = Object.keys(sch.presets).map(k => h("button", { "data-preset": k, onclick: () => aiPreset(k) }, k[0].toUpperCase() + k.slice(1)));
  const length = h("select", { id: "ai-length", "aria-label": "Answer length", onchange: e => aiQueueSave({ params: { length: e.target.value } }) },
    sch.lengths.map(l => h("option", { value: l }, l[0].toUpperCase() + l.slice(1))));
  length.value = p.length;
  const instr = h("textarea", { id: "ai-instruction", rows: 3, maxlength: sch.maxInstruction, placeholder: "Extra instructions for every answer, e.g. “Answer in French.”",
    "aria-label": "Extra instructions", oninput: e => aiQueueSave({ params: { instruction: e.target.value } }) });
  instr.value = p.instruction || "";
  box.replaceChildren(h("div", { class: "card" },
    h("div", { class: "ai-phead" }, h("h2", { style: "margin:0" }, "Tune the answers"), h("span", { id: "ai-saved", class: "badge ok", hidden: !AI.saved }, "Saved")),
    h("div", { class: "ai-presets" }, h("span", { class: "hint" }, "Presets: "), presets,
      h("button", { id: "ai-reset", onclick: aiReset }, "Reset")),
    h("p", { class: "hint" }, "Answers use the “Explain” level from the top menu. Changes apply to the next answer."),
    rows, h("div", { class: "ai-param" }, h("label", { class: "ai-plabel", for: "ai-length" }, "Answer length"), length),
    h("div", { class: "ai-param" }, h("label", { class: "ai-plabel", for: "ai-instruction" }, "Extra instructions"), instr)));
  aiApplyGreying();
}

function aiApplyGreying() {
  const sup = aiSupport();
  for (const name of AI_PARAM_ORDER) {
    const off = !sup.all && !sup.supported.has(name);
    for (const el of document.querySelectorAll(`[data-param="${name}"], [data-param-num="${name}"]`)) el.disabled = off;
    const row = document.querySelector(`[data-row="${name}"]`);
    if (row) row.classList.toggle("off", off);
    const note = document.querySelector(`[data-param-note="${name}"]`);
    if (note) note.textContent = off ? `Not used by ${AI_NAMES[AI.selected.provider] || "this server"}.`
      : (!sup.all && sup.loadTime.has(name)) ? "Applied when the model is loaded (stop and start the AI to change it)." : "";
  }
}

function aiSyncSettingsInputs() {
  const p = AI.settings.params;
  for (const name of AI_PARAM_ORDER) {
    for (const el of document.querySelectorAll(`[data-param="${name}"], [data-param-num="${name}"]`))
      if (document.activeElement !== el) el.value = aiNum(p[name]);
  }
  const len = aiEl("ai-length"); if (len && document.activeElement !== len) len.value = p.length;
  const idle = aiEl("ai-idle"); if (idle && document.activeElement !== idle) idle.value = String(AI.settings.idle_minutes);
}

async function aiPreset(name) {
  clearTimeout(AI.saveTimer); AI.pending = null;
  try { AI.settings = (await aiApi("/api/ai/settings", { params: { preset: name } })).settings; aiPaintSettings(); aiFlash(); }
  catch (e) { toast(e.message); }
}

async function aiReset() {
  clearTimeout(AI.saveTimer); AI.pending = null;
  const d = {};
  for (const name of AI_PARAM_ORDER) d[name] = AI.schema.params[name].default;
  d.length = "normal"; d.instruction = "";
  try { AI.settings = (await aiApi("/api/ai/settings", { params: d })).settings; aiPaintSettings(); aiFlash(); }
  catch (e) { toast(e.message); }
}

function aiFlash() {
  const el = aiEl("ai-saved"); if (!el) return;
  el.hidden = false; clearTimeout(AI.savedTimer);
  AI.savedTimer = setTimeout(() => { const e2 = aiEl("ai-saved"); if (e2) e2.hidden = true; }, 1800);
}

// ---------- chat ----------
// A model may cite code as `path:line` or `path:start-end`. Only citations that point at a real file and a real line become
// links; the rest are shown plainly, marked, with the reason, so an invented citation is easy to spot.
const AI_CITE = /([\w./-]+\.[A-Za-z][A-Za-z0-9]{0,5}):(\d+)(?:-(\d+))?/g;

function aiResolveCite(name, line) {
  let path = BY.has(name) ? name : null;
  if (!path) {
    const hits = FILES.filter(f => f.path === name || f.path.endsWith("/" + name));
    if (hits.length > 1) return { ok: false, why: `ambiguous: ${hits.length} files match ${name}` };
    if (hits.length === 1) path = hits[0].path;
  }
  if (!path) return { ok: false, why: "not found in this project" };
  const lines = BY.get(path).lines || 0;
  if (line < 1 || line > Math.max(lines, 1)) return { ok: false, why: `${path} has only ${lines} lines` };
  return { ok: true, path };
}

function aiOpenCode(path, line) { if (document.getElementById("chart")) closeChart(); openFile(path, line); }

function aiSourceChips(sources) {
  return h("div", { class: "ai-sources" }, h("span", { class: "hint" }, "Based on: "),
    sources.map(src => h("a", { class: "ai-source", href: "#", title: src.reason,
      onclick: e => { e.preventDefault(); aiOpenCode(src.path, src.line); } }, `${src.path}:${src.line}-${src.end} ${src.label}`)));
}

function aiCiteNodes(text) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(AI_CITE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const line = +m[2], r = aiResolveCite(m[1], line);
    if (r.ok) {
      out.push(h("a", { class: "ai-cite", href: "#", title: `Open ${r.path} at line ${line}`,
        onclick: e => { e.preventDefault(); aiOpenCode(r.path, line); } }, m[0]));
    } else out.push(h("span", { class: "ai-cite bad", title: r.why }, m[0]));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// `code` -> <code> and citations -> links, as DOM nodes.
function aiSpans(text) {
  const out = [];
  text.split(/`([^`\n]+)`/).forEach((piece, j) => {
    if (piece) out.push(j % 2 ? h("code", {}, aiCiteNodes(piece)) : h("span", {}, aiCiteNodes(piece)));
  });
  return out;
}

// **bold** -> <strong> (which may contain code and citations). Nothing else is interpreted.
function aiInline(parent, text) {
  text.split(/\*\*([^*\n]+)\*\*/).forEach((seg, i) => {
    if (seg) parent.append(i % 2 ? h("strong", {}, aiSpans(seg)) : h("span", {}, aiSpans(seg)));
  });
}

function aiRenderText(parent, text) {
  String(text).split(/```/).forEach((part, i) => {
    if (i % 2 === 1) parent.append(h("pre", { class: "ai-code" }, part.replace(/^[^\n]*\n/, "").replace(/\n$/, "")));
    else if (part) aiInline(parent, part);
  });
}

function aiFilePath() { return state.selected && BY.get(state.selected) ? state.selected : ""; }

function aiPaintChat() {
  const box = aiEl("ai-chat");
  if (!box) return;
  // The tab refreshes itself in the background after it is drawn; typing must carry on where the person was.
  const before = aiEl("ai-input");
  const keep = before && document.activeElement === before ? { start: before.selectionStart, end: before.selectionEnd } : null;
  const input = h("textarea", { id: "ai-input", rows: 3, placeholder: "Ask about this code…", "aria-label": "Your question",
    oninput: e => { AI.chat.draft = e.target.value; aiPaintChatControls(); },
    onkeydown: e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); aiSend(); } } });
  input.value = AI.chat.draft;
  const path = aiFilePath();
  box.replaceChildren(h("div", { class: "card ai-chatcard" },
    h("div", { class: "ai-phead" }, h("h2", { style: "margin:0" }, "Ask the AI"), h("button", { id: "ai-clear", onclick: aiClear }, "Clear")),
    h("div", { id: "ai-chat-hint", class: "hint" }),
    h("div", { id: "ai-log", class: "ai-log", "aria-live": "polite" }),
    h("div", { class: "ai-ctx" }, h("input", { type: "checkbox", id: "ai-ground", checked: AI.ground,
      onchange: e => { AI.ground = e.target.checked; } }),
      h("label", { for: "ai-ground" }, " Search the whole project for relevant code (recommended)")),
    path ? h("div", { class: "ai-ctx" }, h("input", { type: "checkbox", id: "ai-context", checked: AI.useContext,
      onchange: e => { AI.useContext = e.target.checked; } }),
      h("label", { for: "ai-context" }, ` Focus on the open file (${path})`)) : null,
    h("div", { class: "ai-compose" }, input,
      h("div", { class: "ai-buttons" }, h("button", { id: "ai-send", class: "primary", onclick: aiSend }, "Send"),
        h("button", { id: "ai-chat-stop", hidden: true, onclick: aiStopAnswer }, "Stop")))));
  aiPaintLog();
  aiPaintChatControls();
  if (keep && !input.disabled) { input.focus(); input.setSelectionRange(keep.start, keep.end); }
}

function aiPaintChatControls() {
  const input = aiEl("ai-input"), send = aiEl("ai-send"), stop = aiEl("ai-chat-stop"), hint = aiEl("ai-chat-hint");
  if (!input) return;
  const ready = AI.status.state === "ready", streaming = AI.chat.streaming;
  input.disabled = !ready || streaming;
  send.disabled = !ready || streaming || !input.value.trim();
  stop.hidden = !streaming;
  aiEl("ai-clear").disabled = streaming;
  hint.textContent = ready ? "" : "Start the AI Explainer first (choose a model, then press Start).";
}

function aiPaintLog() {
  const log = aiEl("ai-log");
  if (!log) return;
  log.replaceChildren(...AI.chat.messages.map(aiMessageEl));
  log.scrollTop = log.scrollHeight;
}

function aiMessageEl(m) {
  const el = h("div", { class: `ai-msg ${m.role}`, "data-msg": String(m.id) });
  const text = h("div", { class: "ai-text" });
  if (m.role === "user") text.textContent = m.text;
  else aiRenderText(text, m.text);
  el.append(text);
  if (m.role === "assistant") {
    if (m.thinking && !m.text && !m.done) el.append(h("div", { class: "hint ai-thinking" }, "Thinking…"));
    if (m.retrievalNote) el.append(h("div", { class: "ai-retrieval hint" }, m.retrievalNote));
    if (m.sent && m.sent.sources && m.sent.sources.length) el.append(aiSourceChips(m.sent.sources));
    if (m.note) el.append(h("div", { class: `ai-note ${m.note.kind}` }, m.note.text));
    if (m.stats) el.append(h("div", { class: "ai-stats hint" }, m.stats));
    if (m.sent) {
      const open = AI.chat.sentOpen.has(m.id);
      el.append(h("button", { class: "ai-show-sent", onclick: () => { open ? AI.chat.sentOpen.delete(m.id) : AI.chat.sentOpen.add(m.id); aiPaintLog(); } },
        open ? "Hide what was sent" : "Show what was sent"));
      if (open) el.append(h("pre", { class: "ai-sent-body" }, JSON.stringify(
        { provider: m.sent.provider, model: m.sent.model, options: m.sent.options, trimmed: m.sent.trimmed,
          retrieval: m.sent.retrieval, sources: m.sent.sources, messages: m.sent.messages }, null, 2)));
    }
  }
  return el;
}

function aiUpdateCurrent(m) {
  const old = document.querySelector(`[data-msg="${m.id}"]`);
  if (!old) return;
  const log = aiEl("ai-log");
  const stick = log && log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  old.replaceWith(aiMessageEl(m));
  if (log && stick) log.scrollTop = log.scrollHeight;
}

function aiClear() {
  if (AI.chat.streaming) return;
  AI.chat.messages = []; AI.chat.sentOpen.clear(); aiPaintLog();
}

// Reads a server-sent-events response to its end, calling onEvent for each `data:` payload that parses.
async function aiReadSSE(response, onEvent) {
  const reader = response.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2);
      for (const line of block.split("\n")) if (line.startsWith("data:")) { try { onEvent(JSON.parse(line.slice(5))); } catch (e) { /* skip a bad event */ } }
    }
  }
}

async function aiSend() {
  const input = aiEl("ai-input");
  const text = (input ? input.value : AI.chat.draft).trim();
  if (!text || AI.chat.streaming || AI.status.state !== "ready") return;
  const history = AI.chat.messages.filter(m => m.text && !(m.role === "assistant" && m.note && m.note.kind === "error" && !m.done))
    .map(m => ({ role: m.role, content: m.text })).slice(-20);
  const user = { id: AI.chat.nextId++, role: "user", text };
  const cur = { id: AI.chat.nextId++, role: "assistant", text: "", done: false, thinking: false, note: null, stats: "", sent: null, rid: "" };
  AI.chat.messages.push(user, cur);
  AI.chat.cur = cur; AI.chat.streaming = true; AI.chat.draft = "";
  if (input) input.value = "";
  const ctl = new AbortController();
  AI.chat.ctl = ctl;
  aiPaintLog(); aiPaintChatControls();
  const focusPath = AI.useContext ? aiFilePath() : "";
  const body = { messages: [...history, { role: "user", content: text }], params: { level: LEVEL }, ground: AI.ground,
    focus: focusPath ? { path: focusPath } : null };
  try {
    const r = await fetch("/api/ai/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctl.signal });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).error || msg; } catch (e) { /* keep status */ }
      throw new Error(msg);
    }
    await aiReadSSE(r, ev => aiOnEvent(cur, ev));
  } catch (e) {
    if (e.name === "AbortError") cur.note = cur.note || { kind: "info", text: "Stopped." };
    else cur.note = { kind: "error", text: "Lost the connection: " + e.message };
  }
  AI.chat.streaming = false; AI.chat.cur = null; AI.chat.ctl = null;
  cur.done = cur.done || false;
  aiUpdateCurrent(cur); aiPaintChatControls();
  const next = aiEl("ai-input"); if (next && !next.disabled) next.focus();
}

function aiOnEvent(cur, ev) {
  switch (ev.type) {
    case "start":
      cur.sent = ev; cur.rid = ev.id;
      if (ev.retrieval && ev.retrieval.error) cur.retrievalNote = "Searching the project failed, so this answer does not use the project's code.";
      else if (ev.retrieval && ev.retrieval.enabled && !ev.retrieval.matched)
        cur.retrievalNote = "No matching code was found in the project, so this answer comes from your question alone and may be a guess.";
      if (ev.trimmed && (ev.trimmed.dropped || ev.trimmed.truncated))
        cur.note = { kind: "info", text: "Part of the conversation or your text was left out to fit the model's memory (context size)." };
      aiUpdateCurrent(cur); break;
    case "thinking": cur.thinking = true; aiUpdateCurrent(cur); break;
    case "token": cur.text += ev.text; cur.thinking = false; aiUpdateCurrentText(cur); break;
    case "done": {
      cur.done = true;
      const s = ev.stats || {}, bits = [];
      if (s.completion_tokens) bits.push(`${s.completion_tokens} tokens`);
      if (s.tokens_per_second) bits.push(`${s.tokens_per_second} tokens/s`);
      if (ev.elapsed != null) bits.push(`${ev.elapsed} s`);
      if (cur.sent) bits.push(cur.sent.model);
      cur.stats = bits.join(" · ") || "done";
      if (ev.truncated) cur.note = { kind: "warn", text: "The answer was cut off at the length limit. Raise “Max answer length” in the settings and ask again." };
      aiUpdateCurrent(cur); break;
    }
    case "error": cur.note = { kind: "error", text: ev.message + (ev.hint ? ` ${ev.hint}` : "") }; aiUpdateCurrent(cur); break;
    case "cancelled": cur.note = { kind: "info", text: "Stopped." }; aiUpdateCurrent(cur); break;
    default: break;
  }
}

function aiUpdateCurrentText(cur) {
  const el = document.querySelector(`[data-msg="${cur.id}"] .ai-text`);
  if (!el) return;
  el.replaceChildren();
  aiRenderText(el, cur.text);
  const log = aiEl("ai-log");
  if (log && log.scrollHeight - log.scrollTop - log.clientHeight < 60) log.scrollTop = log.scrollHeight;
}

function aiStopAnswer() {
  const cur = AI.chat.cur;
  if (!cur) return;
  cur.note = { kind: "info", text: "Stopped." };
  if (cur.rid) aiApi("/api/ai/cancel", { id: cur.rid }).catch(() => { /* aborting the request below stops it anyway */ });
  if (AI.chat.ctl) AI.chat.ctl.abort();
}


// ---------- "Explain with AI" buttons (files, functions, flow steps, changes) ----------
// Results live in AIX, keyed by what is explained and the reading level, so they survive the panels being redrawn.
// The browser only names the target; the server builds the prompt, and saved answers are reused (even with the AI stopped).
const AIX = new Map();
const aixKey = t => [LEVEL, t.kind, t.path || "", t.symbol || "", t.parent || "", t.node == null ? "" : t.node, t.part || "", t.from || "", t.to || ""].join("|");
const AIX_LABELS = { change: "✨ Explain this change with AI", flowstep: "✨ Explain this box with AI", part: "✨ Explain this part with AI", link: "✨ Explain this arrow with AI" };

function aixControls(target, compact, label) {
  if (!SERVE) return aixBaked(target);
  if (["symbol", "flow", "flowstep"].includes(target.kind) && (!target.symbol || target.symbol === "<module>")) return null;
  const el = h("div", { class: "aix", "data-aix": aixKey(target) });
  el._target = target; el._compact = !!compact; el._label = label || "";
  aixFill(el);
  return el;
}

// A static report cannot talk to a model, but `analyze --ai` can write explanations into it ahead of time.
// They are shown read-only, and always labelled as AI-written.
const AIX_LEVELS = { kid: "beginner-friendly", plain: "plain-English", tech: "technical" };
function aixBaked(target) {
  const b = D.aiExplain, item = b && b.items && b.items[target.kind + "|" + (target.part || target.path)];
  if (!item) return null;
  const text = h("div", { class: "aix-text" });
  aiRenderText(text, item.text);
  return h("div", { class: "aix aix-baked", "data-kind": target.kind },
    h("div", { class: "aix-box" },
      h("div", { class: "aix-head" }, h("span", { class: "badge aix-label" }, "AI explanation"),
        h("span", { class: "hint" }, `Written ahead of time by ${b.model} (${AIX_LEVELS[b.level] || b.level}${b.generated ? ", " + b.generated : ""}). `
          + "AI can make mistakes: check it against the code.")),
      text));
}

function aixFill(el) {
  el.replaceChildren(...aixBody(el._target, aixKey(el._target), el._compact, el._label));
}

function aixPaint(key) {
  for (const el of document.querySelectorAll("[data-aix]")) if (el.dataset.aix === key && el._target) aixFill(el);
}

function aixPaintText(key, st) {
  for (const el of document.querySelectorAll("[data-aix]")) {
    if (el.dataset.aix !== key) continue;
    const box = el.querySelector(".aix-text");
    if (box) { box.replaceChildren(); aiRenderText(box, st.text); }
  }
}

function aixBody(target, key, compact, label) {
  const st = AIX.get(key);
  if (!st) return [h("button", { class: "aix-btn" + (compact ? " compact" : ""), "data-kind": target.kind, title: "Explain with the local AI",
    onclick: () => aixRun(target, key, false) }, label || (compact ? "✨ Explain" : (AIX_LABELS[target.kind] || "✨ Explain with AI")))];
  const text = h("div", { class: "aix-text" });
  aiRenderText(text, st.text);
  const box = h("div", { class: "aix-box" });
  if (st.state === "running" && !st.text) box.append(h("div", { class: "hint aix-working" }, st.step || (st.thinking ? "Thinking…" : "Waiting for the model…")));
  if (st.text) box.append(text);
  if (st.note) box.append(h("div", { class: `aix-note ${st.note.kind}` }, st.note.text));
  if (st.sources.length) box.append(aiSourceChips(st.sources));
  const foot = h("div", { class: "aix-foot" });
  if (st.cached) foot.append(h("span", { class: "badge ok aix-cached", title: "Shown from a saved answer; the model was not used" }, "saved answer"));
  if (st.stats) foot.append(h("span", { class: "hint aix-stats" }, st.stats));
  if (st.state === "running") foot.append(h("button", { class: "aix-stop", onclick: () => aixStop(key) }, "Stop"));
  else {
    foot.append(h("button", { class: st.state === "done" ? "aix-regen" : "aix-btn", "data-kind": target.kind,
      onclick: () => aixRun(target, key, true) }, st.state === "done" ? "Regenerate" : "Try again"));
    if (st.notReady) {
      if (st.selected) foot.append(h("button", { class: "aix-start", onclick: () => aixStartThenRun(target, key) }, `Start AI with ${st.selected.model} and explain`));
      foot.append(h("button", { class: "aix-open", onclick: () => setTab("ai") }, "Open the AI tab"));
    }
    foot.append(h("button", { class: "aix-hide", onclick: () => { AIX.delete(key); aixPaint(key); } }, "Hide"));
  }
  box.append(foot);
  return [box];
}

async function aixRun(target, key, regenerate) {
  const st = { state: "running", text: "", sources: [], note: null, cached: false, stats: "", rid: "", ctl: new AbortController(),
    thinking: false, step: "", notReady: false, selected: null };
  AIX.set(key, st);
  aixPaint(key);
  try {
    const r = await fetch("/api/ai/explain", { method: "POST", headers: { "Content-Type": "application/json" }, signal: st.ctl.signal,
      body: JSON.stringify({ ...target, regenerate: !!regenerate, params: { level: LEVEL } }) });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).error || msg; } catch (e) { /* keep status */ }
      throw new Error(msg);
    }
    await aiReadSSE(r, ev => aixOnEvent(st, key, ev));
  } catch (e) {
    if (e.name === "AbortError") { st.note = st.note || { kind: "info", text: "Stopped." }; st.state = "stopped"; }
    else { st.note = { kind: "error", text: "Lost the connection: " + e.message }; st.state = "error"; }
  }
  if (st.state === "running") st.state = "done";
  if (st.notReady) {
    try { const sel = (await aiApi("/api/ai/settings")).settings.selected; st.selected = sel && sel.model ? sel : null; } catch (e) { /* no remembered model */ }
  }
  aixPaint(key);
}

function aixOnEvent(st, key, ev) {
  switch (ev.type) {
    case "start": st.sources = ev.sources || []; st.cached = !!ev.cached; st.rid = ev.id; aixPaint(key); break;
    case "thinking": st.thinking = true; aixPaint(key); break;
    case "token": st.text += ev.text; if (st.text.length === ev.text.length) aixPaint(key); else aixPaintText(key, st); break;
    case "done": {
      st.state = "done"; st.cached = !!ev.cached;
      const s = ev.stats || {}, bits = [];
      if (s.completion_tokens) bits.push(`${s.completion_tokens} tokens`);
      if (s.tokens_per_second) bits.push(`${s.tokens_per_second} tokens/s`);
      if (!st.cached && ev.elapsed != null) bits.push(`${ev.elapsed} s`);
      st.stats = bits.join(" · ") || "done";
      if (ev.truncated) st.note = { kind: "warn", text: "The answer was cut off at the length limit. Raise “Max answer length” in the AI settings and try again." };
      aixPaint(key); break;
    }
    case "error":
      st.state = "error"; st.notReady = ev.kind === "not_ready";
      st.note = { kind: "error", text: ev.message + (ev.hint ? ` ${ev.hint}` : "") };
      break;
    case "cancelled": st.note = { kind: "info", text: "Stopped." }; st.state = "stopped"; break;
    default: break;
  }
}

function aixStop(key) {
  const st = AIX.get(key);
  if (!st || st.state !== "running") return;
  st.note = { kind: "info", text: "Stopped." };
  if (st.rid) aiApi("/api/ai/cancel", { id: st.rid }).catch(() => { /* aborting the request stops it anyway */ });
  st.ctl.abort();
}

// One click: start the remembered model, wait for it, then explain.
async function aixStartThenRun(target, key) {
  const st = AIX.get(key);
  if (!st || !st.selected) return;
  const sel = st.selected;
  st.state = "running"; st.text = ""; st.note = null; st.notReady = false; st.step = `Starting ${sel.model}…`;
  aixPaint(key);
  try {
    AI.status = await aiApi("/api/ai/start", sel);
    const end = Date.now() + 180000;
    while (Date.now() < end) {
      AI.status = await aiApi("/api/ai/status");
      if (AI.status.state === "ready") { aixRun(target, key, false); return; }
      if (AI.status.state === "error") throw new Error(AI.status.error + (AI.status.hint ? ` ${AI.status.hint}` : ""));
      st.step = AI.status.step || st.step;
      aixPaint(key);
      await new Promise(res => setTimeout(res, 500));
    }
    throw new Error("The AI took too long to start.");
  } catch (e) {
    st.state = "error"; st.note = { kind: "error", text: e.message }; st.step = "";
    aixPaint(key);
  }
}
