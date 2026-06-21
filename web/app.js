/* Yap web UI logic — shared by settings.html, onboarding.html, panel.html.
   Talks to Python (yap.py) through the WKWebView "yap" message handler. */

function send(action, payload) {
  try {
    window.webkit.messageHandlers.yap.postMessage(JSON.stringify({ action: action, payload: payload }));
  } catch (e) { /* running outside the app (e.g. a browser preview) */ }
}

var HOTKEYS = [
  { v: "left",  label: "⌥ + ←" },
  { v: "up",    label: "⌥ + ↑" },
  { v: "right", label: "⌥ + →" },
  { v: "down",  label: "⌥ + ↓" }
];
// Text modes (e.g. Rewrite) fire on Control+Option + a letter — separate from
// the voice "Right Option + arrow" combos, so the two can't conflict.
var TEXT_HOTKEYS = ["r", "e", "t", "y", "u", "i", "o", "p", "j", "k", "l"].map(
  function (c) { return { v: c, label: "⌃⌥ + " + c.toUpperCase() }; }
);
var DEFAULT_REWRITE_PROMPT =
  "Rewrite the following text in a clean, clear, professional way. Keep my " +
  "meaning and key points. Return only the rewritten text, with no preamble " +
  "or commentary. Do not use em dashes; use commas, colons, or separate " +
  "sentences instead.";
var MODEL_OPTIONS = [
  "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash",
  "gemini-1.5-flash", "gemini-1.5-pro"
];
var MODE_EMOJI = ["🧹", "📝", "🎙️", "🌍", "✨", "💬", "📌", "⚡"];

var CONFIG = { api_key: "", model: "gemini-2.5-flash", modes: [] };

/* =========================== SETTINGS =============================== */

function isSettings() { return !!document.getElementById("modes"); }

function el(tag, cls, html) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

function hotkeySelect(value) {
  var s = el("select", "hk-select");
  HOTKEYS.forEach(function (h) {
    var o = el("option"); o.value = h.v; o.textContent = h.label;
    if (h.v === value) o.selected = true;
    s.appendChild(o);
  });
  return s;
}

function letterSelect(value) {
  // The Control+Option + letter picker for text modes. If the saved letter
  // isn't in our default list (hand-edited config), show it anyway so it sticks.
  var s = el("select", "hk-select");
  var opts = TEXT_HOTKEYS.slice();
  if (value && !opts.some(function (h) { return h.v === value; })) {
    opts.unshift({ v: value, label: "⌃⌥ + " + String(value).toUpperCase() });
  }
  opts.forEach(function (h) {
    var o = el("option"); o.value = h.v; o.textContent = h.label;
    if (h.v === value) o.selected = true;
    s.appendChild(o);
  });
  return s;
}

function renderModes() {
  var wrap = document.getElementById("modes");
  wrap.innerHTML = "";
  CONFIG.modes.forEach(function (m, i) {
    var isText = (m.input === "text");
    var pink = /sum/i.test(m.name || "");
    var card = el("div", "mode-card" + (pink ? " pink" : "") + (isText ? " text-mode" : ""));

    var head = el("div", "mode-head");
    var icon = el("div", "mode-icon", isText ? "✍️" : MODE_EMOJI[i % MODE_EMOJI.length]);
    var meta = el("div", "mode-meta");
    var name = el("input", "mode-name"); name.value = m.name || ""; name.placeholder = "Button name";
    name.addEventListener("input", function () { m.name = name.value; });
    var sub = el("div", "mode-sub", isText ? "Text mode · select, then ⌃⌥ shortcut" : "Voice mode");
    meta.appendChild(name); meta.appendChild(sub);

    var kbd = el("div", "kbd-row");
    var hk = isText ? letterSelect(m.hotkey) : hotkeySelect(m.hotkey);
    hk.addEventListener("change", function () { m.hotkey = hk.value; });
    kbd.appendChild(hk);

    head.appendChild(icon); head.appendChild(meta); head.appendChild(kbd);

    var pwrap = el("div", "prompt-wrap");
    pwrap.appendChild(el("label", "prompt-eyebrow", "Prompt"));
    var ta = el("textarea", "prompt"); ta.value = m.prompt || "";
    ta.addEventListener("input", function () { m.prompt = ta.value; });
    pwrap.appendChild(ta);

    var foot = el("div", "mode-card-foot");
    var rm = el("button", "btn-remove", "Remove");
    rm.addEventListener("click", function () {
      CONFIG.modes.splice(i, 1); renderModes();
    });
    foot.appendChild(rm);

    card.appendChild(head); card.appendChild(pwrap); card.appendChild(foot);
    wrap.appendChild(card);
  });
}

function fillVoice() {
  document.getElementById("api").value =
    (CONFIG.api_key && CONFIG.api_key.indexOf("PASTE_YOUR") === 0) ? "" : (CONFIG.api_key || "");
  var sel = document.getElementById("model");
  sel.innerHTML = "";
  var opts = MODEL_OPTIONS.slice();
  if (CONFIG.model && opts.indexOf(CONFIG.model) === -1) opts.unshift(CONFIG.model);
  opts.forEach(function (mo) {
    var o = el("option"); o.value = mo; o.textContent = mo;
    if (mo === CONFIG.model) o.selected = true;
    sel.appendChild(o);
  });
}

function banner(msg) {
  var b = document.getElementById("banner");
  if (!b) return;
  if (!msg) { b.classList.remove("show"); return; }
  b.textContent = msg; b.classList.add("show");
}

function collectAndValidate() {
  // pull api/model from the voice pane
  CONFIG.api_key = document.getElementById("api").value.trim();
  CONFIG.model = document.getElementById("model").value || "gemini-2.5-flash";
  if (!CONFIG.modes.length) { banner("Add at least one button."); return null; }
  var used = {};
  for (var i = 0; i < CONFIG.modes.length; i++) {
    var m = CONFIG.modes[i];
    if (!m.name || !m.name.trim()) m.name = "Button";
    var isText = (m.input === "text");
    // Voice (arrows) and text (letters) are separate namespaces, so a key only
    // clashes with another button of the same type.
    var key = (isText ? "text:" : "audio:") + m.hotkey;
    if (used[key]) {
      var list = isText ? TEXT_HOTKEYS : HOTKEYS;
      var lbl = list.filter(function (h) { return h.v === m.hotkey; })[0];
      banner("Two " + (isText ? "text" : "voice") + " buttons use " +
        (lbl ? lbl.label : m.hotkey) + ". Each shortcut can only be used once.");
      return null;
    }
    used[key] = true;
  }
  banner("");
  return CONFIG;
}

function doSave(flashId) {
  var cfg = collectAndValidate();
  if (!cfg) return;
  send("save", cfg);
  var f = document.getElementById(flashId);
  if (f) { f.classList.add("show"); setTimeout(function () { f.classList.remove("show"); }, 1800); }
}

/* Python calls these: */
window.yapInit = function (cfg) {
  CONFIG = cfg || CONFIG;
  if (!CONFIG.modes) CONFIG.modes = [];
  if (isSettings()) { renderModes(); fillVoice(); }
};
window.yapSaved = function (ok, msg) { if (!ok && msg) banner(msg); };

function initSettings() {
  // nav
  document.querySelectorAll(".nav-item").forEach(function (item) {
    item.addEventListener("click", function () {
      document.querySelectorAll(".nav-item").forEach(function (n) { n.classList.remove("active"); });
      document.querySelectorAll(".pane").forEach(function (p) { p.classList.remove("active"); });
      item.classList.add("active");
      document.getElementById("pane-" + item.dataset.pane).classList.add("active");
    });
  });
  document.getElementById("add-mode").addEventListener("click", function () {
    var freeHk = (HOTKEYS.filter(function (h) {
      return !CONFIG.modes.some(function (m) { return m.input !== "text" && m.hotkey === h.v; });
    })[0] || HOTKEYS[0]).v;
    CONFIG.modes.push({ name: "New button", input: "audio", hotkey: freeHk, prompt: "" });
    renderModes();
  });
  var addText = document.getElementById("add-text-mode");
  if (addText) addText.addEventListener("click", function () {
    var freeHk = (TEXT_HOTKEYS.filter(function (h) {
      return !CONFIG.modes.some(function (m) { return m.input === "text" && m.hotkey === h.v; });
    })[0] || TEXT_HOTKEYS[0]).v;
    CONFIG.modes.push({ name: "Rewrite", input: "text", hotkey: freeHk, prompt: DEFAULT_REWRITE_PROMPT });
    renderModes();
  });
  document.getElementById("save").addEventListener("click", function () { doSave("saved"); });
  document.getElementById("save2").addEventListener("click", function () { doSave("saved2"); });
  var eye = document.getElementById("eye");
  eye.addEventListener("click", function () {
    var f = document.getElementById("api");
    f.type = (f.type === "password") ? "text" : "password";
  });
  send("ready");
}

/* =========================== ONBOARDING ============================= */

function initOnboarding() {
  var steps = Array.prototype.slice.call(document.querySelectorAll(".step"));
  var dotsWrap = document.getElementById("dots");
  var idx = 0;
  function paintDots() {
    dotsWrap.innerHTML = "";
    steps.forEach(function (_, i) {
      var d = el("span", "dot" + (i === idx ? " on" : ""));
      dotsWrap.appendChild(d);
    });
  }
  var permRequested = false;
  function show(i) {
    idx = Math.max(0, Math.min(steps.length - 1, i));
    steps.forEach(function (s, k) { s.classList.toggle("active", k === idx); });
    paintDots();
    // The first time the user lands on the permissions step, ask Python to
    // report status and walk through whatever prompts are still needed.
    if (!permRequested && steps[idx] && steps[idx].querySelector("#perm-list")) {
      permRequested = true;
      send("permReady");
    }
  }
  document.querySelectorAll("[data-next]").forEach(function (b) {
    b.addEventListener("click", function () { show(idx + 1); });
  });
  document.querySelectorAll("[data-back]").forEach(function (b) {
    b.addEventListener("click", function () { show(idx - 1); });
  });
  document.querySelectorAll("[data-finish]").forEach(function (b) {
    b.addEventListener("click", function () { send("finishOnboarding"); });
  });
  document.querySelectorAll("[data-settings]").forEach(function (b) {
    b.addEventListener("click", function () { send("openSettings"); });
  });
  document.querySelectorAll("[data-getkey]").forEach(function (b) {
    b.addEventListener("click", function () { send("getApiKey"); });
  });
  // Permissions step: per-row fallback buttons (shown only once denied).
  document.querySelectorAll("[data-fix]").forEach(function (b) {
    var row = b.closest(".perm-row");
    b.addEventListener("click", function () {
      send("requestPermission", { which: row.getAttribute("data-perm") });
    });
  });
  document.querySelectorAll("[data-fix-accessibility]").forEach(function (b) {
    b.addEventListener("click", function () { send("openAccessibilitySettings"); });
  });
  show(0);
}

// Python pushes the latest permission status here (see _perm_tick in yap.py).
window.yapPerms = function (state) {
  state = state || {};
  document.querySelectorAll(".perm-row").forEach(function (row) {
    var which = row.getAttribute("data-perm");
    var st = state[which] || "unknown";
    var pill = row.querySelector("[data-pill]");
    var fix = row.querySelector("[data-fix]");  // mic / input-monitoring retry
    row.dataset.status = st;
    if (pill) {
      pill.textContent = st === "granted" ? "✓ Granted"
        : st === "denied" ? "Needs access" : "Waiting…";
    }
    // Show the per-row retry/Settings button only once we've been denied.
    if (fix) fix.hidden = (st !== "denied");
  });
};

/* =========================== QUICK PANEL ============================ */

function initPanel() {
  document.querySelectorAll("[data-settings]").forEach(function (b) {
    b.addEventListener("click", function () { send("openSettings"); });
  });
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () { send("copyLast"); });
  });
  document.querySelectorAll("[data-quit]").forEach(function (b) {
    b.addEventListener("click", function () { send("quit"); });
  });
  send("ready");
}
window.yapPanel = function (state) {
  // state: { mode: 'idle'|'recording'|'working'|'done', modes:[...], lastYap:'' , countdown:'' }
  var live = document.getElementById("live");
  var wave = document.getElementById("wave");
  var status = document.getElementById("status-text");
  var hint = document.getElementById("status-hint");
  var last = document.getElementById("last-yap");
  if (!live) return;
  var m = state.mode || "idle";
  live.dataset.mode = m;
  if (status) {
    status.textContent = m === "recording" ? "Listening…"
      : m === "working" ? "Working on it…"
      : m === "done" ? "Pasted ✓" : "Ready to yap";
  }
  if (hint) {
    hint.textContent = m === "recording" ? "Release Right Option when you’re done"
      : m === "working" ? "Sending to Gemini" : "Hold ⌥ and tap an arrow";
  }
  if (wave) wave.classList.toggle("animate", m === "recording" || m === "working");
  if (state.modes && document.getElementById("rows")) renderPanelRows(state.modes);
  if (last && typeof state.lastYap === "string") {
    if (state.lastYap) {
      last.style.display = "block";
      document.getElementById("last-text").textContent = state.lastYap;
    } else { last.style.display = "none"; }
  }
};

function renderPanelRows(modes) {
  var wrap = document.getElementById("rows");
  wrap.innerHTML = "";
  modes.forEach(function (m, i) {
    var isText = (m.input === "text");
    var keyLabel, sub, icon;
    if (isText) {
      var th = TEXT_HOTKEYS.filter(function (h) { return h.v === m.hotkey; })[0];
      keyLabel = th ? th.label : ("⌃⌥ + " + String(m.hotkey || "").toUpperCase());
      sub = "Select text, press the keys";
      icon = "✍️";
    } else {
      var hk = HOTKEYS.filter(function (h) { return h.v === m.hotkey; })[0];
      keyLabel = hk ? hk.label : m.hotkey;
      sub = "Hold ⌥, tap the key";
      icon = MODE_EMOJI[i % MODE_EMOJI.length];
    }
    var pink = /sum/i.test(m.name || "");
    var row = el("div", "prow");
    row.innerHTML =
      '<div class="prow-icon' + (pink ? " pink" : "") + '">' + icon + '</div>' +
      '<div class="prow-meta"><div class="prow-title">' + escapeHtml(m.name || "Button") + '</div>' +
      '<div class="prow-sub">' + sub + '</div></div>' +
      '<div class="prow-key">' + escapeHtml(keyLabel) + '</div>';
    wrap.appendChild(row);
  });
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}

/* =========================== BOOT =================================== */
document.addEventListener("DOMContentLoaded", function () {
  if (document.getElementById("modes")) initSettings();
  else if (document.getElementById("dots")) initOnboarding();
  else if (document.getElementById("live")) initPanel();
});
