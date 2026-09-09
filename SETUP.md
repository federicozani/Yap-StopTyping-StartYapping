# Yap — Setup Guide

A personal voice-to-text app that lives in your Mac menu bar. **Hold Right Option (⌥), tap an arrow, talk, release** — and your words appear in whatever app you're using. Different arrows give different results (clean transcription, summary, raw text, or your own custom behavior).

> Yap used to be called **Parla**. If you ran Parla before, your settings (including your saved API key) are migrated automatically the first time you run Yap — see "Your settings move over" below.

This guide assumes you've never done this before. Follow it top to bottom.

---

## What you need

1. A Mac.
2. Python 3 (almost certainly already on your Mac; we check below).
3. A free Gemini API key (get one at https://aistudio.google.com).

---

## Step 1: Open Terminal

Press `Cmd + Space`, type `Terminal`, press Enter. You'll paste commands here and press Enter after each one.

---

## Step 2: Check you have Python 3

```
python3 --version
```

If you see `Python 3.9` or newer, you're good. If it says "command not found", install Python from https://www.python.org/downloads/, then reopen Terminal.

---

## Step 3: Install the app's building blocks

Yap uses a few small libraries. Paste this one line:

```
pip3 install rumps pynput sounddevice numpy requests pyobjc-framework-WebKit pyobjc-framework-AVFoundation
```

It prints a lot of text for a minute. As long as the last lines don't say "ERROR", it worked.

(If `pip3` says "command not found", use `python3 -m pip install …` instead.)

---

## Step 4: Start the app once

From the project folder, run:

```
python3 "/Users/federicozaninelli/Downloads/Claude Code Projects/Parla/yap.py"
```

A little fish icon appears in your menu bar (top-right). On the very first run a **Welcome** window also opens — that's the onboarding. macOS will ask for permissions; grant all three:

### The three permissions (important)

`System Settings` → `Privacy & Security`, and in each section make sure **Terminal** is ON (later, when you build the app, you'll toggle **Yap** instead):

1. **Microphone** — so it can hear you.
2. **Accessibility** — so it can paste the result into other apps.
3. **Input Monitoring** — so the hold-to-talk key works while another app is in front.

After granting them, quit (fish icon → Quit Yap) and run Step 4 again.

---

## Step 5: Add your Gemini API key

Click the fish icon → **Settings…**. The Settings window opens. Go to **Voice & model**, paste your key into **Gemini API key** (use the 👁 to reveal it), pick a model, click **Save**.

(Quick alternative: fish icon → "Set Gemini API key…" for a one-field box.)

> Your key is stored only on your Mac, in `~/.yap/config.json`. It never leaves your computer except in the API calls to Google.

---

## Step 6: Try it

1. Click into any text field (Notes, a search box, Messages…).
2. **Hold Right Option (⌥)** and **tap an arrow** to pick a mode — the menu-bar fish turns into a pink "recording" chip with a live countdown.
3. **Keep holding ⌥ and talk.** **Release ⌥** to finish. The fish shows a purple "working" chip while Gemini works, a brief green check when done, then your text is pasted where your cursor is.

The four default arrows:

| Hold ⌥ and tap | Mode    | What you get                                          |
|----------------|---------|-------------------------------------------------------|
| ← Left         | Clean   | Transcription with filler removed, tidy punctuation   |
| ↑ Up           | Summary | A summary with bullet points and action items         |
| → Right        | Raw     | Word-for-word transcription, no edits                 |
| ↓ Down         | Custom  | Your own behavior (default: translate to English)     |

Recording auto-stops at **7 minutes** so it never exceeds Gemini's audio limit.

---

## Step 7: Make it yours (Settings)

Click the fish icon → **Settings…**:

- **Buttons & keys** — rename a button, pick its arrow, rewrite its prompt, **Add a button**, **Remove**, **Save**.
- **Voice & model** — your API key and model.

Click **Save** and Yap picks up the changes automatically; no restart needed. Example prompts to try:

- "Transcribe this and rewrite it as a professional email."
- "Transcribe this and turn it into a tweet under 280 characters."
- "Transcribe this in Italian and fix any grammar."

There's also a **Quick panel** (fish icon → Quick panel) — a small popover showing your buttons, the live "Listening…" status while you record, and your last result with a Copy button. It also pops up on its own while you're recording.

(Prefer raw text? "Open config file…" opens the same settings as JSON.)

---

## Step 8 (optional): Turn it into a clickable app you can share

To make a normal `.app` you can double-click (and send to friends), use `py2app`. Build inside a **clean, throwaway environment** that contains only Yap's libraries:

```
cd "/Users/federicozaninelli/Downloads/Claude Code Projects/Parla"
python3 -m venv build-env
source build-env/bin/activate
pip install rumps pynput sounddevice numpy requests pyobjc-framework-WebKit pyobjc-framework-AVFoundation py2app Pillow
pip install --no-binary charset_normalizer charset_normalizer
python setup_app.py py2app
deactivate
```

This creates `dist/Yap.app` with the Yap fish icon. Double-click it to run.

Notes:
- `pyobjc-framework-WebKit` powers the Settings / onboarding / quick-panel windows (a native web view — WebKit ships with macOS).
- `pyobjc-framework-AVFoundation` is used by onboarding to detect and prompt for the Microphone permission (Input Monitoring + Accessibility use Quartz + ApplicationServices, which come with WebKit's dependencies).
- `Pillow` is only used by `make_icon.py` to generate the icons; the running app never needs it.
- The `--no-binary charset_normalizer` line installs the pure-Python build of that library so it bundles cleanly (the default has a compiled helper py2app can't find).
- **First launch asks for the three permissions again** — Yap now has its own identity (`com.yap.app`), so macOS treats it as a new app and re-asks for Microphone / Accessibility / Input Monitoring. Grant them to **Yap**.

> You only make `build-env` once. To rebuild later: `source build-env/bin/activate`, `python setup_app.py py2app`, `deactivate`. You can delete `build`, `dist`, and `build-env` any time — they're regenerated.

### Changing the artwork

All of Yap's artwork is generated from the design tokens by `make_icon.py`:

```
python3 make_icon.py
```

This (re)creates the four menu-bar state icons in `assets/`, the in-app logo, and the app icon `yap.icns`. Edit the colors/geometry at the top of `make_icon.py` to tweak them, then rebuild (Step 8).

### What to tell a friend you share `Yap.app` with

1. Move `Yap.app` to Applications. First time: **right-click → Open → Open**. Double-clicking will be blocked — the app is signed only with your local certificate, which their Mac doesn't trust. If macOS insists the app "is damaged", the download flag is the cause: `xattr -dr com.apple.quarantine /Applications/Yap.app`.
2. Grant the three permissions when asked (all under System Settings → Privacy & Security). If a prompt is missed, everything still looks fine but nothing works — Settings → **Test & diagnostics** has a test and a step-by-step fix for each one.
3. Get a free Gemini API key at https://aistudio.google.com.
4. Click the fish icon → Settings… → **Provider & model**, paste the key, Save.
5. Hold Right Option, tap an arrow, talk.

> The app is fully self-contained (it carries its own copy of Python), so your friend doesn't need to install anything. Removing the right-click-to-open step for other people needs an Apple Developer ID and notarization, which is not set up here.

---

## Your settings move over (from Parla)

The first time Yap runs, if it finds an old `~/.parla/config.json` but no `~/.yap/config.json` yet, it **copies your old settings over** — API key, model, and your modes — and copies the old log too. Your old `~/.parla` folder is left untouched. So upgrading from Parla loses nothing.

---

## Troubleshooting

- **Nothing pastes:** Accessibility permission isn't granted. Re-check it, then quit and relaunch.
- **Key doesn't trigger recording:** Input Monitoring permission. Same fix. The log shows `This process is not trusted!` when this is the cause.
- **After rebuilding the app, the hotkey stops working:** a freshly built `Yap.app` is a new unsigned binary, so macOS may distrust the permissions you granted the previous build. Fix: System Settings → Privacy & Security → **Input Monitoring** (and **Accessibility**), **remove** the old "Yap" entry with "–", then quit and relaunch Yap and grant it again. The log at `~/.yap/yap.log` confirms it — if pressing Right Option logs no "key pressed" lines, it's this.
- **Health-check a built app without speaking:** run the bundled binary with `YAP_SELFTEST=1`:
  `YAP_SELFTEST=1 "…/Yap.app/Contents/MacOS/Yap"` — it logs whether the Gemini request and the paste both work (a `400` from Gemini is expected and fine; it proves the network/TLS path works).
- **"No API key set" notification:** redo Step 5.
- **It pastes but the text is wrong/empty:** check your internet connection and that the API key is valid.
- **Settings window won't open:** the log at `~/.yap/yap.log` records the reason; "Set Gemini API key…" and "Open config file…" always work as a fallback.
- **To stop the app:** fish icon → Quit Yap.

---

## How it works under the hood (for your learning)

Yap is one Python file, `yap.py`. The four parts:

1. **Capture** (`_on_press` / `_on_release`): `pynput` watches for Right Option held + an arrow tapped; while held, `sounddevice` records the mic into memory.
2. **Process** (`transcribe_with_gemini`): the recording is sent as audio to Gemini's `generateContent` endpoint with the mode's prompt; Gemini transcribes and formats in one call (with automatic retries on "busy" errors).
3. **Output** (`paste_text`): the result goes to the clipboard, then a simulated `Cmd + V` pastes it where your cursor is.
4. **Settings & UI**: your API key and modes live in `~/.yap/config.json`. The menu-bar layer is `rumps`; the Settings, onboarding, and quick-panel windows are an embedded **WebKit web view** (`webui.py` + the HTML/CSS/JS in `web/`, styled from `design/design-tokens.json`). The menu-bar fish has four lifecycle states (idle / recording / working / done) drawn by `make_icon.py`.

Read the comments in `yap.py` and `webui.py` — they're meant to be tinkered with.
