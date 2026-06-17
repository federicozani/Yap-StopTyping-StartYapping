#!/usr/bin/env python3
"""
Parla — a personal voice-to-text menu bar app for macOS.

How it works (the 4 moving parts):
  1. CAPTURE  : pynput listens for a global "hold-to-talk" key. While you
                hold it, we record the microphone.
  2. PROCESS  : on release, we send the recorded audio + the mode's prompt
                to the Gemini API, which transcribes AND formats in one call.
  3. OUTPUT   : the result is copied to the clipboard and pasted (Cmd+V)
                into whatever app you're currently using.
  4. SETTINGS : modes (name + prompt + hotkey) and your API key live in a
                JSON file at ~/.parla/config.json. Edit it from the menu.

Each "mode" is just a saved prompt tied to a hold-to-talk key. Hold a
different key, get different behavior from the same speech.

Read the SETUP.md file next to this one before running.
"""

import base64
import io
import json
import os
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import requests
import rumps
import sounddevice as sd
from pynput import keyboard


def log(*a):
    # Prints to the Terminal so we can see what the app is doing, AND appends to
    # a persistent log file (~/.parla/parla.log) so we can debug the bundled app,
    # whose stderr is invisible when it's double-clicked.
    msg = "[parla] " + " ".join(str(x) for x in a)
    print(msg, file=sys.stderr, flush=True)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # encoding="utf-8" is essential: the bundled app's default text encoding
        # is ASCII, and log lines contain ⌥/🔴/⏳ etc., which would otherwise
        # raise UnicodeEncodeError and silently drop the line.
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass  # logging must never break the app


def log_exc(prefix="ERROR"):
    """Log a full traceback (used in background threads where a bare raise would
    otherwise vanish silently)."""
    import traceback
    log(prefix + "\n" + traceback.format_exc().rstrip())


def notify(title, subtitle, message=""):
    # macOS notifications only work inside a real .app bundle. When running
    # from Terminal they raise, so we try and fall back to a log line.
    import rumps as _rumps
    try:
        _rumps.notification(title, subtitle, message)
    except Exception:
        log(f"{title} — {subtitle} {message}".strip())


# ---------------------------------------------------------------------------
# Config: where settings live, and the defaults we create on first run.
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".parla"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "parla.log"

SAMPLE_RATE = 16000  # 16 kHz mono is plenty for speech and keeps files small.
CHANNELS = 1

# How triggering works:
#   You HOLD the "activator" key (Right Option, ⌥) the whole time you talk.
#   While holding it, you TAP an arrow key to choose the mode and start
#   recording. Release the activator to stop and process.
# So each mode is bound to an arrow: "left", "up", "right", or "down".

DEFAULT_CONFIG = {
    "api_key": "PASTE_YOUR_GEMINI_API_KEY_HERE",
    "model": "gemini-2.5-flash",
    # Bumped whenever the built-in prompts change. On startup, if your config's
    # version is older, the modes are refreshed to these defaults (your API key
    # and model are kept). This means new prompt fixes apply on a simple restart.
    "config_version": 3,
    "modes": [
        {
            "name": "Clean",
            "hotkey": "left",
            "prompt": (
                "Transcribe the audio in the same language it is spoken, then "
                "lightly clean it up: remove filler words (um, uh, like, you "
                "know), false starts, repetitions and self-corrections, and fix "
                "punctuation and capitalization. Keep the meaning and the "
                "speaker's own wording otherwise; do not summarize or rephrase. "
                "Break it into readable paragraphs. Return ONLY the cleaned-up "
                "text, nothing else."
            ),
        },
        {
            "name": "Summary",
            "hotkey": "up",
            "prompt": (
                "You are summarizing MY own spoken notes. I am both the speaker "
                "and the only reader. Follow these rules exactly:\n"
                "- NEVER output a transcript and never repeat my words verbatim. "
                "If I wanted a transcript I would use a different tool.\n"
                "- Write in the first person, in my voice (use 'I'). Never write "
                "'the speaker', 'they', or 'this project'.\n"
                "- Begin with a SHORT summary in plain prose: one or two "
                "sentences that capture the gist. It MUST be shorter than what I "
                "said. Do not re-list every detail here.\n"
                "- Then, ONLY if I mention things to do or changes to make, add a "
                "bullet list of them, each phrased as a clear, direct action. If "
                "some are already done, split the list under 'To do' and 'Done'.\n"
                "- Do NOT add a 'Key points' section, and never list the same "
                "item twice. Pick the single best place for each point.\n"
                "- Keep the whole thing tight and skimmable, in the same "
                "language I spoke.\n"
                "Return only the summary. Only use a different format if I "
                "explicitly ask for one in the recording."
            ),
        },
        {
            "name": "Raw",
            "hotkey": "right",
            "prompt": (
                "Transcribe the following audio word for word in the same "
                "language it is spoken. Return ONLY the transcription, with no "
                "edits and nothing else."
            ),
        },
        {
            "name": "Custom",
            "hotkey": "down",
            "prompt": (
                "Transcribe the following audio and translate the result into "
                "English. Return ONLY the translated text. (This is your custom "
                "slot — edit this prompt to do whatever you want.)"
            ),
        },
    ],
}


def load_config():
    """Read config.json, creating it with defaults the first time."""
    if not CONFIG_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    valid = {"left", "up", "right", "down"}
    needs_save = False
    # If the built-in prompts have been updated (newer version), refresh the
    # modes to the latest defaults, keeping the user's API key and model.
    if cfg.get("config_version") != DEFAULT_CONFIG["config_version"]:
        cfg["modes"] = [dict(m) for m in DEFAULT_CONFIG["modes"]]
        cfg["config_version"] = DEFAULT_CONFIG["config_version"]
        needs_save = True
    # Safety net: fix any invalid/old hotkeys.
    elif not cfg.get("modes") or not all(m.get("hotkey") in valid for m in cfg["modes"]):
        cfg["modes"] = [dict(m) for m in DEFAULT_CONFIG["modes"]]
        needs_save = True
    if needs_save:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    return cfg


# ---------------------------------------------------------------------------
# Gemini: send audio + a prompt, get back finished text.
# ---------------------------------------------------------------------------

def transcribe_with_gemini(wav_bytes: bytes, prompt: str, api_key: str,
                           model: str, retries: int = 5) -> str:
    """Send WAV audio + an instruction to Gemini and return the text result.

    Retries automatically on transient "busy" errors (429/500/503), which
    Google returns when the model is temporarily overloaded.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "audio/wav", "data": audio_b64}},
                ]
            }
        ]
    }
    last_resp = None
    for attempt in range(retries):
        resp = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        if resp.status_code in (429, 500, 503):
            last_resp = resp
            if attempt < retries - 1:  # don't wait after the final attempt
                wait = 2 * (attempt + 1)
                log(f"Gemini busy ({resp.status_code}); retrying in {wait}s "
                    f"(attempt {attempt + 1}/{retries})…")
                time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        # Pull the text out of the first candidate's first part.
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    # Ran out of retries: surface the last busy error.
    if last_resp is not None:
        last_resp.raise_for_status()
    return ""


# ---------------------------------------------------------------------------
# Audio recording: collect mic frames while a key is held.
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self):
        self._frames = []
        self._stream = None

    def start(self):
        self._frames = []

        def callback(indata, frames, time_info, status):
            self._frames.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", callback=callback
        )
        self._stream.start()

    def stop(self) -> bytes:
        """Stop recording and return the audio as in-memory WAV bytes."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._frames:
            return b""
        audio = np.concatenate(self._frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Pasting: copy text to clipboard, then simulate Cmd+V into the focused app.
# ---------------------------------------------------------------------------

_kbd = keyboard.Controller()


def paste_text(text: str):
    # pbcopy puts the text on the macOS clipboard (built in, no extra package).
    subprocess.run("pbcopy", input=text.encode("utf-8"), check=True)
    time.sleep(0.15)  # give the clipboard a moment, and let the hotkey fully release
    with _kbd.pressed(keyboard.Key.cmd):
        _kbd.press("v")
        _kbd.release("v")


# ---------------------------------------------------------------------------
# The menu bar app itself.
# ---------------------------------------------------------------------------

class ParlaApp(rumps.App):
    IDLE_ICON = "🎙️"
    REC_ICON = "🔴"
    BUSY_ICON = "⏳"
    # Stop automatically at 7 minutes so we never blow past Gemini's size limit
    # and lose the recording.
    MAX_RECORD_SECONDS = 7 * 60

    def __init__(self):
        super().__init__(ParlaApp.IDLE_ICON, quit_button=None)
        self.config = load_config()
        self._config_mtime = CONFIG_PATH.stat().st_mtime
        self.recorder = Recorder()
        self.recording = False
        self.option_down = False  # is the activator (Right Option) being held?
        self.active_mode = None
        self._record_start = None  # when the current recording began
        self._pending_title = None  # title requested off-thread; applied in _tick
        self._lock = threading.Lock()  # guards starting/stopping a recording
        # Map each hotkey string ("left", "up", …) to its mode dict.
        self.hotkey_map = {m["hotkey"]: m for m in self.config["modes"]}

        self._build_menu()
        self._start_listener()
        # Ticks 4x/second on the main thread: applies pending icon changes (so
        # the ⏳ busy icon shows promptly) and updates the recording countdown.
        self._timer = rumps.Timer(self._tick, 0.25)
        self._timer.start()

    def _tick(self, _):
        """Runs ~4x/second ON THE MAIN THREAD. Applies any icon/title change
        requested from a background thread, then shows the live countdown."""
        pending = self._pending_title
        if pending is not None:
            self._pending_title = None
            self.title = pending
        if not self.recording or self._record_start is None:
            return
        remaining = ParlaApp.MAX_RECORD_SECONDS - (time.time() - self._record_start)
        if remaining <= 0:
            log("reached 7-minute limit — stopping and processing automatically")
            self._finish_recording()
            return
        m, s = divmod(int(remaining), 60)
        self.title = f"{ParlaApp.REC_ICON} {m}:{s:02d}"

    def _set_title(self, value):
        """Request a menu-bar icon/title change from ANY thread.

        rumps/AppKit only reliably updates the UI from the main thread, and the
        title is changed from the key-listener and _process background threads.
        We simply stash the value here — an atomic attribute assignment that
        NEVER touches AppKit off-thread, so it cannot crash the app — and the
        main-thread _tick timer applies it within ~0.25s. This is what makes the
        ⏳ busy icon reliably appear in the packaged app, without the instability
        of calling into PyObjC from a background thread."""
        self._pending_title = value

    # ----- menu -----------------------------------------------------------

    def _build_menu(self):
        self.menu.clear()
        items = ["Hold Right Option, tap an arrow, speak:", None]  # None = separator
        key_labels = {
            "left": "Right Option + ← Left",
            "up": "Right Option + ↑ Up",
            "right": "Right Option + → Right",
            "down": "Right Option + ↓ Down",
        }
        for m in self.config["modes"]:
            label = key_labels.get(m["hotkey"], m["hotkey"])
            items.append(f"   {label}  →  {m['name']}")
        items += [
            None,
            rumps.MenuItem("Set Gemini API key…", callback=self.set_api_key),
            rumps.MenuItem("Edit prompts & hotkeys…", callback=self.edit_settings),
            rumps.MenuItem("Open config file…", callback=self.open_config_file),
            rumps.MenuItem("Restore default prompts", callback=self.restore_defaults),
            rumps.MenuItem("Reload settings", callback=self.reload_settings),
            None,
            rumps.MenuItem("Quit Parla", callback=rumps.quit_application),
        ]
        self.menu = items

    def set_api_key(self, _):
        """Quick dialog to paste a Gemini API key. Works even in the bundled app."""
        current = self.config.get("api_key", "")
        prefill = "" if current == DEFAULT_CONFIG["api_key"] else current
        window = rumps.Window(
            title="Gemini API Key",
            message="Paste your free Gemini API key (get one at aistudio.google.com):",
            default_text=prefill,
            ok="Save",
            cancel="Cancel",
            dimensions=(360, 24),
        )
        response = window.run()
        if response.clicked:
            key = response.text.strip()
            if key:
                self.config["api_key"] = key
                self._save_config()
                notify("Parla", "API key saved", "")

    def edit_settings(self, _):
        """Open the visual settings window.

        We relaunch THIS program with the PARLA_SETTINGS flag set, instead of
        launching settings_gui.py as a separate script. Inside a bundled .app,
        sys.executable is the app stub (not python3), so the old "run a second
        script" trick was unreliable; relaunching ourselves works in both cases
        (from source and from the bundle). The flagged process shows the
        Tkinter editor (see the __main__ block) instead of the menu bar app.
        """
        env = dict(os.environ, PARLA_SETTINGS="1")
        args = [sys.executable]
        if not getattr(sys, "frozen", False):
            # From source, sys.executable is python3 — tell it which file to run.
            args.append(os.path.abspath(__file__))
        try:
            subprocess.Popen(args, env=env)
        except Exception:
            self.open_config_file(None)

    def open_config_file(self, _):
        # Opens config.json in the default text editor (a reliable fallback).
        os.system(f'open -t "{CONFIG_PATH}"')

    def restore_defaults(self, _):
        """Replace the modes (names, hotkeys, prompts) with the built-in
        defaults, keeping your API key and model. Use this to pull in improved
        default prompts without hand-editing."""
        self.config["modes"] = [dict(m) for m in DEFAULT_CONFIG["modes"]]
        self._save_config()
        notify("Parla", "Default prompts restored", "")

    def reload_settings(self, _):
        self.config = load_config()
        self._config_mtime = CONFIG_PATH.stat().st_mtime
        self.hotkey_map = {m["hotkey"]: m for m in self.config["modes"]}
        self._build_menu()
        notify("Parla", "Settings reloaded", "")

    def _save_config(self):
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2)
        self._config_mtime = CONFIG_PATH.stat().st_mtime
        self.hotkey_map = {m["hotkey"]: m for m in self.config["modes"]}
        self._build_menu()

    def _maybe_reload(self):
        """If the config file changed (e.g. the settings window saved), reload it."""
        try:
            mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            return
        if mtime != self._config_mtime:
            try:
                self.config = load_config()
                self._config_mtime = mtime
                self.hotkey_map = {m["hotkey"]: m for m in self.config["modes"]}
                self._build_menu()
            except Exception:
                pass

    # ----- global hotkey listener ----------------------------------------

    def _start_listener(self):
        # Maps pynput key objects to our hotkey strings.
        # The key you HOLD while talking.
        self._activator = keyboard.Key.alt_r  # Right Option ⌥
        # Tap one of these (while holding the activator) to pick a mode + start.
        self._arrow_names = {
            keyboard.Key.left: "left",
            keyboard.Key.up: "up",
            keyboard.Key.right: "right",
            keyboard.Key.down: "down",
        }
        listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        listener.daemon = True
        listener.start()

    def _on_press(self, key):
        # Show every special key so we can confirm what keys report as.
        if isinstance(key, keyboard.Key):
            log("key pressed:", key)
        # Track the activator (Right Option) being held.
        if key == self._activator:
            self.option_down = True
            return
        # An arrow tapped WHILE the activator is held starts recording.
        if not self.option_down or self.recording:
            return
        arrow = self._arrow_names.get(key)
        if arrow is None:
            return
        self._maybe_reload()  # pick up any settings saved since last time
        mode = self.hotkey_map.get(arrow)
        if mode is None:
            return
        self.recording = True
        self.active_mode = mode
        try:
            self.recorder.start()
            self._record_start = time.time()
            m, s = divmod(ParlaApp.MAX_RECORD_SECONDS, 60)
            self._set_title(f"{ParlaApp.REC_ICON} {m}:{s:02d}")
            log("RECORDING started for mode:", mode["name"],
                "— keep holding Right Option, release it to finish")
        except Exception as e:
            self.recording = False
            self.active_mode = None
            self._set_title(ParlaApp.IDLE_ICON)
            log("ERROR starting microphone:", e)

    def _on_release(self, key):
        # Releasing the activator (Right Option) stops and processes.
        if key != self._activator:
            return
        self.option_down = False
        self._finish_recording()

    def _finish_recording(self):
        """Stop recording and start processing. Safe to call from either the
        key-release handler or the auto-stop timer (whichever happens first)."""
        with self._lock:
            if not self.recording:
                return
            self.recording = False
        self._record_start = None
        wav_bytes = self.recorder.stop()
        log("RECORDING stopped, audio bytes:", len(wav_bytes))
        mode = self.active_mode
        self.active_mode = None
        self._set_title(ParlaApp.BUSY_ICON)
        # Do the network call off the main thread so the UI stays responsive.
        threading.Thread(target=self._process, args=(wav_bytes, mode), daemon=True).start()

    def _process(self, wav_bytes, mode):
        try:
            # Ignore accidental taps (less than ~0.3s of audio).
            if len(wav_bytes) < SAMPLE_RATE * 2 * 0.3:
                log("audio too short, skipping (did the mic record anything?)")
                self._set_title(ParlaApp.IDLE_ICON)
                return
            api_key = self.config.get("api_key", "")
            if not api_key or api_key == DEFAULT_CONFIG["api_key"]:
                log("no API key set")
                notify("Parla", "No API key set",
                       "Open Edit settings and paste your Gemini API key.")
                self._set_title(ParlaApp.IDLE_ICON)
                return
            log("sending to Gemini…")
            text = transcribe_with_gemini(
                wav_bytes, mode["prompt"], api_key, self.config.get("model", "gemini-2.5-flash")
            )
            preview = (text[:80] + "…") if len(text) > 80 else text
            log("Gemini returned:", repr(preview))
            if text:
                paste_text(text)
                log("pasted.")
            else:
                log("Gemini returned empty text.")
        except requests.HTTPError as e:
            body = getattr(e.response, "text", "")[:400]
            log("Gemini HTTP error:", e, body)
            notify("Parla", "Gemini busy, try again", "The model was overloaded.")
        except Exception as e:
            log_exc("ERROR in _process")
            notify("Parla", "Something went wrong", str(e))
        finally:
            self._set_title(ParlaApp.IDLE_ICON)


if __name__ == "__main__":
    # When relaunched in "settings mode" (by edit_settings), show the visual
    # editor and exit instead of starting the menu bar app. This is how the
    # settings window opens both from source and inside the bundled .app.
    if os.environ.get("PARLA_SETTINGS") == "1":
        # Inside the bundle, tkinter needs to be told where the bundled Tcl/Tk
        # script libraries live (they're under Resources/lib/tclX). py2app does
        # this automatically for framework Pythons but not for conda builds.
        if getattr(sys, "frozen", False):
            res = Path(sys.executable).resolve().parent.parent / "Resources" / "lib"
            for var, sub in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
                p = res / sub
                if p.is_dir():
                    os.environ.setdefault(var, str(p))
        import settings_gui
        settings_gui.main()
        sys.exit(0)
    if os.environ.get("PARLA_SELFTEST") == "1":
        log("SELFTEST start. frozen=%r" % getattr(sys, "frozen", False))
        # the exact Gemini request mechanism (dummy key -> expect HTTP 400/403,
        # which proves requests + TLS work; an SSL/conn error would prove they don't)
        try:
            transcribe_with_gemini(b"RIFF....", "hi", "dummy-key", "gemini-2.5-flash", retries=1)
            log("SELFTEST gemini: returned without raising")
        except Exception as e:
            log("SELFTEST gemini raised:", repr(e))
        # paste mechanism (pbcopy + pynput Cmd+V)
        try:
            paste_text("parla-selftest"); log("SELFTEST paste OK")
        except Exception as e:
            log_exc("SELFTEST paste FAIL")
        log("SELFTEST done")
        sys.exit(0)
    log("starting. Hold Right Option (⌥) and speak. Watch these lines while you test.")
    ParlaApp().run()
