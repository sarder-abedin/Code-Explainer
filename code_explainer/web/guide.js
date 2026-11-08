// ---------- ease of use: a start card, a shortcuts sheet, and a first-visit tip ----------
const GUIDE_TIP_KEY = "ce.tipSeen";
function guideTipSeen() { try { return localStorage.getItem(GUIDE_TIP_KEY) === "1"; } catch (e) { return false; } }
function guideTipDone() { try { localStorage.setItem(GUIDE_TIP_KEY, "1"); } catch (e) { /* storage may be blocked */ } }

const GUIDE_SHORTCUTS = [["/", "Search files and functions"], ["?", "Show this list"], ["Esc", "Close the search, a flowchart or this list"],
  ["↑ ↓ Enter", "Move through search results and open one"], ["← →", "Move between the tabs of a file panel (when a tab is focused)"],
  ["Alt + ←", "Go back to the last file or view (the browser's Back also works)"]];

function startCard() {
  const go = (label, hint, run) => h("button", { class: "start-opt", onclick: run }, h("b", {}, label), h("span", {}, hint));
  const opts = [
    go("See the big picture", "A map of the parts and how they talk", () => setTab("map")),
    go("Follow what happens when it runs", "Step through what calls what", () => setTab("flow")),
    go("Check what is risky or untested", "Safety findings and the tests-to-code map", () => setTab("health")),
    CH ? go("Review what changed", "What this change touches and what to check", () => setTab("changes")) : null,
    go("Read the guided story", "A short tour in plain words", () => setTab("story")),
    go("Find a file or function", "Type a name; press / any time", () => { const i = document.getElementById("search"); i.focus(); i.select(); })].filter(Boolean);
  const tip = guideTipSeen() ? null : h("div", { class: "start-tip", role: "note" },
    h("span", {}, "New here? Pick one above. Press ", h("kbd", {}, "?"), " at any time to see the keyboard shortcuts."),
    h("button", { onclick: e => { guideTipDone(); e.currentTarget.parentElement.remove(); } }, "Got it"));
  return h("section", { class: "start-card card", "aria-label": "Where to start" },
    h("h2", { style: "margin:0 0 8px" }, "What do you want to do?"), h("div", { class: "start-opts" }, opts), tip);
}

let GUIDE_OPENER = null;
function closeHelp() {
  const el = document.getElementById("help-sheet");
  if (!el) return;
  el.remove();
  document.removeEventListener("keydown", closeHelp.onKey);
  if (GUIDE_OPENER && document.contains(GUIDE_OPENER)) GUIDE_OPENER.focus();
  GUIDE_OPENER = null;
}
function openHelp(opener) {
  if (document.getElementById("help-sheet")) return;
  GUIDE_OPENER = opener || document.activeElement;
  closeHelp.onKey = e => { if (e.key === "Escape") { e.preventDefault(); closeHelp(); } };
  document.addEventListener("keydown", closeHelp.onKey);
  const close = h("button", { class: "help-close", "aria-label": "Close the list of shortcuts", onclick: closeHelp }, "✕");
  document.body.append(h("div", { id: "help-sheet", class: "chart-overlay", role: "dialog", "aria-modal": "true", "aria-label": "Keyboard shortcuts",
    onclick: e => { if (e.target.id === "help-sheet") closeHelp(); } },
  h("div", { class: "help-box" }, h("div", { class: "rd-uses-head" }, h("h2", { style: "margin:0" }, "Keyboard shortcuts"), close),
    h("dl", {}, GUIDE_SHORTCUTS.map(([k, d]) => [h("dt", {}, h("kbd", {}, k)), h("dd", {}, d)])))));
  close.focus();
}
function setupGuide() {
  document.getElementById("help").onclick = e => openHelp(e.currentTarget);
  document.addEventListener("keydown", e => {
    if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); openHelp(); }
  });
}
