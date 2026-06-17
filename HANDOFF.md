# Parla — Handoff & Roadmap

A personal voice-to-text menu bar app for macOS. Hold Right Option, tap an arrow to pick a mode, talk, release. The audio + a per-mode prompt go to Gemini, which transcribes and formats in one call; the result is pasted where the cursor is. Built in Python, runs locally, no server.

This file is the context for continuing work (e.g. in Claude Code).

## Current state (working)

- Menu bar app (`rumps`). Hold-to-talk via `pynput`. Mic capture via `sounddevice`. Gemini call via `requests`. Paste via clipboard + simulated Cmd+V.
- Triggers: hold **Right Option (⌥)**, tap an arrow to choose the mode and start; release Right Option to stop and process.
  - ← Left = Clean (AI-cleaned transcription)
  - ↑ Up = Summary (first-person summary + task list)
  - → Right = Raw (verbatim)
  - ↓ Down = Custom
- Live countdown in the menu bar while recording; auto-stops at 7:00 so we never exceed Gemini's ~8-min inline limit.
- Auto-retries on Gemini "busy" errors (429/500/503), up to 5 attempts.
- Notifications fail safely (they only work inside a real .app bundle).
- Settings auto-reload when the config file changes.
- Prompt versioning: `DEFAULT_CONFIG["config_version"]`. On startup, if the user's config version is older, modes refresh to the built-in defaults (API key + model kept). Bump this number whenever default prompts change so updates apply on restart.

## Files

- `parla.py` — the menu bar app (main program).
- `settings_gui.py` — visual settings editor (Tkinter), launched as a separate process from the menu.
- `setup_app.py` — py2app config to build `Parla.app`.
- `requirements.txt` — dependencies.
- `SETUP.md` — beginner setup + sharing guide.

## Important: where settings live

User settings (API key, model, modes) are stored at **`~/.parla/config.json`**, NOT in this folder. It's created on first run from `DEFAULT_CONFIG` in `parla.py`.

## How to run (from source)

```
pip3 install rumps pynput sounddevice numpy requests
python3 parla.py
```

Needs three macOS permissions for the app (or for Terminal, when run from source): Microphone, Accessibility, Input Monitoring.

## Roadmap (tomorrow's to-do)

All achievable in Python — no Swift required.

1. **Make the settings window work inside the bundled .app.**
   - From source, `settings_gui.py` launches fine as a subprocess. Inside a py2app bundle, `sys.executable` is the app stub, so launching a second script is unreliable. Current behavior: `edit_settings()` detects a frozen bundle and falls back to opening the config file.
   - Options to fix: (a) add a `--settings` entry point to `parla.py` and launch it correctly within the bundle; (b) rebuild the settings window natively with PyObjC/AppKit so it runs in-process with rumps (most robust for bundling, more verbose code); (c) investigate py2app multi-script bundling.

2. **One-click launch with an app icon.**
   - `python3 setup_app.py py2app` already produces `dist/Parla.app` (double-click, no terminal; `LSUIElement` = menu-bar only).
   - Add an icon: create a `.icns` file (design a PNG, convert with `iconutil` or an online converter), then add `"iconfile": "parla.icns"` to the py2app OPTIONS in `setup_app.py`.

3. **Easy sharing.**
   - Share the single `Parla.app`. It's unsigned, so recipients right-click > Open the first time (Gatekeeper). Bundle is ~50-100MB (includes Python).
   - Optional later: Apple Developer ID signing + notarization for friction-free distribution (needs a paid Apple account).

## Backlog (bigger features, separate sessions)

- **Multiple LLM providers**: let the user pick Gemini / OpenAI / Anthropic / etc. and store the matching key. Requires a small provider abstraction around `transcribe_with_gemini`.
- **Custom keys + custom prompts**: more than 4 modes / user-defined trigger combos.
- **Long audio (>7 min)**: switch from inline audio to Gemini's Files API (upload then reference) and raise the request timeout.

## Notes / gotchas

- Debug `log()` lines print to the terminal (harmless; invisible in a bundle).
- Paste uses Cmd+V, so it inserts wherever the cursor is in the focused app.
- Option+Arrow also triggers OS text-navigation in some apps (minor cursor nudge at recording start); acceptable, but a non-arrow trigger could be revisited.
