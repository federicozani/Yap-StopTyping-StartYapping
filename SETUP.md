# Parla — Setup Guide

A personal voice-to-text app that lives in your Mac menu bar. Hold a key, talk, release, and your words appear in whatever app you're using. Different keys give different results (clean transcription, summary with action items, raw text, or your own custom behavior).

This guide assumes you've never done this before. Follow it top to bottom.

---

## What you need

1. A Mac.
2. Python 3 (almost certainly already on your Mac; we check below).
3. A free Gemini API key (you said you have one).

---

## Step 1: Open Terminal

Terminal is the app where you type commands. Press `Cmd + Space`, type `Terminal`, press Enter. A window with a blinking cursor opens. You'll paste commands here and press Enter after each one.

---

## Step 2: Check you have Python 3

Paste this and press Enter:

```
python3 --version
```

If you see something like `Python 3.11.x`, you're good. If it says "command not found", install Python from https://www.python.org/downloads/ (download the macOS installer, run it, then reopen Terminal).

---

## Step 3: Install the app's building blocks

These are the small libraries Parla uses. Paste this one line and press Enter:

```
pip3 install rumps pynput sounddevice numpy requests
```

It'll print a lot of text for a minute. As long as the last lines don't say "ERROR", it worked.

(Tip: if `pip3` says "command not found", try `python3 -m pip install rumps pynput sounddevice numpy requests` instead.)

---

## Step 4: Start the app once (to create the settings file)

In Terminal, paste this and press Enter (keep the quotes):

```
python3 "/Users/federicozaninelli/Claude/Projects/Claude Code Helper/Parla/parla.py"
```

The first time you run it, macOS will pop up permission requests, and a 🎙️ icon should appear in your menu bar (top right of the screen). The app needs three permissions. Grant all three:

### The three permissions (important)

Go to `System Settings` > `Privacy & Security`, and in each section below, make sure **Terminal** is listed and toggled ON. (Later, if you bundle Parla into its own app, you'll toggle "Parla" instead of "Terminal".)

1. **Microphone** — so it can hear you.
2. **Accessibility** — so it can type the result (paste) into other apps.
3. **Input Monitoring** — so the hold-to-talk key works even when another app is in front.

After granting permissions, fully quit the app (click the 🎙️ icon > Quit Parla) and run the command from Step 4 again.

> Why Terminal and not Parla? Right now you're launching the app *through* Terminal, so macOS attributes the permissions to Terminal. Step 8 shows how to turn Parla into its own clickable app with its own permissions.

---

## Step 5: Add your Gemini API key

Click the 🎙️ menu bar icon, then "Set Gemini API key…". A small box appears. Paste your key, click Save. That's it.

> Your key is stored only on your own Mac, in `~/.parla/config.json`. It never leaves your computer except in the API calls to Google.

---

## Step 6: Try it

1. Click into any text field (Notes, a browser search box, Messages, anywhere you can type).
2. **Hold Right Option (⌥)** and say a sentence or two. The icon turns 🔴 while recording.
3. **Release the key.** The icon shows ⏳ while Gemini works, then your cleaned-up text is pasted right where your cursor is.

The four keys and what they do:

| Hold this key       | Mode    | What you get                                            |
|---------------------|---------|---------------------------------------------------------|
| Right Option ⌥      | Clean   | Transcription with filler removed, tidy punctuation     |
| Right Command ⌘     | Summary | A summary with bullet points and an action-items list   |
| Right Shift ⇧       | Raw     | Word-for-word transcription, no edits                   |
| Right Control ⌃     | Custom  | Your own behavior (default: translate to English)       |

---

## Step 7: Make it yours (the settings window)

This is the whole point of Parla: you control how it behaves with plain-English instructions.

Click the icon > "Edit prompts & hotkeys…". A settings window opens where you can, with no code:

- Paste/change your API key and the model.
- Rename any mode.
- Pick which hotkey triggers it (the dropdown of right-side modifier keys).
- Rewrite the prompt for each mode.
- Add a new mode (+ Add mode) or remove one.

Click Save and Parla picks up the changes automatically; no restart needed. Example prompts to try:

- "Transcribe this and rewrite it as a professional email."
- "Transcribe this and turn it into a tweet under 280 characters."
- "Transcribe this in Italian and fix any grammar."

(Prefer editing raw text? "Open config file…" opens the same settings as a JSON file.)

---

## Step 8 (optional): Turn it into a clickable app you can share

Right now you start Parla by typing a command. To make it a normal `.app` you can double-click (and send to friends), use `py2app`. The most reliable way is to build inside a **clean, throwaway environment** that contains only Parla's libraries — this avoids accidentally dragging unrelated packages into the app. Paste these, one block at a time, from the project folder:

```
cd "/Users/federicozaninelli/Downloads/Claude Code Projects/Parla"
python3 -m venv build-env
source build-env/bin/activate
pip install rumps pynput sounddevice numpy requests charset_normalizer py2app
python setup_app.py py2app
deactivate
```

This creates `dist/Parla.app`, with the Parla microphone icon. Double-click it to run. The first launch will ask for the same three permissions (this time grant them to "Parla"). Inside the app, "Edit prompts & hotkeys…" opens the full visual settings window, just like from source.

> You only make the `build-env` folder once; to rebuild later, run `source build-env/bin/activate`, then `python setup_app.py py2app`, then `deactivate`. You can delete the `build`, `dist`, and `build-env` folders any time — they're regenerated.

### Changing the app icon

Parla ships with a simple placeholder icon (`parla.icns`). To use your own:

- **Easiest:** put a square PNG (ideally 1024×1024) named `parla_icon.png` in the project folder, then run `python3 make_icon.py`. It converts your PNG into a proper `parla.icns`.
- **Or:** replace `parla.icns` directly with your own `.icns` file.

Then rebuild (Step 8). Running `python3 make_icon.py` with no `parla_icon.png` present just regenerates the default placeholder.

### What to tell a friend you share `Parla.app` with

1. Move `Parla.app` to Applications. The first time, right-click it > Open > Open (this gets past the "unidentified developer" warning, which appears because the app isn't signed by Apple).
2. Grant the three permissions when macOS asks (Microphone, Accessibility, Input Monitoring), all under System Settings > Privacy & Security.
3. Get a free Gemini API key at https://aistudio.google.com (sign in, "Get API key", copy it).
4. Click the 🎙️ icon > "Set Gemini API key…", paste, Save.
5. Hold Right Option and talk.

> The "unidentified developer" warning is normal for an unsigned app; right-click > Open clears it the first time, and the app is fully self-contained (it carries its own copy of Python, so your friend doesn't need to install anything). Proper Apple signing for friction-free sharing is a separate, more advanced step and isn't set up here.

---

## Troubleshooting

- **Nothing pastes:** Accessibility permission isn't granted (Step 4). Re-check it, then quit and relaunch.
- **Key doesn't trigger recording:** Input Monitoring permission. Same fix.
- **"No API key set" notification:** redo Step 5, and remember to "Reload settings".
- **It pastes but the text is wrong/empty:** check your internet connection and that the API key is valid.
- **To stop the app:** click the menu bar icon > Quit Parla.
- **Settings window won't open** (you see a `tkinter` error in Terminal): your Python is missing the GUI toolkit. If you installed Python with Homebrew, run `brew install python-tk`; or use the installer from python.org, which includes it. The "Set Gemini API key…" and "Open config file…" items work regardless.

---

## How it works under the hood (for your learning)

Parla is about 250 lines of Python in `parla.py`. The four parts:

1. **Capture** (`_on_press` / `_on_release`): `pynput` watches for the hold-to-talk key globally; while held, `sounddevice` records the mic into memory.
2. **Process** (`transcribe_with_gemini`): the recording is sent as audio to Gemini's `generateContent` endpoint together with the mode's prompt. Gemini transcribes and formats in a single call.
3. **Output** (`paste_text`): the result goes to the clipboard, then a simulated `Cmd + V` pastes it where your cursor is.
4. **Settings**: your API key and modes (name, prompt, hotkey) live in `~/.parla/config.json`. The visual window (`settings_gui.py`, built with Tkinter) just reads and writes that file; the menu bar app notices the change and reloads on its own.

Open `parla.py` and `settings_gui.py` and read the comments. They're meant to be understood and tinkered with (and exactly the kind of small changes your `fix-driver` command can iterate on later).
