# Yap — Handoff & Roadmap

A personal voice-to-text menu bar app for macOS. Hold Right Option, tap an arrow to pick a mode, talk, release. The audio + a per-mode prompt go to Gemini, which transcribes and formats in one call; the result is pasted where the cursor is. Built in Python, runs locally, no server.

> Renamed from **Parla** → **Yap**. Old `~/.parla` settings are migrated to `~/.yap` automatically on first run.

This file is the context for continuing work (e.g. in Claude Code).

## Current state (working)

- Menu bar app (`rumps`). Hold-to-talk via `pynput`. Mic capture via `sounddevice`. Gemini call via `requests`. Paste via clipboard + simulated Cmd+V. **The engine is unchanged from Parla** — only the UI, branding, and packaging changed.
- Triggers: hold **Right Option (⌥)**, tap an arrow to choose the mode and start; release Right Option to stop and process.
  - ← Left = Clean · ↑ Up = Summary · → Right = Raw · ↓ Down = Custom
- **Menu-bar lifecycle icon with 4 states** (drawn by `make_icon.py` from the design tokens):
  idle (monochrome template fish, OS-tinted) → recording (pink chip + live mm:ss countdown in the title) → working (purple chip + bubbles) → done (green check, ~1s) → idle. State is set from background threads only by stashing a name; the main-thread `_tick` timer applies the icon (this thread-safety is what keeps recording from crashing in the bundle — do not regress it).
- Live countdown while recording; auto-stops at 7:00.
- Auto-retries on Gemini "busy" errors (429/500/503), up to 5 attempts.
- Settings auto-reload when the config file changes; prompt versioning via `config_version`.
- **Embedded web-view UI** (WebKit via PyObjC — `webui.py` + `web/`):
  - **Settings** — Buttons & keys (per-mode name/hotkey/prompt, Add, Remove, Save), Voice & model (API key + show/hide, model), About. Saves to the same `~/.yap/config.json` and triggers the normal reload.
  - **Onboarding** — 3-step welcome, shown on first run (and via "Show welcome screen…").
  - **Quick panel** — a borderless **non-activating** popover under the status item (so it never steals key focus → paste keeps working). Shows the buttons, a live "Listening…/Working" status, and the last result with Copy. Auto-shows during recording.
- Menu-bar only (no Dock icon, `LSUIElement`). App icon = squircle brand gradient + fish (`yap.icns`).

## Files

- `yap.py` — the menu bar app (engine + lifecycle states + menu + web-view wiring). *(was `parla.py`)*
- `webui.py` — PyObjC WKWebView window/panel controllers + the JS↔Python bridge.
- `web/` — the UI: `settings.html`, `onboarding.html`, `panel.html`, `app.css`, `tokens.css` (mirrors `design/design-tokens.json`), `app.js`, `fonts/` (bundled Bricolage Grotesque + Hanken Grotesk).
- `assets/` — generated menu-bar state icons + `app-logo.png`.
- `make_icon.py` — generates `assets/*` and `yap.icns` from the design tokens (build-time; needs Pillow).
- `setup_app.py` — py2app config to build `Yap.app`.
- `requirements.txt`, `SETUP.md`, `design/` (the finalized design spec + tokens).
- *(removed)* `settings_gui.py` — the old Tkinter editor; replaced by the web view, so the bundle no longer ships Tcl/Tk.

## Important: where settings live

User settings (API key, model, modes) are at **`~/.yap/config.json`** (log at `~/.yap/yap.log`), NOT in this folder. Created on first run from `DEFAULT_CONFIG` in `yap.py`, or migrated from `~/.parla` if present.

## How to run (from source)

```
pip3 install rumps pynput sounddevice numpy requests pyobjc-framework-WebKit
python3 yap.py
```

Needs three macOS permissions (Microphone, Accessibility, Input Monitoring). See SETUP.md for building `Yap.app`.

## UI architecture notes (for the next session)

- The web view loads local files via `loadFileURL:allowingReadAccessToURL:`; read access is granted to the Resources root so pages can reach `../assets/`.
- JS → Python: `window.webkit.messageHandlers.yap.postMessage({action, payload})`. Python → JS: `evaluateJavaScript_` (helpers `push_js` / `eval_js`).
- PyObjC gotcha: bare single-word method names on the NSObject subclasses (e.g. `push`, `resize`, `hide`, `show`, `close`) collide with existing ObjC selectors and raise `BadPrototypeError`. Keep helper names suffixed (`push_js`, `resize_to`, `hide_now`, `show_window`, `close_window`).
- charset_normalizer must be installed **pure-Python** (`pip install --no-binary charset_normalizer charset_normalizer`) or its mypyc `.so` fails to import in the bundle.

## Backlog (bigger features, separate sessions)

- **Menu-bar animation**: the working-state bubbles and the in-panel waveform animate via CSS in the web view, but the *menu-bar* icon itself is static art (NSStatusItem can't easily animate per-frame). Could swap frames on the tick timer if desired.
- **Display fonts**: bundled from Google Fonts at build time. If a future build can't fetch them, the CSS falls back to the token fallback stacks (Trebuchet/ system).
- **Decorative Settings panes** from the mockup (General toggles, Dictionary, swatch picker) were not built — they had no real setting to back them. Add them when there's a real preference to wire (e.g. launch-at-login, icon style).
- **Multiple LLM providers**, **long audio (>7 min via Files API)** — as before.

## Notes / gotchas

- Paste uses Cmd+V into the focused app; the quick panel is non-activating so it doesn't intercept it.
- Option+Arrow also nudges the cursor (OS text-navigation) in some apps at recording start; acceptable.
- New bundle id `com.yap.app` ⇒ macOS re-asks for the three permissions on Yap's first launch (one-time after the rename).
