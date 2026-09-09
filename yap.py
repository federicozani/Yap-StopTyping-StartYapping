#!/usr/bin/env python3
"""
Yap — a personal voice-to-text menu bar app for macOS.

How it works (the 4 moving parts):
  1. CAPTURE  : pynput listens for a global "hold-to-talk" key. While you
                hold it, we record the microphone.
  2. PROCESS  : on release, we send the recorded audio + the mode's prompt
                to the Gemini API, which transcribes AND formats in one call.
  3. OUTPUT   : the result is copied to the clipboard and pasted (Cmd+V)
                into whatever app you're currently using.
  4. SETTINGS : modes (name + prompt + hotkey) and your API key live in a
                JSON file at ~/.yap/config.json. Edit it from the menu.

Each "mode" is just a saved prompt tied to a hold-to-talk key. Hold a
different key, get different behavior from the same speech.

Read the SETUP.md file next to this one before running.
"""

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

try:
    import numpy as np
except ImportError as _numpy_err:
    # The bundled numpy and _cffi_backend are compiled for THIS Mac's CPU. On a
    # Mac with a different architecture the import dies deep inside numpy and
    # py2app shows only "Launch error", which tells the user nothing. Say what
    # actually happened, in a dialog they can read, before giving up.
    import platform
    import subprocess as _sp
    _msg = ("Yap can't run on this Mac.\n\n"
            "This copy was built for a different processor "
            "(it needs an Apple Silicon Mac — M1 or newer).\n\n"
            "Ask for a build made for your Mac.")
    try:
        _sp.run(["osascript", "-e",
                 'display alert "Yap can\u2019t start" message "%s" as critical giving up after 120'
                 % _msg.replace('"', '\\"').replace("\n", "\\n")], check=False)
    except Exception:
        pass
    print("[yap] FATAL: numpy failed to import on %s (%s): %s"
          % (platform.machine(), platform.platform(), _numpy_err), file=sys.stderr)
    raise SystemExit(1)

import requests
import rumps
import sounddevice as sd
from pynput import keyboard

# Quartz powers two macOS-only tricks: (1) sending a clean Cmd+C/Cmd+V whose
# modifier flags are set explicitly, so the Control+Option chord the user is
# still holding can't corrupt it; (2) suppressing the text-rewrite chord key so
# it never types into the focused app. Guarded so the app still runs (minus those
# tricks) if Quartz is somehow unavailable.
try:
    import Quartz
except Exception:
    Quartz = None

# certifi ships the CA bundle that HTTPS verification needs. See ca_bundle()
# below for why we copy it out of certifi's control at startup.
try:
    import certifi
except Exception:
    certifi = None

# NSWorkspace lets us hear "the Mac just woke up" so the first recording after
# a sleep works without a relaunch. Guarded like Quartz: no PyObjC, no observer,
# app still runs.
try:
    from AppKit import NSWorkspace, NSPasteboard, NSPasteboardTypeString
    from Foundation import NSObject
except Exception:
    NSWorkspace = None
    NSObject = None
    NSPasteboard = None
    NSPasteboardTypeString = None


def log(*a):
    # Prints to the Terminal so we can see what the app is doing, AND appends to
    # a persistent log file (~/.yap/yap.log) so we can debug the bundled app,
    # whose stderr is invisible when it's double-clicked.
    msg = "[yap] " + " ".join(str(x) for x in a)
    print(msg, file=sys.stderr, flush=True)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_big()
        # encoding="utf-8" is essential: the bundled app's default text encoding
        # is ASCII, and log lines contain ⌥/🔴/⏳ etc., which would otherwise
        # raise UnicodeEncodeError and silently drop the line.
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass  # logging must never break the app


def _rotate_log_if_big():
    """Keep yap.log small enough to send. Once it passes LOG_MAX_BYTES it
    becomes yap.log.1 (replacing any previous one) and a fresh log starts.
    Two files means the history around a problem survives one rotation."""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            prev = LOG_PATH.with_suffix(".log.1")
            try:
                if prev.exists():
                    prev.unlink()
            except Exception:
                pass
            LOG_PATH.rename(prev)
    except Exception:
        pass  # rotation must never break logging


def log_environment():
    """Record WHICH copy of Yap is running, and where from.

    When Yap misbehaves on another Mac the log is all we get, and the usual
    culprits are invisible without this: an old copy launching instead of the
    new one, a quarantined app macOS is running from a randomised read-only
    path (App Translocation — permissions can never stick to it), or a
    processor mismatch. One line each, so the log answers those first."""
    try:
        import platform
        exe = sys.executable or ""
        bundle = ""
        if getattr(sys, "frozen", False):
            # …/Yap.app/Contents/MacOS/Yap  ->  …/Yap.app
            bundle = os.path.abspath(os.path.join(os.path.dirname(exe), "..", ".."))
        log("Yap %s (%s) — python %s on macOS %s / %s"
            % (APP_VERSION, APP_CODENAME, platform.python_version(),
               platform.mac_ver()[0] or "?", platform.machine()))
        log("running from: %s" % (bundle or exe or "source"))
        if bundle and "/AppTranslocation/" in bundle:
            log("WARNING: macOS is running Yap from a randomised read-only copy "
                "(App Translocation). Permissions can NEVER stick like this — "
                "move Yap.app to Applications in Finder and remove the download "
                "flag: xattr -dr com.apple.quarantine /Applications/Yap.app")
        if bundle:
            try:
                q = subprocess.run(["xattr", "-p", "com.apple.quarantine", bundle],
                                   capture_output=True, text=True)
                if q.returncode == 0 and q.stdout.strip():
                    log("WARNING: this copy is still marked as downloaded "
                        "(quarantine=%s). Run: xattr -dr com.apple.quarantine %s"
                        % (q.stdout.strip(), bundle))
            except Exception:
                pass
    except Exception:
        log_exc("could not log the environment")


def _bundle_path():
    """Path to the running .app, or "" when running from source."""
    if not getattr(sys, "frozen", False):
        return ""
    exe = sys.executable or ""
    return os.path.abspath(os.path.join(os.path.dirname(exe), "..", ".."))


def startup_repair():
    """Fix, at launch, the states that silently stop Yap working — the ones a
    user has no way to guess at.

    Only the safe half of a reset can be automated. Yap CAN strip its own
    download flag and throw away its own stale web-view cache. It deliberately
    does NOT touch your settings or your API key (an update must never cost you
    those), and it CANNOT reset its own macOS permissions: Accessibility and
    Input Monitoring live in a system database that needs an administrator
    password, and an app that asks for your password at launch is exactly what
    you should refuse. That part stays in "Reset Yap completely…" in the menu."""
    bundle = _bundle_path()
    if not bundle:
        return

    # 1. Running from a randomised read-only copy: nothing granted can stick.
    #    Yap can't move itself out of there, so say so, loudly and precisely.
    if "/AppTranslocation/" in bundle:
        msg = ("macOS is running Yap from a temporary read-only copy, because "
               "the app is still marked as downloaded. Permissions you grant "
               "can never stick while this is true.\n\n"
               "Fix it: quit Yap, drag Yap.app to your Applications folder in "
               "Finder, then open it from there.")
        log("TRANSLOCATED — " + msg.replace("\n", " "))
        _alert("Yap needs to be moved", msg)
        return

    # 2. The download flag itself. Left on, it re-triggers the above on the next
    #    launch. Yap owns this file, so it can simply clear it.
    try:
        q = subprocess.run(["xattr", "-p", "com.apple.quarantine", bundle],
                           capture_output=True, text=True)
        if q.returncode == 0 and q.stdout.strip():
            r = subprocess.run(["xattr", "-dr", "com.apple.quarantine", bundle],
                               capture_output=True, text=True)
            if r.returncode == 0:
                log("removed the download flag from %s (it blocks Gatekeeper "
                    "and breaks permissions)" % bundle)
            else:
                log("could not remove the download flag: %s"
                    % (r.stderr or "").strip()[:120])
    except Exception:
        log_exc("quarantine check failed")


def clear_web_cache():
    """Throw away the Settings web view's cache.

    WKWebView caches the HTML/CSS/JS behind Settings, so after an update you can
    be looking at the OLD page in a NEW app. Cheap to rebuild, so this runs
    whenever the version changes."""
    for d in ("~/Library/Caches/com.yap.app", "~/Library/WebKit/com.yap.app"):
        path = Path(d).expanduser()
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                log("cleared stale web cache: %s" % path)
        except Exception:
            log_exc("could not clear %s" % path)


def _alert(title, message):
    """A dialog that never blocks startup and never gets stuck on screen."""
    try:
        subprocess.Popen(
            ["osascript", "-e",
             'display alert "%s" message "%s" giving up after 120'
             % (title.replace('"', '\\"'),
                message.replace('\\', '\\\\').replace('"', '\\"').replace("\n", "\\n"))])
    except Exception:
        log_exc("could not show alert")


def another_copy_is_running():
    """True when a DIFFERENT copy of Yap.app is already running.

    macOS stops you launching the same bundle twice, but two copies in
    different folders (the old one in Applications, a new one in Downloads)
    happily run at once — and then both listen for the same shortcut while the
    permissions are granted to only one of them."""
    if not getattr(sys, "frozen", False):
        return False  # running from source alongside the app is intentional
    try:
        from AppKit import NSRunningApplication, NSBundle
        me = NSBundle.mainBundle().bundleIdentifier()
        if not me:
            return False
        others = NSRunningApplication.runningApplicationsWithBundleIdentifier_(me)
        mine = os.getpid()
        for app in others or []:
            if app.processIdentifier() != mine:
                try:
                    log("other copy: pid %s at %s"
                        % (app.processIdentifier(),
                           app.bundleURL().path() if app.bundleURL() else "?"))
                except Exception:
                    pass
                return True
    except Exception:
        log_exc("could not check for another running copy")
    return False


def log_exc(prefix="ERROR"):
    """Log a full traceback (used in background threads where a bare raise would
    otherwise vanish silently)."""
    import traceback
    log(prefix + "\n" + traceback.format_exc().rstrip())


# Kept alive for the life of the process: faulthandler writes into this file
# descriptor from a signal handler, so it must never be garbage collected.
_crash_file = None


def install_crash_handlers():
    """Make sure a crash always leaves something behind to read.

    Yap had three blind spots. A crash inside native code (CoreAudio, PortAudio,
    WebKit) killed the process without unwinding any Python, so yap.log ended
    mid-sentence and said nothing. An unhandled exception on the main thread
    printed to a stderr nobody sees in a double-clicked .app. And an exception in
    a worker thread vanished entirely. After this runs, all three land on disk.

    Every part is guarded: a diagnostic aid must never be the thing that stops
    the app starting."""
    global _crash_file
    try:
        import faulthandler
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Line-buffered and never closed — faulthandler writes to this fd from a
        # signal handler during the crash itself.
        _crash_file = open(CRASH_LOG_PATH, "a", buffering=1)
        _crash_file.write("\n===== Yap %s started %s =====\n"
                          % (APP_VERSION, time.strftime("%Y-%m-%d %H:%M:%S")))
        faulthandler.enable(file=_crash_file, all_threads=True)
    except Exception:
        pass

    def _hook(exc_type, exc_value, exc_tb):
        try:
            import traceback
            log("UNHANDLED EXCEPTION on the main thread\n"
                + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip())
        except Exception:
            pass

    try:
        sys.excepthook = _hook
    except Exception:
        pass

    # threading.excepthook is Python 3.8+; guarded anyway so an older or odd
    # interpreter can't turn this into a launch failure.
    try:
        def _thread_hook(args):
            try:
                import traceback
                log("UNHANDLED EXCEPTION in thread %s\n"
                    % getattr(args.thread, "name", "?")
                    + "".join(traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback)).rstrip())
            except Exception:
                pass
        threading.excepthook = _thread_hook
    except Exception:
        pass


# Anything that looks like an API key/token. Used to scrub provider error
# bodies before they reach the log (e.g. OpenAI 401s echo part of the key).
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|AIza[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]{6,})")


def _redact(s):
    """Mask key/token-looking substrings so secrets never land in the log file."""
    try:
        return _SECRET_RE.sub("[redacted]", str(s))
    except Exception:
        return s


def notify(title, subtitle, message=""):
    # macOS notifications only work inside a real .app bundle. When running
    # from Terminal they raise, so we try and fall back to a log line.
    import rumps as _rumps
    try:
        _rumps.notification(title, subtitle, message)
    except Exception:
        log(f"{title} — {subtitle} {message}".strip())


# ---------------------------------------------------------------------------
# Version. THE single source of truth: the Settings sidebar and About pane are
# filled from here at runtime, and setup_app.py reads this line to stamp
# CFBundleShortVersionString into the built .app, so Finder's "Get Info" and the
# app itself can never disagree. Bump it here and nowhere else.
# ---------------------------------------------------------------------------

APP_VERSION = "3.2"
APP_CODENAME = "Herring"

# ---------------------------------------------------------------------------
# Config: where settings live, and the defaults we create on first run.
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".yap"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "yap.log"

# Where faulthandler writes when the app dies inside native code (CoreAudio,
# WebKit, PortAudio). A hard crash like that unwinds no Python, so nothing
# reaches yap.log — this file is the only trace left behind. See
# install_crash_handlers().
CRASH_LOG_PATH = CONFIG_DIR / "yap-crash.log"

# yap.log is append-only and had grown past 3 MB / 68k lines in normal use,
# which is more than anyone can send when asking for help. Rotate it at 2 MB,
# keeping one previous file, so the log stays complete enough to diagnose from
# and small enough to attach to a message.
LOG_MAX_BYTES = 2 * 1024 * 1024

# Safety net for recordings: every clip is written here BEFORE it is sent, and
# deleted the moment the model answers. Only clips whose upload failed outright
# survive, so nothing is ever silently dropped. This lives under ~/.yap (private
# to this user, mode 0700) rather than the system temp dir precisely BECAUSE
# macOS periodically purges the temp dir — the same purge that breaks the CA
# bundle (see ca_bundle()). Leftovers are swept at startup after 24 hours.
RECORDINGS_DIR = CONFIG_DIR / "recordings"
RECORDING_KEEP_SECONDS = 24 * 60 * 60

# The CA bundle Yap verifies HTTPS against, copied to a stable path (see
# ca_bundle()).
CA_BUNDLE_PATH = CONFIG_DIR / "cacert.pem"

# The app used to be called "Parla". On first run we migrate the old settings
# (including the saved API key) and log from ~/.parla so nothing is lost.
OLD_CONFIG_DIR = Path.home() / ".parla"
OLD_CONFIG_PATH = OLD_CONFIG_DIR / "config.json"
OLD_LOG_PATH = OLD_CONFIG_DIR / "parla.log"

# Where onboarding sends users to create an API key — the key page itself for
# each provider (not the home page), so the "create key" button is right there.
AISTUDIO_URL = "https://aistudio.google.com/apikey"
OPENAI_KEY_URL = "https://platform.openai.com/api-keys"


def resource_path(*parts):
    """Absolute path to a bundled resource (icons, web UI), working both from
    source and inside the py2app bundle. In the .app, data_files land in
    Contents/Resources; from source they sit next to this script."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent.parent / "Resources"
    else:
        base = Path(__file__).resolve().parent
    return str(base.joinpath(*parts))


# The menu-bar lifecycle icons (generated by make_icon.py). Each entry is
# (image path, is_template). Idle is a monochrome template glyph so macOS tints
# it for light/dark bars; the active states are full-color gradient tiles.
ICON_STATES = {
    "idle": (resource_path("assets", "menubar-idle.png"), True),
    "recording": (resource_path("assets", "menubar-recording.png"), False),
    "working": (resource_path("assets", "menubar-working.png"), False),
    "done": (resource_path("assets", "menubar-done.png"), False),
}

SAMPLE_RATE = 16000  # 16 kHz mono is plenty for speech and keeps files small.
CHANNELS = 1

# How triggering works:
#   VOICE modes — you HOLD the "activator" key (Right Option, ⌥) the whole time
#   you talk. While holding it, you TAP an arrow key to choose the mode and start
#   recording. Release the activator to stop and process. So each voice mode is
#   bound to an arrow: "left", "up", "right", or "down".
#
#   TEXT modes (e.g. "Rewrite") — there's no recording. You select text in any
#   app, then tap an arrow while holding Right Command + Right Option (⌘⌥). Yap
#   copies the selection, sends it to Gemini with the mode's prompt, and pastes
#   the result back over the selection. Same arrow layout as voice; the extra
#   Command is what turns "speak" into "rewrite the selection".

# Valid hotkeys for each input type. BOTH voice and text modes are bound to the
# four arrows now. They're told apart by Command: voice is "Right Option + arrow"
# (hold ⌥, tap arrow, speak); text rewrite is "Right Command + Right Option +
# arrow" (hold ⌘⌥, tap arrow, rewrite the selection in place).
ARROW_HOTKEYS = {"left", "up", "right", "down"}

# Log every key the listener sees. Off by default: it is the single noisiest
# line in the app and made yap.log unusable for diagnosing anything else.
DEBUG_KEYS = os.environ.get("YAP_DEBUG_KEYS") == "1"

# Arrow hotkey name → macOS virtual-key code (Carbon HIToolbox kVK_*Arrow). The
# suppressor tap matches the rewrite chord by the arrow's vk + the modifier flags.
ARROW_VK = {"left": 123, "right": 124, "down": 125, "up": 126}

# Device-dependent modifier bits inside a CGEvent's flags (IOKit IOLLEvent.h).
# CGEventGetFlags reports which SIDE a modifier is on; we require the RIGHT-side
# Command and Option so (a) it matches the intended gesture and (b) the common
# left-hand ⌘⌥← ("previous tab" in browsers) keeps working untouched.
NX_DEVICERCMDKEYMASK = 0x000010  # right Command
NX_DEVICERALTKEYMASK = 0x000040  # right Option

# Text-mode key → macOS virtual-key code (Carbon HIToolbox <Events.h>, kVK_ANSI_*).
# We match the text-mode chord by the key's *virtual-key code*, NOT the character
# it produces: with Control/Option held, macOS reports odd characters (⌥/ → "÷",
# ⌥R → "®"), but the vk of a physical key is stable regardless of modifiers. This
# is exactly why "/" works as a chord key — keeping Control in the combo means the
# OS never types "÷", and we match the slash by its vk (44) either way.
LETTER_VK = {
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4,
    "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31,
    "p": 35, "q": 12, "r": 15, "s": 1, "t": 17, "u": 32, "v": 9,
    "w": 13, "x": 7, "y": 16, "z": 6,
    # Non-letter keys (so text-mode hotkeys aren't limited to letters).
    "/": 44, ";": 41, "'": 39, ",": 43, ".": 47,
    "[": 33, "]": 30, "-": 27, "=": 24,
}
VK_TO_LETTER = {vk: ltr for ltr, vk in LETTER_VK.items()}
TEXT_HOTKEYS = set(LETTER_VK)

# Arrow virtual-key code → hotkey name, for our own event tap (see QuartzKeyTap).
VK_TO_ARROW = {vk: name for name, vk in ARROW_VK.items()}

# The modifier keys we track, by virtual-key code (Carbon kVK_*), each with the
# device-dependent flag bit that says whether THAT key is down (IOKit
# IOLLEvent.h). macOS reports a modifier as a "flags changed" event rather than
# a key up/down, so the only way to tell press from release is to look at
# whether the key's own bit survived the change.
MODIFIER_VK = {
    55: ("cmd_l", 0x000008),
    54: ("cmd_r", 0x000010),
    58: ("alt_l", 0x000020),
    61: ("alt_r", 0x000040),
    59: ("ctrl_l", 0x000001),
    62: ("ctrl_r", 0x002000),
}


class QuartzKeyTap:
    """The hold-to-talk listener, as an event tap Yap owns outright.

    Why not pynput, which is already a dependency? Because macOS switches off any
    event tap whose callback takes too long, and it does so silently: the tap
    stops delivering, the thread stays alive, nothing raises and nothing is
    logged. pynput enables its tap once at startup and has no code path that
    re-enables it, so the hotkey stayed dead until Yap was quit and reopened.
    Owning the tap is the only way to notice that and fix it — macOS reports the
    shutdown as a kCGEventTapDisabledBy* event, and re-enabling is a single call.

    The tap is LISTEN-ONLY on purpose. It cannot alter or swallow keystrokes, so
    it needs Input Monitoring but never Accessibility — preserving the split that
    lets voice keep working when only Accessibility is missing. The separate
    suppressor tap (_start_suppressor) is the one that needs to modify events.

    Keys are handed to the same on_press/on_release callbacks pynput used, as the
    same pynput Key objects, so everything downstream is unchanged and the pynput
    path below stays a working fallback."""

    def __init__(self, on_press, on_release, quiet=False):
        self._on_press = on_press
        self._on_release = on_release
        # quiet=True for the retry loop: it runs every few seconds while the
        # permission is missing, and one "not granted" line per attempt would
        # bury the log in exactly the situation you need to read it.
        self._quiet = quiet
        self._tap = None
        self._loop = None
        self._thread = None
        self._running = False
        self._ready = threading.Event()
        self._reenables = 0

    # ---- lifecycle -------------------------------------------------------

    @staticmethod
    def input_monitoring_granted():
        """Is Input Monitoring actually granted?

        This has to be asked separately, because CGEventTapCreate SUCCEEDS
        without the permission — it hands back a tap that is simply never sent
        any events. So "the tap was created" says nothing about whether the
        hotkey works, and treating it as success is how you end up reporting a
        working listener to someone whose hotkey does nothing."""
        try:
            from Quartz import CGPreflightListenEventAccess
            return bool(CGPreflightListenEventAccess())
        except Exception:
            try:
                import permissions
                return permissions.input_monitoring_status() == "granted"
            except Exception:
                return False

    def start(self, timeout=5.0):
        """Create the tap on its own run-loop thread. Returns True if it is live.

        False means macOS refused to create the tap outright. Note that True
        does NOT mean the hotkey works — see input_monitoring_granted()."""
        if Quartz is None:
            return False
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="yap-keytap")
        self._thread.start()
        self._ready.wait(timeout)
        return self._tap is not None

    def stop(self):
        self._running = False
        try:
            if self._loop is not None:
                Quartz.CFRunLoopStop(self._loop)
        except Exception:
            pass

    def _run(self):
        try:
            mask = ((1 << Quartz.kCGEventKeyDown)
                    | (1 << Quartz.kCGEventKeyUp)
                    | (1 << Quartz.kCGEventFlagsChanged))
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                mask,
                self._callback,
                None,
            )
            if tap is None:
                # Almost always Input Monitoring. Say so rather than leaving a
                # bare "tap is None" for someone to decode later.
                if not self._quiet:
                    log("could not create the keyboard event tap — Input "
                        "Monitoring is not granted. System Settings → Privacy & "
                        "Security → Input Monitoring, switch Yap on.")
                self._ready.set()
                return
            self._tap = tap
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(self._loop, source,
                                      Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            self._ready.set()
            while self._running:
                Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 1.0, False)
        except Exception:
            log_exc("the keyboard event tap thread died")
        finally:
            self._ready.set()
            self._loop = None

    # ---- health ----------------------------------------------------------

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def ensure_enabled(self):
        """Re-enable the tap if macOS has switched it off. Returns True if it
        had to act. Called from the _tick watchdog as a backstop to the
        kCGEventTapDisabledBy* event, which is the usual way we hear about it."""
        if Quartz is None or self._tap is None:
            return False
        try:
            if Quartz.CGEventTapIsEnabled(self._tap):
                return False
            Quartz.CGEventTapEnable(self._tap, True)
            self._reenables += 1
            log("the keyboard event tap had been switched off by macOS — "
                "re-enabled it (%d time(s) so far this run)" % self._reenables)
            return True
        except Exception:
            log_exc("could not re-enable the keyboard event tap")
            return False

    # ---- the callback ----------------------------------------------------

    def _callback(self, proxy, event_type, event, refcon):
        """Runs on the tap thread for every key event. MUST be fast: macOS
        disables a tap whose callback dawdles, which is the whole reason this
        class exists. Everything slow is dispatched to a worker elsewhere."""
        try:
            # macOS telling us it has switched the tap off. Turning it back on
            # here is what makes the hotkey survive; without this the app looks
            # alive and does nothing.
            if event_type in (Quartz.kCGEventTapDisabledByTimeout,
                              Quartz.kCGEventTapDisabledByUserInput):
                why = ("it was too slow to respond"
                       if event_type == Quartz.kCGEventTapDisabledByTimeout
                       else "the system disabled it")
                self._reenables += 1
                Quartz.CGEventTapEnable(self._tap, True)
                log("macOS switched the keyboard event tap off (%s) — "
                    "re-enabled it (%d time(s) so far this run)"
                    % (why, self._reenables))
                return event

            vk = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode)

            if event_type == Quartz.kCGEventFlagsChanged:
                mod = MODIFIER_VK.get(vk)
                if mod is not None:
                    name, bit = mod
                    key = getattr(keyboard.Key, name, None)
                    if key is not None:
                        flags = Quartz.CGEventGetFlags(event)
                        if flags & bit:
                            self._on_press(key)
                        else:
                            self._on_release(key)
                return event

            arrow = VK_TO_ARROW.get(vk)
            if arrow is not None:
                key = getattr(keyboard.Key, arrow, None)
                if key is not None:
                    if event_type == Quartz.kCGEventKeyDown:
                        self._on_press(key)
                    elif event_type == Quartz.kCGEventKeyUp:
                        self._on_release(key)
        except Exception:
            # An exception must never escape into the tap. This is exactly how
            # the pynput listener used to die: one bad event and the whole
            # hotkey stopped, silently, for the rest of the session.
            log_exc("error handling a key event (ignored, tap stays up)")
        return event


def chord_letter(key):
    """Return the key string for a pressed pynput key using its macOS virtual-key
    code (so Control/Option don't change the result), or None if it isn't one of
    our text-mode keys. The vk path handles letters AND symbols (e.g. "/"); the
    char fallback only matters when vk is unavailable, which doesn't happen for
    these keys on macOS."""
    vk = getattr(key, "vk", None)
    if vk in VK_TO_LETTER:
        return VK_TO_LETTER[vk]
    ch = getattr(key, "char", None)  # fallback if vk is unavailable
    if ch and len(ch) == 1 and ch.isalpha():
        return ch.lower()
    return None


def _hotkey_valid(mode):
    """True if a mode's hotkey is valid. Both voice and text modes use the four
    arrows now (text adds Command to the gesture, not a different key)."""
    return mode.get("hotkey") in ARROW_HOTKEYS


DEFAULT_CONFIG = {
    # Which AI provider Yap sends recordings/text to: "gemini" or "openai".
    "provider": "gemini",
    # One key + model per provider, so you can switch without re-pasting. Only
    # audio-capable models belong here (all Gemini models; OpenAI's
    # gpt-4o-*-audio-preview pair and the newer gpt-audio family) because voice
    # is a single native call: the recording goes straight to the model.
    "providers": {
        "gemini": {
            "api_key": "PASTE_YOUR_GEMINI_API_KEY_HERE",
            "model": "gemini-3.1-flash-lite",
        },
        "openai": {
            "api_key": "",
            "model": "gpt-4o-audio-preview",
        },
    },
    # How a voice recording is triggered:
    #   "hold"   — hold Right Option, tap an arrow, release to stop (the classic).
    #   "toggle" — tap Right Option + arrow once to start, tap Right Option again
    #              to stop. Recording keeps going after you let go.
    "trigger_mode": "hold",
    # What's shown while recording a voice mode:
    #   "full"    — the whole quick panel pops up (current behavior).
    #   "minimal" — a small "Listening…" chip near the menu bar, so the rest of
    #               your screen stays usable.
    "recording_indicator": "full",
    # Which microphone to record from. Empty string = let macOS pick the system
    # default input. Otherwise the exact device name (as shown in Settings →
    # Microphone). Matters on Macs without a built-in mic (e.g. a Mac Mini), where
    # the "default" input may not be the mic you actually plugged in.
    "input_device": "",
    # False until the user finishes the welcome/onboarding flow (or saves a key
    # in it). Existing configs are treated as already-onboarded on load.
    "onboarding_complete": False,
    # Bumped whenever the built-in prompts change. On startup, if your config's
    # version is older, the modes are refreshed to these defaults (your provider,
    # API keys and model picks are kept). New prompt fixes apply on a restart.
    "config_version": 9,
    # The version that last ran. Used only to notice an upgrade and drop the
    # stale Settings web cache; never affects your settings or your key.
    "last_version": "",
    "modes": [
        {
            "key": "clean",
            "name": "Clean",
            "input": "audio",
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
            "key": "summary",
            "name": "Summary",
            "input": "audio",
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
            "key": "email",
            "name": "Email",
            "input": "audio",
            "hotkey": "right",
            "prompt": (
                "You are my email-drafting assistant. I will speak, giving the "
                "context and the points I want to make. Turn what I say into a "
                "clear, well-structured email, ready to send, in the same "
                "language I speak. Write a short, relevant subject line labeled "
                "\"Subject:\". Open with an appropriate greeting (use the "
                "recipient's name if I mention it, otherwise a neutral greeting). "
                "Write the body in clear, well-organized paragraphs, using short "
                "bullet points only if I list several items. Cover everything I "
                "asked for, in a professional but friendly tone unless I specify "
                "a different one. Close with a suitable sign-off, using my name if "
                "I say it, otherwise leave a \"[Your name]\" placeholder. Do not "
                "invent facts I did not give you, do not include a transcript or "
                "any commentary, and return only the finished email. Do not use "
                "em dashes; use commas, colons, or separate sentences instead. If "
                "I state a tone, recipient, length, or language in the recording, "
                "follow that."
            ),
        },
        {
            "key": "custom",
            "name": "Custom",
            "input": "audio",
            "hotkey": "down",
            "prompt": (
                "Transcribe the following audio and translate the result into "
                "English. Return ONLY the translated text. (This is your custom "
                "slot — edit this prompt to do whatever you want.)"
            ),
        },
        {
            # A TEXT mode (no recording): select text anywhere, then tap this
            # arrow while holding Right Command + Right Option (⌘⌥) to rewrite the
            # selection in place. Same arrows as voice; the extra Command is what
            # makes it rewrite instead of record.
            "key": "rewrite",
            "name": "Rewrite",
            "input": "text",
            "hotkey": "left",  # Right ⌘ + Right ⌥ + ←
            "prompt": (
                "Rewrite the following text in a clean, clear and friendly way. "
                "Keep my meaning and key points. Return only the rewritten text, "
                "with no preamble or commentary. Do not use em dashes; use commas, "
                "colons, or separate sentences instead."
            ),
        },
    ],
}


def _migrate_from_parla():
    """One-time migration from the old "Parla" name.

    If the new ~/.yap/config.json doesn't exist yet but an old
    ~/.parla/config.json does, copy the settings over (API key, model, modes)
    so nothing is lost. The old log is copied too. The old files are left in
    place untouched — we only ever copy, never delete."""
    try:
        if CONFIG_PATH.exists():
            return  # already migrated / fresh Yap config in place
        if OLD_CONFIG_PATH.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(OLD_CONFIG_PATH, CONFIG_PATH)
            if OLD_LOG_PATH.exists() and not LOG_PATH.exists():
                shutil.copy2(OLD_LOG_PATH, LOG_PATH)
            log("migrated settings from ~/.parla → ~/.yap")
    except Exception:
        log_exc("migration from ~/.parla failed (continuing with defaults)")


def load_config():
    """Read config.json, creating it with defaults the first time."""
    _migrate_from_parla()
    if not CONFIG_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    needs_save = False
    # Default (and sanity-check) the top-level behavior settings on every load, so
    # configs written before these existed gain them, and bad values can't break
    # the recording state machine or the indicator. (The version bump below only
    # refreshes `modes`, so these need their own pass.)
    if cfg.get("trigger_mode") not in ("hold", "toggle"):
        cfg["trigger_mode"] = "hold"
        needs_save = True
    if cfg.get("recording_indicator") not in ("full", "minimal"):
        cfg["recording_indicator"] = "full"
        needs_save = True
    # Microphone selection: default to the empty string (system default input)
    # for configs written before this existed, and coerce any non-string value.
    if not isinstance(cfg.get("input_device"), str):
        cfg["input_device"] = ""
        needs_save = True
    # An existing config means the user has already used Yap, so treat them as
    # already-onboarded (only a brand-new config, created above with the default
    # False, should trigger the welcome flow). This runs only for configs written
    # before the flag existed.
    if "onboarding_complete" not in cfg:
        cfg["onboarding_complete"] = True
        needs_save = True
    # Provider migration: older configs stored a single flat api_key + model
    # (Gemini-only). Fold those into the new per-provider structure so nothing
    # is lost, keep Gemini as the default, and drop the old flat keys. This is
    # structural (runs whenever `providers` is absent), independent of the
    # version bump below.
    if "providers" not in cfg:
        old_key = cfg.pop("api_key", "") or DEFAULT_CONFIG["providers"]["gemini"]["api_key"]
        old_model = cfg.pop("model", "") or DEFAULT_CONFIG["providers"]["gemini"]["model"]
        cfg["providers"] = {
            "gemini": {"api_key": old_key, "model": old_model},
            "openai": dict(DEFAULT_CONFIG["providers"]["openai"]),
        }
        cfg.setdefault("provider", "gemini")
        needs_save = True
    # Sanity-check the provider selection and make sure both provider slots
    # exist with their fields, so a hand-edited or partial config can't wedge
    # the picker. (Like the trigger_mode pass above; the version bump only
    # touches `modes`.)
    if cfg.get("provider") not in ("gemini", "openai"):
        cfg["provider"] = "gemini"
        needs_save = True
    providers = cfg.setdefault("providers", {})
    for name in ("gemini", "openai"):
        slot = providers.get(name)
        if not isinstance(slot, dict):
            slot = {}
            providers[name] = slot
            needs_save = True
        if "api_key" not in slot:
            slot["api_key"] = DEFAULT_CONFIG["providers"][name]["api_key"]
            needs_save = True
        if not slot.get("model"):
            slot["model"] = DEFAULT_CONFIG["providers"][name]["model"]
            needs_save = True
    # Migrate away from Gemini models Google has retired/shut down: point a saved
    # config at the current default so it never references a dead model. (A custom
    # model the user typed is left alone — only the known-retired ids are moved.)
    retired_gemini = {"gemini-2.0-flash", "gemini-2.0-flash-lite",
                      "gemini-1.5-flash", "gemini-1.5-pro"}
    gem = providers.get("gemini")
    if isinstance(gem, dict) and gem.get("model") in retired_gemini:
        gem["model"] = DEFAULT_CONFIG["providers"]["gemini"]["model"]
        needs_save = True
    # If the built-in prompts have been updated (newer version), refresh the
    # modes to the latest defaults, keeping the user's provider, keys and models.
    if cfg.get("config_version") != DEFAULT_CONFIG["config_version"]:
        cfg["modes"] = [dict(m) for m in DEFAULT_CONFIG["modes"]]
        cfg["config_version"] = DEFAULT_CONFIG["config_version"]
        needs_save = True
    # Safety net: fix any invalid/old hotkeys (arrows for voice, letters for text).
    elif not cfg.get("modes") or not all(_hotkey_valid(m) for m in cfg["modes"]):
        cfg["modes"] = [dict(m) for m in DEFAULT_CONFIG["modes"]]
        needs_save = True
    if needs_save:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    return cfg


# ---------------------------------------------------------------------------
# LLM providers: send audio (or text) + a prompt, get back finished text.
#
# Yap supports two providers, both reached with ONE native request that takes
# the recording and returns finished text — no separate transcription step:
#   - Google Gemini  (any multimodal Gemini model; audio via inline_data)
#   - OpenAI         (gpt-4o-audio-preview / gpt-audio chat models; audio via the
#                     chat `input_audio` content part)
# A shared retry/backoff core is used by both so a recording is never lost just
# because the model was briefly busy.
#
# Deliberately NOT supported: Anthropic/Claude. Claude's API takes no audio, so
# Yap would have to run a separate speech-to-text call first and then a second
# call to format the result — two round trips instead of one, which is exactly
# the latency Yap is built to avoid. Settings → Provider & model says the same
# thing to the user.
# ---------------------------------------------------------------------------

# --- HTTPS plumbing -------------------------------------------------------
#
# Why we keep our own copy of the CA bundle:
#
# Inside the py2app bundle, certifi's cacert.pem lives in the zipped
# site-packages, so certifi.where() EXTRACTS it to a throwaway file in the
# per-user temp dir (/var/folders/…/T/tmpXXXXcacert.pem) and then caches that
# path for the whole life of the process. macOS periodically purges that
# directory. When it does, the path certifi remembers no longer exists, and
# every single HTTPS call fails INSTANTLY with
#     OSError: Could not find a suitable TLS CA certificate bundle
# and keeps failing until the app is relaunched (a fresh launch re-extracts it).
# That is the real reason a long-running Yap stops reaching Gemini after a
# sleep/wake cycle and only a quit-and-reopen brings it back.
#
# So: copy the bundle once into ~/.yap/cacert.pem — a location macOS never
# cleans — and verify against that. If it ever goes missing we rebuild it in
# place, so the app heals itself instead of needing a restart.

def _refresh_ca_bundle(force: bool = False) -> str:
    """Make sure ~/.yap/cacert.pem exists and return its path (or "" if we
    could not produce one, in which case requests falls back to its default)."""
    try:
        if force or not CA_BUNDLE_PATH.exists() or CA_BUNDLE_PATH.stat().st_size < 1000:
            if certifi is None:
                return ""
            src = certifi.where()
            if not os.path.exists(src):
                # certifi's own cached temp copy is gone too; nothing to copy.
                log("CA bundle: certifi source missing at", src)
                return ""
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, str(CA_BUNDLE_PATH))
            log(f"CA bundle refreshed → {CA_BUNDLE_PATH}")
        return str(CA_BUNDLE_PATH)
    except Exception:
        log_exc("CA bundle refresh failed")
        return ""


def ca_bundle():
    """Path to verify HTTPS against for this request. Cheap existence check on
    every call so a purged bundle is rebuilt before it can break anything."""
    return _refresh_ca_bundle() or True  # True = requests' built-in default


# --- wake from sleep ------------------------------------------------------
# When the Mac wakes, sockets Yap was holding are dead and the temp CA file may
# have been purged while it slept. Rebuilding the CA bundle here means the FIRST
# recording after wake works, instead of failing and needing a quit-and-reopen.
if NSObject is not None:
    class _YapWakeObserver(NSObject):
        def handleWake_(self, _note):
            try:
                log("system woke from sleep — refreshing network state")
                _refresh_ca_bundle(force=True)
            except Exception:
                log_exc("wake handler failed")
else:
    _YapWakeObserver = None


# --- retry/backoff tuning -------------------------------------------------
# The old settings (12 attempts, 2,4,6,8…s waits) could add ~2 minutes of pure
# waiting to a single recording. Real blips are short, so: fewer attempts, much
# shorter waits, and a hard cap on the total time backoff may ever add.
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = [0.4, 0.8, 1.5, 3.0, 5.0]
MAX_TOTAL_BACKOFF = 12.0
# Split timeouts. A short CONNECT timeout means a socket left dead by sleep
# fails fast and we retry, instead of hanging for a minute. The READ timeout is
# per-socket-operation (not total), and has to cover uploading a long recording.
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 120


def _post_with_retry(url: str, headers: dict, body: dict, parse_fn,
                     retries: int = MAX_ATTEMPTS, label: str = "LLM") -> str:
    """POST `body` to `url` and return the text pulled out by `parse_fn(json)`.

    Retries on BOTH kinds of transient failure:
      * provider "busy" replies (429/500/503), and
      * connection-level errors — dropped/dead sockets, DNS hiccups, timeouts,
        a purged CA bundle — which is what a sleep/wake cycle produces. These
        used to escape as a hard error and lose the recording.

    Every attempt opens a BRAND-NEW connection (fresh Session + `Connection:
    close`) so we can never reuse a keep-alive socket that died while the Mac
    was asleep.

    Logs the wall-clock duration, attempt count and total backoff of every call,
    so the log shows at a glance whether a slow yap was our retry waiting or the
    provider's own latency.
    """
    last_resp = None
    last_exc = None
    slept = 0.0
    started = time.time()

    def _wait(attempt, why):
        """Sleep before the next attempt; returns False if we're done retrying."""
        nonlocal slept
        if attempt >= retries - 1 or slept >= MAX_TOTAL_BACKOFF:
            return False
        wait = min(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)],
                   MAX_TOTAL_BACKOFF - slept)
        log(f"{label} {why}; retrying in {wait:.1f}s "
            f"(attempt {attempt + 1}/{retries})…")
        time.sleep(wait)
        slept += wait
        return True

    for attempt in range(retries):
        try:
            # A new Session per attempt — no pooled sockets survive between
            # calls, so a connection killed by sleep can never be reused.
            with requests.Session() as sess:
                resp = sess.post(url,
                                 headers=dict(headers, **{"Connection": "close"}),
                                 json=body,
                                 timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                                 verify=ca_bundle())
        except OSError as e:
            # Covers requests' ConnectionError/Timeout/SSLError (all subclass
            # OSError) and the bare OSError from a missing CA bundle.
            last_exc = e
            log(f"{label} connection problem: {e.__class__.__name__}: "
                f"{_redact(str(e))[:200]}")
            # If the bundle was purged, rebuild it here so the very next
            # attempt succeeds — no restart needed. (Not forced: rewriting a
            # perfectly good 230KB file on every retry is just noise.)
            _refresh_ca_bundle()
            if _wait(attempt, "connection failed"):
                continue
            break
        if resp.status_code in (429, 500, 503):
            last_resp = resp
            last_exc = None
            if _wait(attempt, f"busy ({resp.status_code})"):
                continue
            break
        resp.raise_for_status()
        text = parse_fn(resp.json())
        log(f"{label} OK in {time.time() - started:.1f}s "
            f"({attempt + 1} attempt{'s' if attempt else ''}, "
            f"{slept:.1f}s of that waiting to retry)")
        return text

    log(f"{label} FAILED after {time.time() - started:.1f}s "
        f"({retries} attempts, {slept:.1f}s of that waiting to retry)")
    if last_exc is not None:
        raise last_exc
    if last_resp is not None:
        last_resp.raise_for_status()
    return ""


def _gemini_call(parts: list, api_key: str, model: str,
                 retries: int = MAX_ATTEMPTS) -> str:
    """One Gemini generateContent call. `parts` is the list of content parts
    (text and, for audio, an inline_data part)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    body = {"contents": [{"parts": parts}]}

    def _parse(d):
        # Gemini echoes the model that actually served the call. Log it whenever
        # it isn't the one we asked for, so a silent fallback to a slower model
        # can never hide behind "the API was slow today".
        served = (d.get("modelVersion") or "").strip()
        if served and served != model:
            log(f"Gemini model: asked for {model!r}, served by {served!r}")
        # Pull the text out of the first candidate's first part.
        return d["candidates"][0]["content"]["parts"][0]["text"].strip()

    return _post_with_retry(url, headers, body, _parse, retries, label="Gemini")


def _openai_call(messages: list, api_key: str, model: str,
                 retries: int = MAX_ATTEMPTS, audio: bool = False) -> str:
    """One OpenAI chat-completions call. `messages` is the chat messages list;
    set `audio=True` when a message carries an input_audio part, so the model
    is told to answer with text."""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages}
    if audio:
        body["modalities"] = ["text"]
    return _post_with_retry(
        url, headers, body,
        lambda d: (d["choices"][0]["message"]["content"] or "").strip(),
        retries, label="OpenAI")


def transcribe(wav_bytes: bytes, prompt: str, provider: str, api_key: str,
               model: str, retries: int = MAX_ATTEMPTS) -> str:
    """Send WAV audio + an instruction to the chosen provider in a single
    native call and return the finished text. Only audio-capable models work
    here (all Gemini models; OpenAI's gpt-4o-audio-preview,
    gpt-4o-mini-audio-preview, gpt-audio-1.5 and gpt-audio chat models)."""
    audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")
    if provider == "openai":
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "input_audio",
             "input_audio": {"data": audio_b64, "format": "wav"}},
        ]}]
        return _openai_call(messages, api_key, model, retries, audio=True)
    # Gemini (default for any unknown provider).
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "audio/wav", "data": audio_b64}},
    ]
    return _gemini_call(parts, api_key, model, retries)


def rewrite(text: str, prompt: str, provider: str, api_key: str,
            model: str, retries: int = MAX_ATTEMPTS) -> str:
    """Send selected text + an instruction to the chosen provider as a plain
    text request (no audio) and return the rewritten result. Reuses the same
    retry/backoff and logging as the audio path."""
    combined = prompt.rstrip() + "\n\n" + text
    if provider == "openai":
        messages = [{"role": "user", "content": combined}]
        return _openai_call(messages, api_key, model, retries)
    # Gemini (default for any unknown provider).
    return _gemini_call([{"text": combined}], api_key, model, retries)


# ---------------------------------------------------------------------------
# Recording safety net: no clip is ever lost to a failed upload.
# ---------------------------------------------------------------------------

def _save_recording(wav_bytes: bytes):
    """Write a clip to ~/.yap/recordings/ BEFORE we try to send it, and return
    the path (or None if we couldn't). The directory is this user's only —
    created 0700 — and never lives in the shared/system temp dir."""
    try:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(RECORDINGS_DIR, 0o700)
        path = RECORDINGS_DIR / time.strftime("yap-%Y%m%d-%H%M%S.wav")
        n = 1
        while path.exists():  # two clips in the same second
            path = RECORDINGS_DIR / time.strftime(f"yap-%Y%m%d-%H%M%S-{n}.wav")
            n += 1
        with open(path, "wb") as f:
            f.write(wav_bytes)
        os.chmod(path, 0o600)
        return path
    except Exception:
        log_exc("could not save recording safety copy")
        return None


def _discard_recording(path):
    """Delete the safety copy — called the moment the model returns text, so a
    successful yap leaves nothing behind on disk."""
    if not path:
        return
    try:
        os.remove(path)
    except Exception:
        log_exc("could not remove recording safety copy")


def _sweep_old_recordings():
    """At startup, delete safety copies older than 24 hours so recordings from
    failed uploads can never pile up."""
    try:
        if not RECORDINGS_DIR.is_dir():
            return
        cutoff = time.time() - RECORDING_KEEP_SECONDS
        removed = 0
        for f in RECORDINGS_DIR.glob("yap-*.wav"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                pass
        if removed:
            log(f"cleaned up {removed} recording(s) older than 24h")
    except Exception:
        log_exc("recording cleanup failed")


def _active_provider(config: dict):
    """Resolve the currently selected provider and its key/model from config.
    Returns (provider, api_key, model). Falls back to Gemini for any unknown
    or malformed provider so a bad config can never wedge the app."""
    provider = config.get("provider", "gemini")
    if provider not in ("gemini", "openai"):
        provider = "gemini"
    pcfg = (config.get("providers") or {}).get(provider) or {}
    default_model = DEFAULT_CONFIG["providers"][provider]["model"]
    return provider, pcfg.get("api_key", ""), pcfg.get("model") or default_model


def _has_usable_key(config: dict) -> bool:
    """True only if the selected provider has a REAL API key (non-empty and not
    the bundled `PASTE_YOUR…` placeholder). Drives the onboarding trigger so the
    welcome flow always appears when there is no working key in ~/.yap — it can
    never be skipped because of a leftover/placeholder config."""
    _provider, key, _model = _active_provider(config)
    return bool(key) and not key.startswith("PASTE_YOUR")


# ---------------------------------------------------------------------------
# Audio recording: collect mic frames while a key is held.
# ---------------------------------------------------------------------------

def list_input_devices():
    """Input-capable devices for the Settings microphone picker. Always returns
    at least the system-default entry, and never raises — a PortAudio hiccup must
    not break the Settings window. Each entry is {"name", "label"}; an empty
    name means "let macOS pick the system default input"."""
    devices = [{"name": "", "label": "Default (system)"}]
    try:
        seen = set()
        for dev in sd.query_devices():
            if dev.get("max_input_channels", 0) > 0:
                name = (dev.get("name") or "").strip()
                if name and name not in seen:
                    seen.add(name)
                    devices.append({"name": name, "label": name})
    except Exception as e:
        log("could not enumerate input devices:", e)
    return devices


def _resolve_input_device(name):
    """Map a saved device name to a PortAudio device index. Returns None for the
    system default — either because the name is empty, or because that device
    isn't currently connected (so we fall back to the default instead of failing)."""
    name = (name or "").strip()
    if not name:
        return None
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if (dev.get("max_input_channels", 0) > 0
                    and (dev.get("name") or "").strip() == name):
                return idx
    except Exception:
        pass
    return None


class Recorder:
    """Owns the microphone stream.

    start() and stop() are called from DIFFERENT worker threads (opening and
    closing the device are both too slow to run on the event-tap callback — see
    _on_press), and a quick tap can order them so that stop() arrives while
    start() is still opening the device. `_lock` serialises the two, and
    `_generation` lets a start that finishes after its stop close itself
    immediately instead of leaving the microphone — and its recording
    indicator — switched on forever."""

    def __init__(self):
        self._frames = []
        self._stream = None
        self._lock = threading.Lock()
        self._generation = 0
        # The rate/channels we actually CAPTURED at (filled in by start()). The
        # WAV we hand back is always SAMPLE_RATE / CHANNELS — we resample in stop().
        self._capture_rate = SAMPLE_RATE
        self._capture_channels = CHANNELS

    def start(self, device_name=""):
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._frames = []

        device = _resolve_input_device(device_name)

        # Open the mic at its NATIVE sample rate and channel count instead of
        # forcing 16 kHz mono. Many external/USB mics — and any Mac without a
        # built-in microphone (e.g. a Mac Mini) — refuse to open at exactly
        # 16 kHz, which is why recording silently failed there. We capture at
        # whatever the device supports and downsample to 16 kHz in stop().
        try:
            info = sd.query_devices(device, "input")
            rate = int(round(info.get("default_samplerate") or SAMPLE_RATE))
            max_in = int(info.get("max_input_channels") or 1)
        except Exception:
            rate = SAMPLE_RATE
            max_in = CHANNELS
        channels = max(1, min(2, max_in))

        self._capture_rate = rate
        self._capture_channels = channels

        def callback(indata, frames, time_info, status):
            self._frames.append(indata.copy())

        stream = sd.InputStream(
            samplerate=rate, channels=channels, dtype="int16",
            device=device, callback=callback,
        )
        stream.start()

        with self._lock:
            if generation != self._generation:
                # A stop() (or another start) happened while the device was
                # opening. This stream is already obsolete — close it here or the
                # microphone stays live with nobody holding it.
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                log("microphone opened after the recording had already stopped "
                    "— closed it again")
                return
            self._stream = stream

    def stop(self) -> bytes:
        """Stop recording and return the audio as in-memory WAV bytes — always
        16 kHz mono, regardless of the rate/channels the device captured at.

        Closing the stream is guarded: if the microphone was unplugged (or macOS
        pulled it out from under us) PortAudio raises here, and an exception
        escaping this method used to be fatal — it propagated into the key-release
        handler and permanently killed the hotkey listener. Whatever frames were
        already captured are still worth returning, so we log the failure and
        carry on with them."""
        with self._lock:
            # Retiring the generation tells an in-flight start() that its stream
            # is obsolete, so a stop that lands mid-open still leaves the
            # microphone closed.
            self._generation += 1
            stream, self._stream = self._stream, None
            frames, self._frames = self._frames, []
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                log("could not close the microphone stream cleanly:", e)
        if not frames:
            return b""
        audio = np.concatenate(frames, axis=0)  # (n, channels) int16

        # Down-mix to mono.
        if audio.ndim == 2 and audio.shape[1] > 1:
            audio = audio.mean(axis=1)
        audio = audio.reshape(-1).astype(np.float32)

        # Resample to 16 kHz with linear interpolation. Dependency-free (numpy is
        # already bundled) and perfectly adequate for speech. Skipped when the
        # device already captured at 16 kHz.
        if self._capture_rate and self._capture_rate != SAMPLE_RATE and audio.size:
            n_out = int(round(audio.size * SAMPLE_RATE / self._capture_rate))
            if n_out > 0:
                src_idx = np.linspace(0.0, audio.size - 1, num=n_out)
                audio = np.interp(src_idx, np.arange(audio.size), audio)

        audio = np.clip(audio, -32768, 32767).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()

    def recent_peak(self):
        """Peak |sample| of the most recently captured frame, normalized to 0–1.
        Used by the Settings microphone test for a live input-level meter; returns
        0.0 when nothing has been captured yet. Cheap and read-only."""
        try:
            frame = self._frames[-1]
            if frame is None or frame.size == 0:
                return 0.0
            return float(np.abs(frame).max()) / 32768.0
        except Exception:
            return 0.0


# Below this, audio is considered "silent" — typical room noise on a working mic
# sits well above it, while a muted/absent input stays near zero.
MIC_TEST_THRESHOLD = 0.015


def _mic_permission_denied():
    """True only if macOS reports the Microphone permission as explicitly denied.
    Used to refine a failed mic test — not as a hard pre-gate, because the status
    is unreliable for a non-bundled interpreter (it can read 'denied' even while
    capture works)."""
    try:
        import permissions
        return permissions.microphone_status() == "denied"
    except Exception:
        return False


def diag_mic(device_name="", on_level=None, duration=3.0):
    """Microphone diagnostic for Settings. Records ~`duration` seconds from the
    selected input device and reports whether any sound was captured. Returns
    (ok, peak, message). Never hangs: a fixed duration plus try/except guards.

    `on_level(level)` (0–1), if given, is called ~10×/sec for a live meter.

    We try to record FIRST and only consult the permission status to explain a
    failure, so a flaky 'denied' reading can't produce a false negative."""
    rec = Recorder()
    try:
        rec.start(device_name)
    except Exception as e:
        if _mic_permission_denied():
            return (False, 0.0,
                    "Microphone permission is off. Grant it in System Settings → "
                    "Privacy & Security → Microphone, then quit and reopen Yap.")
        return (False, 0.0,
                "Couldn't open this microphone (" + _redact(str(e)) + "). "
                "Pick a different input under Microphone, or check that one is "
                "connected.")

    peak = 0.0
    try:
        steps = max(1, int(duration / 0.1))
        for _ in range(steps):
            time.sleep(0.1)
            lvl = rec.recent_peak()
            if lvl > peak:
                peak = lvl
            if on_level is not None:
                try:
                    on_level(lvl)
                except Exception:
                    pass
    finally:
        try:
            rec.stop()
        except Exception:
            pass

    if peak >= MIC_TEST_THRESHOLD:
        return (True, peak, "Audio detected — this microphone is working.")
    # Opened fine but stayed silent: a denied permission delivers zeros, so name
    # that explicitly; otherwise it's a muted/quiet/wrong input.
    if _mic_permission_denied():
        return (False, peak,
                "No sound came through. Microphone permission is off — grant it in "
                "System Settings → Privacy & Security → Microphone, then quit and "
                "reopen Yap.")
    return (False, peak,
            "No sound detected. Speak while testing, raise the input volume, or "
            "pick a different microphone above.")


def diag_api(config):
    """API-connection diagnostic for Settings. Sends one minimal request to the
    currently selected provider + model with the saved key and reports
    (ok, message). Fails fast (retries=1) and never leaks the key."""
    provider, api_key, model = _active_provider(config)
    pname = "OpenAI" if provider == "openai" else "Gemini"
    if not api_key or api_key.startswith("PASTE_YOUR"):
        return (False, "No " + pname + " API key set — paste one under "
                "“Provider & model”.")
    try:
        rewrite("ping", "Reply with OK.", provider, api_key, model, retries=1)
        return (True, "Connected to " + pname + " (" + model + ").")
    except requests.HTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (400, 401, 403):
            msg = (pname + " rejected the request (HTTP " + str(status) + ") — "
                   "usually a wrong/expired API key or a model your key can't use. "
                   "Check the key and model.")
        elif status == 404:
            msg = ("Model “" + model + "” isn’t available on " + pname +
                   " (HTTP 404). Pick a different model.")
        elif status == 429:
            msg = (pname + " is rate-limiting right now (HTTP 429). Wait a moment "
                   "and try again.")
        else:
            msg = pname + " returned HTTP " + str(status) + "."
        return (False, msg)
    except requests.RequestException as e:
        return (False, "Couldn’t reach " + pname + " — check your internet "
                "connection. (" + _redact(str(e)) + ")")
    except OSError as e:
        # Non-requests connection-level failure (e.g. a missing CA bundle after
        # macOS purged the temp dir). Yap rebuilds the bundle on the next call.
        return (False, "Couldn’t reach " + pname + " — connection problem. ("
                + _redact(str(e))[:160] + ")")
    except Exception as e:
        return (False, "Unexpected error: " + _redact(str(e)))


def diag_paste():
    """Accessibility diagnostic for Settings. Yap delivers every result by
    simulating Cmd+V, which macOS only permits for apps trusted for
    Accessibility — the same trust the text-rewrite chord needs. A real paste
    can't be tested without stealing focus and typing into whoever is in front,
    so this reports the trust status, which is what actually breaks. Returns
    (ok, message)."""
    try:
        import permissions
        status = permissions.accessibility_status()
    except Exception:
        log_exc("diag_paste failed")
        status = "unknown"
    if status == "granted":
        return (True, "Accessibility is on — Yap can paste into other apps and "
                "the rewrite shortcut works.")
    if status == "denied":
        return (False, "Accessibility is off, so Yap can transcribe but can’t "
                "paste the result (and the rewrite shortcut won’t fire). Open "
                "the fix below to switch it on.")
    return (False, "Couldn’t read the Accessibility permission. Check it under "
            "System Settings → Privacy & Security → Accessibility.")


# ---------------------------------------------------------------------------
# Pasting: copy text to clipboard, then simulate Cmd+V into the focused app.
# ---------------------------------------------------------------------------

_kbd = keyboard.Controller()


def _post_cmd_key(vk):
    """Send a single Cmd+<vk> keystroke via a Quartz CGEvent with an EXPLICIT
    Command-only flag mask. Unlike pynput's press/release (which combine with
    whatever modifiers are physically held), this guarantees a clean Cmd+key:
    if the user is still holding the Control+Option rewrite chord, the copy/paste
    won't degrade into Ctrl+Opt+Cmd+key (which copies/pastes nothing)."""
    down = Quartz.CGEventCreateKeyboardEvent(None, vk, True)
    Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
    up = Quartz.CGEventCreateKeyboardEvent(None, vk, False)
    Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def _send_cmd(letter):
    """Cmd+<letter> via Quartz when available, else fall back to pynput."""
    if Quartz is not None and letter in LETTER_VK:
        _post_cmd_key(LETTER_VK[letter])
    else:
        with _kbd.pressed(keyboard.Key.cmd):
            _kbd.press(letter)
            _kbd.release(letter)


# The clipboard is written through the NATIVE pasteboard, never by piping bytes
# to pbcopy. Reason: pbcopy decodes its stdin using the current locale's
# encoding, and a double-clicked .app inherits no LANG at all — so it fell back
# to MacRoman and turned the UTF-8 bytes of "è" (C3 A8) into "√®". Handing
# AppKit a real Unicode string means the encoding is never guessed.
_UTF8_ENV = dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")


def set_clipboard(text: str) -> bool:
    """Put `text` on the macOS clipboard as proper Unicode. True if it stuck."""
    if NSPasteboard is not None:
        try:
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            if pb.setString_forType_(text, NSPasteboardTypeString):
                return True
            log("NSPasteboard refused the string; falling back to pbcopy")
        except Exception:
            log_exc("NSPasteboard write failed; falling back to pbcopy")
    # Fallback only. Forcing a UTF-8 locale is what keeps accents intact here.
    try:
        subprocess.run("pbcopy", input=text.encode("utf-8"), check=True,
                       env=_UTF8_ENV)
        return True
    except Exception:
        log_exc("pbcopy failed")
        return False


def get_clipboard() -> str:
    """Read the macOS clipboard as text (Unicode-safe, same reasoning)."""
    if NSPasteboard is not None:
        try:
            val = NSPasteboard.generalPasteboard().stringForType_(
                NSPasteboardTypeString)
            return "" if val is None else str(val)
        except Exception:
            log_exc("NSPasteboard read failed; falling back to pbpaste")
    try:
        out = subprocess.run("pbpaste", capture_output=True, check=True,
                             env=_UTF8_ENV).stdout
        return out.decode("utf-8", errors="replace")
    except Exception:
        log_exc("pbpaste failed")
        return ""


def paste_text(text: str):
    set_clipboard(text)
    time.sleep(0.15)  # give the clipboard a moment, and let the hotkey fully release
    _send_cmd("v")


def copy_selection() -> str:
    """Simulate Cmd+C to copy the current selection, then return it as text.

    Used by text modes (e.g. Rewrite). To tell "nothing is selected" apart from
    "the clipboard happens to hold old text", we first drop a hidden sentinel on
    the clipboard: a real selection replaces it, but if nothing is selected the
    sentinel survives and we return "" so the caller can quietly do nothing.
    """
    sentinel = "\x00yap-no-selection\x00"
    if not set_clipboard(sentinel):
        log("copy_selection: failed to seed clipboard sentinel")
    time.sleep(0.05)
    _send_cmd("c")
    time.sleep(0.2)  # give the frontmost app time to write its selection out
    text = get_clipboard()
    return "" if text == sentinel else text


# ---------------------------------------------------------------------------
# The menu bar app itself.
# ---------------------------------------------------------------------------

class YapApp(rumps.App):
    # Stop automatically at 7 minutes so we never blow past Gemini's size limit
    # and lose the recording.
    MAX_RECORD_SECONDS = 7 * 60
    DONE_HOLD_SECONDS = 1.1  # how long the green "done" check shows before idle

    def __init__(self):
        idle_icon, idle_tmpl = ICON_STATES["idle"]
        super().__init__("", icon=idle_icon, template=idle_tmpl, quit_button=None)
        self.config = load_config()
        # An upgrade (or a downgrade) invalidates the cached Settings page, so
        # drop it once, here, rather than leaving people looking at the old UI
        # inside the new app. Settings and API key are untouched.
        if self.config.get("last_version") != APP_VERSION:
            previous = self.config.get("last_version") or "none"
            log("version changed: %s -> %s" % (previous, APP_VERSION))
            clear_web_cache()
            self.config["last_version"] = APP_VERSION
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                with open(CONFIG_PATH, "w") as f:
                    json.dump(self.config, f, indent=2)
            except Exception:
                log_exc("could not record the running version")
        # Show the welcome/onboarding flow on first run OR whenever there is no
        # usable API key in ~/.yap (e.g. after `rm -rf ~/.yap`, or a config that
        # was never finished). Keying off the actual stored key — not just the
        # onboarding_complete flag — means onboarding can never be skipped because
        # of a leftover/placeholder config, and a fresh launch always starts at
        # the beginning. (The flag still suppresses re-showing once a real key is
        # in place and the flow was completed, e.g. across the IM quit-and-reopen.)
        self._needs_onboarding = (
            not self.config.get("onboarding_complete", False)
            or not _has_usable_key(self.config))
        self._settings_ctrl = None
        self._onboarding_ctrl = None
        # Onboarding permission walk-through state (Microphone → Input
        # Monitoring → Accessibility), driven on the main thread by _tick.
        self._perm_queue = []          # permissions still to request, in order
        self._perm_active = None       # permission we're currently waiting on
        self._perm_deadline = 0.0      # give up waiting on _perm_active after this
        self._perm_advance = False     # set by the (background) mic callback
        self._perm_last_snapshot = None
        self._panel = None
        self._panel_autoshown = False
        self._panel_compact = False    # is the panel currently the minimal chip?
        self._click_installed = False  # menu-bar icon click routing set up yet?
        self._statusmenu = None        # the NSMenu (kept for right-click)
        # Hold-to-talk tap (see QuartzKeyTap) — set up in _start_listener, which
        # runs before the timer starts, but declared here so _tick can never
        # arrive first and find them missing.
        self._keytap = None
        self._keytap_next_try = 0.0
        self._keytap_granted = False   # is Input Monitoring actually granted?
        self._listener = None
        self._suppressor = None
        self._suppressor_disabled = False
        # Menu-bar icon health (see _statusitem_check). _statusitem_deadline is
        # when we give up waiting for the icon to appear and tell the user.
        self._statusitem_ready = False
        self._statusitem_deadline = time.time() + 5.0
        self._statusitem_warned = False
        self._statusitem_next_check = 0.0
        self._config_mtime = CONFIG_PATH.stat().st_mtime
        self.recorder = Recorder()
        self.recording = False
        self.option_down = False  # is the activator (Right Option) being held?
        self.ctrl_down = False    # is Control held? (legacy; for _process_text settle)
        self.alt_down = False     # is Option/Alt held? (either side)
        self.cmd_down = False     # is Command held? (distinguishes rewrite from voice)
        self._text_busy = False   # a text-rewrite (copy → Gemini → paste) is in flight
        self.active_mode = None
        # Recording trigger state. _active_trigger_mode is snapshotted at the
        # moment recording starts (so changing the setting mid-recording can't
        # strand a live recording); _toggle_armed means "a toggle recording is
        # live and the next standalone Right Option tap should stop it".
        self._active_trigger_mode = "hold"
        self._toggle_armed = False
        # Settings "Test hotkey" diagnostic: while _diag_hotkey_waiting is True,
        # _on_press records the detected voice-mode gesture (without starting a
        # real recording) and signals _diag_hotkey_event.
        self._diag_hotkey_waiting = False
        self._diag_hotkey_result = None
        self._diag_hotkey_event = threading.Event()
        self.last_yap = ""        # most recent transcript (shown in the quick panel)
        self._record_start = None  # when the current recording began
        self._state = "idle"        # currently-applied menu-bar state (main thread)
        self._pending_state = None  # state requested off-thread; applied in _tick
        self._done_until = 0.0      # when to revert the "done" check back to idle
        self._lock = threading.Lock()  # guards starting/stopping a recording
        # Split modes into the two trigger namespaces (arrows vs. letters).
        self._rebuild_hotkey_maps()

        self._build_menu()
        self._start_listener()
        # Pin the CA bundle to ~/.yap up front (see ca_bundle()), clear out any
        # recordings a previous failed upload left behind, and listen for wake.
        _refresh_ca_bundle()
        _sweep_old_recordings()
        try:
            import permissions as _perm
            log("permissions — microphone: %s | input monitoring: %s | "
                "accessibility: %s"
                % (_perm.microphone_status(), _perm.input_monitoring_status(),
                   _perm.accessibility_status()))
        except Exception:
            log_exc("could not read permission status")
        self._wake_observer = None
        self._install_wake_observer()
        # Ticks 4x/second on the main thread: applies pending icon changes (so
        # the busy icon shows promptly) and updates the recording countdown.
        self._timer = rumps.Timer(self._tick, 0.25)
        self._timer.start()

    def _install_wake_observer(self):
        """Ask NSWorkspace to tell us when the Mac wakes from sleep."""
        if _YapWakeObserver is None or NSWorkspace is None:
            log("wake observer unavailable (no PyObjC) — skipping")
            return
        try:
            obs = _YapWakeObserver.alloc().init()
            NSWorkspace.sharedWorkspace().notificationCenter() \
                .addObserver_selector_name_object_(
                    obs, b"handleWake:", "NSWorkspaceDidWakeNotification", None)
            self._wake_observer = obs  # keep a strong ref or it's deallocated
            log("wake-from-sleep observer installed")
        except Exception:
            log_exc("could not install wake observer")

    # ----- menu-bar lifecycle state --------------------------------------

    def _set_state(self, name):
        """Request a menu-bar lifecycle state change from ANY thread.

        We only stash the name here — an atomic attribute assignment that NEVER
        touches AppKit off-thread, so it cannot crash the app — and the
        main-thread _tick timer applies it within ~0.25s. This is what makes the
        icon updates reliable in the packaged app, without the instability of
        calling into PyObjC from a background thread (the bug that previously
        broke recording)."""
        self._pending_state = name

    def _apply_state(self, name):
        """Apply a lifecycle state ON THE MAIN THREAD: swap the icon (toggling
        template mode) and reset the title."""
        icon_path, is_template = ICON_STATES.get(name, ICON_STATES["idle"])
        try:
            self.template = is_template
            self.icon = icon_path
        except Exception:
            log_exc("failed to set menu-bar icon")
        self.title = ""  # recording shows the countdown; other states show no text
        self._state = name
        if name == "done":
            self._done_until = time.time() + YapApp.DONE_HOLD_SECONDS
        # Mirror state to the quick panel if it's open (Item 4 wires this).
        self._push_panel_state(name)

    def _tick(self, _):
        """Runs ~4x/second ON THE MAIN THREAD. Applies any pending state change,
        shows the live recording countdown, and reverts the 'done' check."""
        # Once the run loop is up, route the menu-bar icon's click to the quick
        # panel (left-click) and keep the full menu on right/Control-click.
        if not self._click_installed:
            self._install_icon_click()
        # Keep both taps alive (see _keytap_check / _suppressor_check).
        self._keytap_check()
        self._suppressor_check()
        # Keep the menu-bar icon present (see _statusitem_check).
        self._statusitem_check()
        # Until onboarding is complete, greet the user with the welcome window
        # once the app's run loop is up (doing it here keeps it on the main
        # thread). Cleared immediately so it only opens once per launch.
        if self._needs_onboarding:
            self._needs_onboarding = False
            try:
                self.show_onboarding(None)
            except Exception:
                log_exc("failed to show onboarding")
        # While onboarding is open, keep the permission checklist live and drive
        # the prompt sequence forward as each one is granted.
        if self._onboarding_ctrl is not None:
            try:
                self._perm_tick()
            except Exception:
                log_exc("permission tick failed")
        pending = self._pending_state
        if pending is not None:
            self._pending_state = None
            self._apply_state(pending)
        if self.recording and self._record_start is not None:
            remaining = YapApp.MAX_RECORD_SECONDS - (time.time() - self._record_start)
            if remaining <= 0:
                log("reached 7-minute limit — stopping and processing automatically")
                self._finish_recording()
                return
            m, s = divmod(int(remaining), 60)
            self.title = f" {m}:{s:02d}"
            return
        # Auto-revert the green "done" check back to the idle fish.
        if self._state == "done" and time.time() >= self._done_until:
            self._apply_state("idle")

    def _push_panel_state(self, name):
        """Reflect the lifecycle state in the quick panel (main thread).

        Auto-shows the panel while recording/working so you can see the live
        status, then auto-hides it shortly after a result is delivered. Manual
        opens (via the menu) stay open until toggled again."""
        try:
            if name == "recording":
                self._ensure_panel()
                if self._panel is not None and not self._panel.is_visible():
                    self._panel_autoshown = True
                    # "minimal" → a small Listening chip; "full" → the whole panel.
                    if self.config.get("recording_indicator", "full") == "minimal":
                        self._show_panel_compact()
                    else:
                        self._show_panel()
            if self._panel is None or not self._panel.is_visible():
                return
            self._panel.push_js("yapPanel", self._panel_state(name))
            if name == "idle" and self._panel_autoshown:
                self._panel_autoshown = False
                self._panel.hide_now()
        except Exception:
            log_exc("quick panel update failed")

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
        arrow_glyph = {"left": "←", "up": "↑", "right": "→", "down": "↓"}
        for m in self.config["modes"]:
            if m.get("input", "audio") == "text":
                glyph = arrow_glyph.get(m.get("hotkey"), m.get("hotkey") or "")
                label = "Right ⌘ + Right ⌥ + " + glyph
                items.append(f"   {label}  →  {m['name']}  (text)")
            else:
                label = key_labels.get(m["hotkey"], m["hotkey"])
                items.append(f"   {label}  →  {m['name']}")
        items += [
            None,
            rumps.MenuItem("Quick panel", callback=self.toggle_panel),
            rumps.MenuItem("Settings…", callback=self.edit_settings),
            rumps.MenuItem("Set API key…", callback=self.set_api_key),
            rumps.MenuItem("Open config file…", callback=self.open_config_file),
            rumps.MenuItem("Restore default prompts", callback=self.restore_defaults),
            rumps.MenuItem("Reload settings", callback=self.reload_settings),
            rumps.MenuItem("Reset menu bar icon", callback=self.reset_status_icon),
            rumps.MenuItem("Show welcome screen…", callback=self.show_onboarding),
            rumps.MenuItem("Reset Yap completely…", callback=self.full_reset),
            None,
            rumps.MenuItem(f"Yap {APP_VERSION} — “{APP_CODENAME}”"),  # no callback = greyed label
            rumps.MenuItem("Quit Yap", callback=rumps.quit_application),
        ]
        self.menu = items

    def full_reset(self, _):
        """Hand the user the full reset — the destructive half Yap won't do to
        itself. It runs in Terminal, not silently in here, for two reasons: the
        permission reset needs an administrator password, which you should only
        ever type into something you can see; and you get to watch exactly what
        is deleted. The script shipped inside the app is the same one in the
        project's tools/ folder."""
        script = resource_path("tools", "reset-yap.sh")
        if not os.path.exists(script):
            log("reset script missing from the bundle: %s" % script)
            _alert("Reset script not found",
                   "This build didn't ship tools/reset-yap.sh. Run it from the "
                   "project folder instead.")
            return
        if not rumps.alert(
                title="Reset Yap completely?",
                message=("Terminal will open and walk through the reset: it deletes "
                         "your Yap settings and API key on this Mac, clears its "
                         "caches, and revokes its macOS permissions. The app itself "
                         "is not deleted.\n\n"
                         "You'll be asked to confirm there, and for your password "
                         "(the Accessibility and Input Monitoring permissions live "
                         "in a system database).\n\n"
                         "Reboot afterwards, then open Yap for a clean first run."),
                ok="Open Terminal", cancel="Cancel"):
            return
        try:
            # Copy out of the bundle: Terminal can't always run a script from
            # inside an .app, and the copy is what the user can re-read later.
            dest = CONFIG_DIR / "reset-yap.sh"
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(script, dest)
            os.chmod(dest, 0o755)
            log("opening Terminal to run %s" % dest)
            subprocess.Popen(["open", "-a", "Terminal", str(dest)])
        except Exception:
            log_exc("could not launch the reset script")
            _alert("Couldn't start the reset",
                   "Run it by hand instead:\n\nbash tools/reset-yap.sh")

    def set_api_key(self, _):
        """Quick dialog to paste the API key for the currently selected provider.
        Works even in the bundled app. (Use Settings to switch providers/models.)"""
        provider, current, _model = _active_provider(self.config)
        prefill = "" if (not current or current.startswith("PASTE_YOUR")) else current
        if provider == "openai":
            title = "OpenAI API Key"
            message = "Paste your OpenAI API key (from platform.openai.com):"
        else:
            title = "Gemini API Key"
            message = "Paste your free Gemini API key (get one at aistudio.google.com):"
        window = rumps.Window(
            title=title,
            message=message,
            default_text=prefill,
            ok="Save",
            cancel="Cancel",
            dimensions=(360, 24),
        )
        response = window.run()
        if response.clicked:
            key = response.text.strip()
            if key:
                self.config.setdefault("providers", {}).setdefault(provider, {})["api_key"] = key
                self._save_config()
                notify("Yap", "API key saved", "")

    def edit_settings(self, _):
        """Open the embedded web-view Settings window (Buttons & keys, Voice &
        model, About). Reads and writes the same ~/.yap/config.json."""
        try:
            import webui
            self._maybe_reload()
            if self._settings_ctrl is not None:
                self._settings_ctrl.show_window()
                self._settings_ctrl.push_js("yapInit", self._public_config())
                return

            def on_close(ctrl):
                self._settings_ctrl = None

            self._settings_ctrl = webui.open_window(
                resource_path("web", "settings.html"), resource_path("web"),
                title="Yap Settings", size=(760, 640), resizable=True,
                min_size=(640, 460),
                on_message=self._on_settings_message, on_close=on_close)
        except Exception:
            log_exc("failed to open Settings web view; opening config file instead")
            self.open_config_file(None)

    def show_onboarding(self, _):
        """Open the 3-step welcome / onboarding window (embedded web view)."""
        try:
            import webui
            if self._onboarding_ctrl is not None:
                self._onboarding_ctrl.show_window()
                return

            def on_close(ctrl):
                self._onboarding_ctrl = None

            self._onboarding_ctrl = webui.open_window(
                resource_path("web", "onboarding.html"), resource_path("web"),
                title="Welcome to Yap", size=(700, 600), resizable=False,
                on_message=self._on_onboarding_message, on_close=on_close)
        except Exception:
            log_exc("failed to open onboarding web view")

    def _public_config(self):
        """The slice of config the web UI needs (and is allowed to write back)."""
        providers = self.config.get("providers") or {}
        return {
            "provider": self.config.get("provider", "gemini"),
            "providers": {
                name: {
                    "api_key": (providers.get(name) or {}).get("api_key", ""),
                    "model": (providers.get(name) or {}).get(
                        "model", DEFAULT_CONFIG["providers"][name]["model"]),
                }
                for name in ("gemini", "openai")
            },
            "trigger_mode": self.config.get("trigger_mode", "hold"),
            "recording_indicator": self.config.get("recording_indicator", "full"),
            # The chosen mic ("" = system default) plus the list of currently
            # available input devices, so Settings can render the picker.
            "input_device": self.config.get("input_device", ""),
            "input_devices": list_input_devices(),
            "modes": [dict(m) for m in self.config.get("modes", [])],
            "version": APP_VERSION,
            "codename": APP_CODENAME,
            # The shipped defaults, so the Settings UI can offer a per-mode
            # "Reset to default" (matched by the stable `key`).
            "default_modes": [
                {"key": m["key"], "name": m["name"], "prompt": m["prompt"]}
                for m in DEFAULT_CONFIG["modes"]
            ],
        }

    def _on_settings_message(self, action, payload, ctrl):
        """Handle messages from the Settings web view (main thread)."""
        if action == "ready":
            ctrl.push_js("yapInit", self._public_config())
        elif action == "save" and isinstance(payload, dict):
            try:
                modes = []
                for m in payload.get("modes", []):
                    is_text = m.get("input") == "text"
                    hk = (m.get("hotkey") or "").strip().lower()
                    # Both voice and text modes are bound to arrows now.
                    hotkey = hk if hk in ARROW_HOTKEYS else "left"
                    entry = {
                        "name": (m.get("name") or "Button").strip(),
                        "input": "text" if is_text else "audio",
                        "hotkey": hotkey,
                        "prompt": (m.get("prompt") or "").strip(),
                    }
                    # Preserve the stable default-mode key (powers per-mode
                    # "Reset to default"); user-added modes have none.
                    if m.get("key"):
                        entry["key"] = m.get("key")
                    modes.append(entry)
                if not modes:
                    ctrl.push_js("yapSaved", False)
                    return
                prov = (payload.get("provider") or "gemini").strip()
                self.config["provider"] = prov if prov in ("gemini", "openai") else "gemini"
                in_providers = payload.get("providers") or {}
                providers = self.config.setdefault("providers", {})
                for name in ("gemini", "openai"):
                    slot = providers.setdefault(name, {})
                    incoming = in_providers.get(name) or {}
                    slot["api_key"] = (incoming.get("api_key") or "").strip()
                    slot["model"] = ((incoming.get("model") or "").strip()
                                     or DEFAULT_CONFIG["providers"][name]["model"])
                tm = (payload.get("trigger_mode") or "hold").strip()
                self.config["trigger_mode"] = tm if tm in ("hold", "toggle") else "hold"
                ri = (payload.get("recording_indicator") or "full").strip()
                self.config["recording_indicator"] = ri if ri in ("full", "minimal") else "full"
                # Microphone: accept "" (system default) or a name that is a
                # currently-available input device. Anything else falls back to
                # the default so a stale/unplugged name can't strand recording.
                dev = (payload.get("input_device") or "").strip()
                valid_names = {d["name"] for d in list_input_devices()}
                self.config["input_device"] = dev if dev in valid_names else ""
                self.config["modes"] = modes
                self._save_config()  # writes config.json, rebuilds menu, refreshes hotkeys
                log("settings saved from web UI")
                ctrl.eval_js("window.yapSaved && window.yapSaved(true);")
            except Exception:
                log_exc("failed to save settings from web UI")
                ctrl.eval_js("window.yapSaved && window.yapSaved(false);")
        elif action == "diag_mic":
            self._run_diag("mic", ctrl)
        elif action == "diag_hotkey":
            self._run_diag("hotkey", ctrl)
        elif action == "diag_api":
            self._run_diag("api", ctrl)
        elif action == "diag_paste":
            self._run_diag("paste", ctrl)
        elif action == "permStatus":
            # The "how to fix" panels ask for a fresh reading whenever they're
            # opened, so the user sees the state as it is right now — not as it
            # was when Settings was first shown.
            snap = self._perm_snapshot()
            if snap is not None:
                ctrl.push_js("yapPerms", snap)
        elif action == "openPermissionPane" and isinstance(payload, dict):
            try:
                import permissions
                permissions.open_pane(payload.get("which"))
            except Exception:
                log_exc("failed to open permission pane from Settings")
        elif action == "relaunchApp":
            self._relaunch_app()

    # ---- Settings → Test / Diagnostics ----------------------------------
    def _diag_push(self, ctrl, obj):
        """Send a diagnostics update to the Settings web view on the MAIN thread.
        The tests run on a worker thread, but WKWebView JS must be evaluated on
        the main thread — AppHelper.callAfter hops there (rumps relies on it too)."""
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(ctrl.push_js, "yapDiag", obj)
        except Exception:
            try:
                ctrl.push_js("yapDiag", obj)
            except Exception:
                pass

    def _run_diag(self, which, ctrl):
        """Run one diagnostic on a background thread so the 3-second mic capture
        or a network call never freezes the Settings window or the menu bar."""
        def work():
            try:
                if which == "mic":
                    device = self.config.get("input_device", "") or ""
                    label = device or "Default (system)"
                    ok, peak, msg = diag_mic(
                        device,
                        on_level=lambda lvl: self._diag_push(
                            ctrl, {"test": "mic", "phase": "level", "level": lvl}),
                    )
                    self._diag_push(ctrl, {"test": "mic", "phase": "done",
                                           "ok": ok, "msg": msg, "level": peak,
                                           "device": label})
                elif which == "hotkey":
                    ok, msg = self._diag_hotkey()
                    self._diag_push(ctrl, {"test": "hotkey", "phase": "done",
                                           "ok": ok, "msg": msg})
                elif which == "api":
                    ok, msg = diag_api(self.config)
                    self._diag_push(ctrl, {"test": "api", "phase": "done",
                                           "ok": ok, "msg": msg})
                elif which == "paste":
                    ok, msg = diag_paste()
                    self._diag_push(ctrl, {"test": "paste", "phase": "done",
                                           "ok": ok, "msg": msg})
            except Exception as e:
                log_exc("diagnostic %r failed" % which)
                self._diag_push(ctrl, {"test": which, "phase": "done", "ok": False,
                                       "msg": "Test couldn't run: " + _redact(str(e))})
        threading.Thread(target=work, daemon=True).start()

    def _diag_hotkey(self, timeout=6.0):
        """Wait up to `timeout` s for the user to press a voice recording hotkey,
        reusing the live listener via the _diag_hotkey_waiting flag. Returns
        (ok, message)."""
        try:
            import permissions
            status = permissions.input_monitoring_status()
        except Exception:
            status = "unknown"
        if status == "denied":
            return (False, "Input Monitoring is off, so Yap can’t see your keys. "
                    "Grant it in System Settings → Privacy & Security → Input "
                    "Monitoring, then quit and reopen Yap.")
        self._diag_hotkey_result = None
        self._diag_hotkey_event.clear()
        self._diag_hotkey_waiting = True
        try:
            got = self._diag_hotkey_event.wait(timeout)
        finally:
            self._diag_hotkey_waiting = False
        if got and self._diag_hotkey_result:
            return (True, "Detected your “" + self._diag_hotkey_result +
                    "” button — the hotkey works.")
        hint = ("Didn’t catch a hotkey. Hold Right Option (⌥) and tap an arrow "
                "that’s assigned to a voice button.")
        if status != "granted":
            hint += (" If nothing is detected, grant Input Monitoring and quit "
                     "and reopen Yap.")
        return (False, hint)

    def _mark_onboarded(self):
        """Persist that the welcome flow has been completed, so it doesn't
        reappear (notably after the Input Monitoring quit-and-reopen)."""
        if not self.config.get("onboarding_complete"):
            self.config["onboarding_complete"] = True
            self._save_config()

    def _relaunch_app(self):
        """Quit and reopen Yap. Enabling Input Monitoring for an unsigned app
        only takes effect after a relaunch, so the onboarding offers this as the
        final action. When running from the bundle we relaunch it; from source we
        can only quit (the dev restarts manually)."""
        self._mark_onboarded()  # so the reopened app lands in a normal state
        try:
            if getattr(sys, "frozen", False):
                bundle = Path(sys.executable).resolve().parents[2]  # …/Yap.app
                # `open -n` launches a fresh instance after this one exits.
                subprocess.Popen(["open", "-n", str(bundle)])
                log(f"relaunching {bundle}")
            else:
                log("relaunch requested from source; quitting only")
        except Exception:
            log_exc("relaunch failed; quitting anyway")
        rumps.quit_application()

    def _on_onboarding_message(self, action, payload, ctrl):
        if action == "finishOnboarding":
            self._mark_onboarded()
            ctrl.close_window()
        elif action == "saveOnboardingKey" and isinstance(payload, dict):
            # The API-key step: store the chosen provider + model + key, then
            # mark onboarding complete (the user has passed the key step before
            # permissions, so a later quit-and-reopen launches normally).
            try:
                prov = (payload.get("provider") or "gemini").strip()
                prov = prov if prov in ("gemini", "openai") else "gemini"
                model = (payload.get("model") or "").strip() \
                    or DEFAULT_CONFIG["providers"][prov]["model"]
                key = (payload.get("api_key") or "").strip()
                providers = self.config.setdefault("providers", {})
                slot = providers.setdefault(prov, {})
                slot["api_key"] = key
                slot["model"] = model
                self.config["provider"] = prov
                self.config["onboarding_complete"] = True
                self._save_config()
                log(f"onboarding: saved {prov} key + model {model}")
                ctrl.push_js("yapKeySaved", True)
            except Exception:
                log_exc("failed to save onboarding API key")
                ctrl.push_js("yapKeySaved", False)
        elif action == "openSettings":
            self.edit_settings(None)
        elif action == "permReady":
            # The permissions step mounted: send current status, then walk the
            # user through whatever is still missing, one prompt at a time.
            self._perm_start_sequence()
        elif action == "requestPermission" and isinstance(payload, dict):
            self._perm_request_one(payload.get("which"))
        elif action == "openPermissionPane" and isinstance(payload, dict):
            # Open the EXACT System Settings privacy pane for this permission.
            try:
                import permissions
                permissions.open_pane(payload.get("which"))
            except Exception:
                log_exc("failed to open permission pane")
        elif action == "relaunchApp":
            self._relaunch_app()
        elif action == "getApiKey":
            # Open the key page for whichever provider the onboarding step has
            # selected — AI Studio for Gemini, the OpenAI dashboard for OpenAI.
            # Opened via the browser (not the web view, which has no nav delegate).
            prov = ""
            if isinstance(payload, dict):
                prov = (payload.get("provider") or "").strip()
            url = OPENAI_KEY_URL if prov == "openai" else AISTUDIO_URL
            try:
                subprocess.run(["open", url], check=False)
            except Exception:
                log_exc("failed to open the provider key page")

    # ----- onboarding permission walk-through ----------------------------

    def _perm_snapshot(self):
        try:
            import permissions
            return permissions.snapshot()
        except Exception:
            log_exc("failed to read permission status")
            return None

    def _perm_push(self, snap):
        if self._onboarding_ctrl is not None and snap is not None:
            self._onboarding_ctrl.push_js("yapPerms", snap)

    def _perm_start_sequence(self):
        """Push current status, then queue the prompts for the missing ones.
        Input Monitoring is LAST because granting it to an unsigned app only
        takes effect after a quit-and-reopen, so it's the final step the user
        finishes with."""
        snap = self._perm_snapshot()
        self._perm_last_snapshot = snap
        self._perm_push(snap)
        if snap is None:
            return
        order = ["microphone", "accessibility", "inputMonitoring"]
        self._perm_queue = [k for k in order if snap.get(k) != "granted"]
        self._perm_active = None
        self._perm_advance = False
        self._perm_pump()

    def _perm_pump(self):
        """Fire the next queued prompt (main thread). Mic resolves via its
        callback; the others are observed via polling in _tick."""
        if self._perm_active is not None or not self._perm_queue:
            return
        which = self._perm_queue.pop(0)
        snap = self._perm_snapshot() or {}
        if snap.get(which) == "granted":
            self._perm_pump()  # already granted in the meantime — skip ahead
            return
        self._perm_active = which
        self._perm_deadline = time.time() + 30  # don't stall the chain forever
        try:
            import permissions
            permissions.request(which, mic_callback=self._on_mic_result)
            log(f"onboarding: requested {which} permission")
        except Exception:
            log_exc(f"failed to request {which} permission")
            self._perm_active = None
            self._perm_pump()

    def _perm_request_one(self, which):
        """Re-prompt a single permission from a per-row button (best effort)."""
        if which not in ("microphone", "inputMonitoring", "accessibility"):
            return
        try:
            import permissions
            permissions.request(which, mic_callback=self._on_mic_result)
        except Exception:
            log_exc(f"failed to re-request {which} permission")

    def _on_mic_result(self, granted):
        # Runs on a background queue — only touch a plain attribute here; _tick
        # advances the sequence on the main thread.
        self._perm_advance = True

    def _perm_tick(self):
        """Called from _tick while onboarding is open: keep the checklist live
        and advance the prompt sequence as each permission is granted."""
        snap = self._perm_snapshot()
        if snap is None:
            return
        if snap != self._perm_last_snapshot:
            self._perm_last_snapshot = snap
            self._perm_push(snap)
        if self._perm_active is not None:
            done = (snap.get(self._perm_active) == "granted"
                    or self._perm_advance
                    or time.time() >= self._perm_deadline)
            if done:
                self._perm_advance = False
                self._perm_active = None
                self._perm_pump()

    # ----- quick panel ----------------------------------------------------

    def _ensure_panel(self):
        if self._panel is not None:
            return
        try:
            import webui
            self._panel = webui.open_panel(
                resource_path("web", "panel.html"), resource_path("web"),
                size=(344, 500), on_message=self._on_panel_message)
        except Exception:
            log_exc("failed to create quick panel")
            self._panel = None

    def _panel_anchor(self):
        """Screen rect (x, y, w, h) of the menu-bar status item, or None."""
        try:
            btn = self._nsapp.nsstatusitem.button()
            fr = btn.window().frame()
            return (fr.origin.x, fr.origin.y, fr.size.width, fr.size.height)
        except Exception:
            return None

    def _panel_state(self, mode=None):
        modes = self.config.get("modes", [])
        return {
            "mode": mode or self._state,
            "modes": [{"name": m.get("name", ""), "hotkey": m.get("hotkey", ""),
                       "input": m.get("input", "audio")} for m in modes],
            "lastYap": self.last_yap or "",
            # The minimal "Listening…" chip vs. the full panel, and which trigger
            # mode is live (so the hint can say "release" vs "tap to stop").
            "compact": bool(self._panel_compact),
            "trigger": self._active_trigger_mode,
        }

    def _show_panel(self):
        if self._panel is None:
            return
        # The full panel: size to fit the content, then anchor under the status
        # item. Always full-size (manual opens force this), so clear the chip flag.
        self._panel_compact = False
        rows = len(self.config.get("modes", []))
        height = 232 + rows * 58 + (132 if self.last_yap else 0)
        self._panel.resize_to(344, height)
        self._panel.push_js("yapPanel", self._panel_state())
        self._panel.show_at(self._panel_anchor())

    def _show_panel_compact(self):
        """The minimal recording indicator: a small "Listening…" chip anchored by
        the menu-bar icon, so the rest of the screen stays usable. Reuses the same
        non-activating panel window (just resized small); the web side hides the
        panel chrome when state.compact is true."""
        if self._panel is None:
            return
        self._panel_compact = True
        self._panel.resize_to(184, 96)
        self._panel.push_js("yapPanel", self._panel_state())
        self._panel.show_at(self._panel_anchor())

    def _install_icon_click(self):
        """Make a normal click on the menu-bar icon open the quick panel instead
        of the dropdown menu. The menu is detached from the status item and shown
        only on right/Control-click, so every action (Quit included) stays
        reachable. Runs once, on the main thread, after the run loop is up."""
        try:
            statusitem = self._nsapp.nsstatusitem
            button = statusitem.button()
            if button is None:
                return  # run loop not fully up yet; try again next tick
            import webui
            self._statusmenu = statusitem.menu()  # capture the live NSMenu
            statusitem.setMenu_(None)             # detach so clicks fire our action
            webui.install_status_click(button, self.toggle_panel_click,
                                       self._show_status_menu)
            self._force_statusitem_visible(statusitem)
            self._click_installed = True
            log("menu-bar icon: left-click → quick panel, right-click → menu")
            # Record what the status item actually looks like to macOS. When
            # someone reports "Yap is running but there's no icon", this line is
            # the difference between diagnosing it and guessing.
            try:
                log("menu-bar icon state: visible=%s autosave=%r button=%s"
                    % (statusitem.isVisible(), statusitem.autosaveName(),
                       statusitem.button() is not None))
            except Exception:
                log("menu-bar icon state: could not be read")
        except Exception:
            # Leave _click_installed False so we retry; if it never succeeds the
            # menu simply stays as-is (nothing is lost).
            log_exc("failed to install icon click handler")

    # ----- menu-bar icon health ------------------------------------------
    #
    # Yap can be running perfectly with no icon in the menu bar, and then there
    # is no way to reach Settings or Quit. The cause is in rumps: it creates the
    # status item and never calls setVisible_, and never gives it an autosave
    # name. When the menu bar is full — routine on a laptop with a notch — macOS
    # hides the item and REMEMBERS that, so the icon does not come back on later
    # launches either. rumps 0.4.0 is the last release there will be, so this is
    # fixed here rather than upstream.

    STATUSITEM_AUTOSAVE = "YapStatusItem"

    def _force_statusitem_visible(self, statusitem=None):
        """Insist the status item is visible, and give it a name macOS can
        remember it by. Safe to call repeatedly."""
        try:
            if statusitem is None:
                statusitem = self._nsapp.nsstatusitem
            if statusitem is None:
                return False
            # Give the item a name of our own. macOS does NOT leave this empty —
            # it assigns a positional default ("Item-0"), which is shared with
            # every other app that never named its item, so the position and
            # visibility macOS remembers under it are not reliably ours. Set our
            # name unconditionally; checking "is it empty first" never fires.
            try:
                if statusitem.autosaveName() != self.STATUSITEM_AUTOSAVE:
                    statusitem.setAutosaveName_(self.STATUSITEM_AUTOSAVE)
            except Exception:
                pass  # older macOS, or rumps handed us something unexpected
            try:
                if not statusitem.isVisible():
                    statusitem.setVisible_(True)
                    log("the menu-bar icon was hidden — made it visible again")
            except Exception:
                pass
            return bool(statusitem.button() is not None)
        except Exception:
            log_exc("could not check the menu-bar icon")
            return False

    def _statusitem_check(self):
        """Watchdog for the menu-bar icon, called from _tick.

        Re-asserts visibility (macOS can hide the item at any point, not only at
        launch), and if the icon still isn't there a few seconds in, says so —
        once — instead of leaving a running app with no way to reach it."""
        now = time.time()
        if now < self._statusitem_next_check:
            return
        self._statusitem_next_check = now + 1.0
        try:
            statusitem = getattr(self._nsapp, "nsstatusitem", None)
        except Exception:
            statusitem = None

        if statusitem is None or statusitem.button() is None:
            # The item never got created, or has gone away entirely.
            if not self._statusitem_warned and now > self._statusitem_deadline:
                self._statusitem_warned = True
                log("MENU-BAR ICON MISSING: the status item does not exist.")
                self._warn_no_icon()
            return

        visible = self._force_statusitem_visible(statusitem)
        try:
            visible = visible and bool(statusitem.isVisible())
        except Exception:
            pass

        if visible:
            self._statusitem_ready = True
            return
        # Still not showing after the grace period: macOS is refusing, which in
        # practice means there is no room left in the menu bar.
        if not self._statusitem_warned and now > self._statusitem_deadline:
            self._statusitem_warned = True
            log("MENU-BAR ICON MISSING: macOS will not show the status item "
                "(the menu bar is most likely full).")
            self._warn_no_icon()

    def _warn_no_icon(self):
        """Tell the user, once per launch, that Yap is running invisibly."""
        try:
            _alert(
                "Yap is running, but its menu-bar icon isn’t showing",
                "macOS won’t give Yap a spot in the menu bar — this normally "
                "means the menu bar is full.\n\n"
                "Make room by holding ⌘ and dragging an icon you don’t need out "
                "of the menu bar, then use “Reset menu bar icon” from Yap’s menu "
                "— or just quit and reopen Yap.\n\n"
                "The hold-to-talk shortcut keeps working either way.")
        except Exception:
            log_exc("could not show the missing-icon warning")

    def reset_status_icon(self, _=None):
        """Menu action: clear the position macOS has remembered for our status
        item and build a fresh one. For when the icon is stuck outside the
        visible menu bar and forcing setVisible_ isn't enough on its own."""
        try:
            from AppKit import NSStatusBar
            old = getattr(self._nsapp, "nsstatusitem", None)
            # Forget the remembered position/visibility. Clear it under BOTH our
            # own autosave name and whatever name the item currently carries —
            # macOS hands out positional defaults like "Item-0", and a stale
            # entry under that name is exactly what strands the icon offscreen.
            names = {self.STATUSITEM_AUTOSAVE}
            try:
                if old is not None and old.autosaveName():
                    names.add(str(old.autosaveName()))
            except Exception:
                pass
            try:
                from Foundation import NSUserDefaults
                d = NSUserDefaults.standardUserDefaults()
                for name in names:
                    for k in ("NSStatusItem Preferred Position %s" % name,
                              "NSStatusItem Visible %s" % name):
                        d.removeObjectForKey_(k)
                d.synchronize()
                log("cleared the saved menu-bar position for: %s"
                    % ", ".join(sorted(names)))
            except Exception:
                log_exc("could not clear the saved menu-bar position")
            bar = NSStatusBar.systemStatusBar()
            if old is not None:
                bar.removeStatusItem_(old)
            item = bar.statusItemWithLength_(-1)
            self._nsapp.nsstatusitem = item
            # Rebuild what the old item carried: icon, title and click routing.
            self._click_installed = False
            self._statusitem_warned = False
            self._statusitem_deadline = time.time() + 5.0
            self._nsapp.setStatusBarIcon()
            self._nsapp.setStatusBarTitle()
            item.setMenu_(self._statusmenu)
            self._force_statusitem_visible(item)
            self._install_icon_click()
            log("rebuilt the menu-bar icon from scratch")
            notify("Yap", "Menu-bar icon reset", "")
        except Exception:
            log_exc("could not reset the menu-bar icon")
            notify("Yap", "Could not reset the menu-bar icon",
                   "Quit and reopen Yap instead.")

    def toggle_panel_click(self):
        self.toggle_panel(None)

    def _show_status_menu(self):
        """Show the full menu under the icon (right/Control-click)."""
        try:
            menu = self._statusmenu
            button = self._nsapp.nsstatusitem.button()
            if menu is not None and button is not None:
                menu.popUpMenuPositioningItem_atLocation_inView_(None, (0, 0), button)
        except Exception:
            log_exc("failed to show status menu")

    def toggle_panel(self, _):
        self._ensure_panel()
        if self._panel is None:
            return
        if self._panel.is_visible():
            self._panel_autoshown = False
            self._panel.hide_now()
        else:
            self._panel_autoshown = False  # user-opened: keep open until toggled
            self._show_panel()

    def _on_panel_message(self, action, payload, ctrl):
        if action == "ready":
            ctrl.push_js("yapPanel", self._panel_state())
        elif action == "openSettings":
            self.edit_settings(None)
            # Close the pop-up so the user is left with just the Settings window.
            self._panel_autoshown = False
            if self._panel is not None:
                self._panel.hide_now()
        elif action == "quit":
            rumps.quit_application()
        elif action == "copyLast":
            if self.last_yap:
                try:
                    set_clipboard(self.last_yap)
                    log("copied last yap to clipboard from quick panel")
                except Exception:
                    log_exc("copyLast failed")

    def open_config_file(self, _):
        # Opens config.json in the default text editor (a reliable fallback).
        os.system(f'open -t "{CONFIG_PATH}"')

    def restore_defaults(self, _):
        """Replace the modes (names, hotkeys, prompts) with the built-in
        defaults, keeping your API key and model. Use this to pull in improved
        default prompts without hand-editing."""
        self.config["modes"] = [dict(m) for m in DEFAULT_CONFIG["modes"]]
        self._save_config()
        notify("Yap", "Default prompts restored", "")

    def reload_settings(self, _):
        self.config = load_config()
        self._config_mtime = CONFIG_PATH.stat().st_mtime
        self._rebuild_hotkey_maps()
        self._build_menu()
        notify("Yap", "Settings reloaded", "")

    def _rebuild_hotkey_maps(self):
        """Split modes into the two trigger namespaces, both keyed by arrow:
        voice modes for 'Right Option + arrow' (hold-to-talk), and text modes for
        'Right Command + Right Option + arrow' (rewrite the selection)."""
        modes = self.config.get("modes", [])
        self.hotkey_map = {m["hotkey"]: m for m in modes
                           if m.get("input", "audio") != "text"}
        self.text_hotkey_map = {m["hotkey"]: m for m in modes
                                if m.get("input", "audio") == "text"}
        # Arrow virtual-key code -> text mode, for the dedicated suppressor tap.
        # It uses this to SUPPRESS the rewrite arrow so it never reaches the app
        # (which would move/collapse the selection) and to know which mode to fire.
        self._text_arrow_vk_to_mode = {ARROW_VK[k]: m
                                       for k, m in self.text_hotkey_map.items()
                                       if k in ARROW_VK}

    def _save_config(self):
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2)
        self._config_mtime = CONFIG_PATH.stat().st_mtime
        self._rebuild_hotkey_maps()
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
                self._rebuild_hotkey_maps()
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
        # Voice listener: LISTEN-ONLY (Input Monitoring). It must NOT suppress
        # keys, so voice never depends on Accessibility.
        #
        # We run our own tap rather than pynput's because pynput cannot recover
        # when macOS switches a tap off — see QuartzKeyTap. pynput stays as the
        # fallback for a Mac where Quartz somehow isn't importable, but there the
        # old silent-death behaviour is still possible; nothing can be done about
        # that from outside the library.
        if Quartz is not None:
            tap = QuartzKeyTap(self._on_press, self._on_release)
            if tap.start():
                self._keytap = tap
                if QuartzKeyTap.input_monitoring_granted():
                    log("hold-to-talk ACTIVE on Yap's own event tap "
                        "(self-healing if macOS switches it off)")
                    self._keytap_granted = True
                else:
                    # The tap exists but macOS will not feed it. Saying it is
                    # "running" here would be the most misleading line in the
                    # log — this is exactly the state where the icon is there,
                    # nothing errors, and the hotkey does nothing.
                    log("hold-to-talk INACTIVE — the event tap was created but "
                        "Input Monitoring is not granted, so macOS sends it no "
                        "keys. System Settings → Privacy & Security → Input "
                        "Monitoring, switch Yap on; Yap picks it up within a "
                        "few seconds, no restart needed.")
            else:
                log("Yap's own event tap could not start — falling back to pynput")
        if self._keytap is None:
            listener = keyboard.Listener(on_press=self._on_press,
                                         on_release=self._on_release)
            listener.daemon = True
            listener.start()
            self._listener = listener

        # Separate, dedicated tap JUST for the text-rewrite chord. darwin_intercept
        # makes this one an ACTIVE tap (Accessibility — already required to paste)
        # so it can SUPPRESS the trigger key ("/"), keeping it out of the focused
        # app so the selection survives, and fire the rewrite. Kept independent so
        # that if Accessibility isn't granted (tap fails to create) the voice
        # listener above keeps working regardless.
        self._suppressor = None
        self._suppressor_warned = False
        self._suppressor_next_try = 0.0
        self._suppressor_disabled = False
        if self._start_suppressor():
            log("text-rewrite chord ACTIVE (Right ⌘ + Right ⌥ + arrow)")
        else:
            log("text-rewrite chord INACTIVE — needs Accessibility. Grant it in "
                "System Settings → Privacy & Security → Accessibility; Yap picks "
                "it up within a few seconds, no restart needed.")

    def _start_suppressor(self):
        """Create the ACTIVE event tap that powers the text-rewrite chord.

        This tap SUPPRESSES the arrow key, which macOS only permits with
        Accessibility — and it decides that when the tap is created, i.e. at
        launch. So granting Accessibility while Yap is already running leaves
        this tap dead even though pasting (checked per call) works fine: voice
        keeps working and only rewrite is broken.

        pynput signals the failure by quietly returning from its thread
        (_util/darwin.py: `if tap is None: self._mark_ready(); return`) — no
        exception — so the only way to detect it is to check the thread is still
        alive afterwards. Returns True if the tap is live.
        """
        if Quartz is None:
            return False
        try:
            sup = keyboard.Listener(on_press=lambda k: None,
                                    on_release=lambda k: None,
                                    darwin_intercept=self._intercept)
            sup.daemon = True
            sup.start()
            sup.wait()          # returns once the tap is up — or has given up
            time.sleep(0.03)    # let a failed thread finish exiting
            if not sup.is_alive():
                return False
            self._suppressor = sup
            return True
        except Exception:
            log_exc("text-rewrite suppressor tap could not start")
            return False

    def _keytap_check(self):
        """Keep the hold-to-talk tap alive. Called from _tick; cheap.

        Two jobs. If the tap is up, re-enable it should macOS have switched it
        off — the callback normally catches that itself, but only if it is still
        being called, so this is the backstop. If the tap never started (Input
        Monitoring not granted at launch), retry, so granting it later starts
        working without quitting and reopening Yap."""
        if Quartz is None:
            return
        now = time.time()
        if self._keytap is not None:
            # A tap whose run-loop thread has died delivers nothing and can
            # never recover on its own; drop it so the branch below rebuilds it.
            if not self._keytap.is_alive():
                log("the keyboard event tap thread is gone — rebuilding it")
                self._keytap = None
                self._keytap_granted = False
                self._keytap_next_try = 0.0
                return
            # Keep a live tap enabled — this is the recovery that makes the
            # hotkey survive macOS switching the tap off mid-session.
            self._keytap.ensure_enabled()
            # A tap that exists but is starved of events (no Input Monitoring)
            # has to be rebuilt once the permission arrives: macOS does not
            # start feeding an existing tap retroactively.
            if now < self._keytap_next_try:
                return
            self._keytap_next_try = now + 3.0
            granted = QuartzKeyTap.input_monitoring_granted()
            if granted and not self._keytap_granted:
                self._keytap.stop()
                fresh = QuartzKeyTap(self._on_press, self._on_release, quiet=True)
                if fresh.start():
                    self._keytap = fresh
                    self._keytap_granted = True
                    log("hold-to-talk is now ACTIVE (Input Monitoring granted)")
            elif not granted and self._keytap_granted:
                # Revoked while running — say so rather than going quiet.
                self._keytap_granted = False
                log("hold-to-talk INACTIVE — Input Monitoring was switched off "
                    "for Yap.")
            return
        if now < self._keytap_next_try:
            return
        self._keytap_next_try = now + 3.0
        # The tap could not be created at all. Retry regardless of what the
        # permission API says: creating a tap is one cheap call, and failing
        # costs nothing.
        tap = QuartzKeyTap(self._on_press, self._on_release, quiet=True)
        if tap.start():
            self._keytap = tap
            self._keytap_granted = QuartzKeyTap.input_monitoring_granted()
            if self._keytap_granted:
                log("hold-to-talk is now ACTIVE")

    def _suppressor_check(self):
        """Retry the rewrite tap while it's down, so granting Accessibility
        later starts working without quitting and reopening Yap. Called from
        _tick; cheap, and does nothing once the tap is alive."""
        if Quartz is None:
            return
        now = time.time()
        if now < self._suppressor_next_try:
            return
        self._suppressor_next_try = now + 3.0
        if (self._suppressor is not None and self._suppressor.is_alive()
                and not self._suppressor_disabled):
            return
        if self._suppressor_disabled and self._suppressor is not None:
            # A tap macOS switched off keeps its thread alive, so "is it alive?"
            # can't tell us anything here. Tear it down and build a fresh one.
            try:
                self._suppressor.stop()
            except Exception:
                pass
            self._suppressor = None
            self._suppressor_disabled = False
        try:
            import permissions
            if permissions.accessibility_status() != "granted":
                if not self._suppressor_warned:
                    log("text-rewrite chord inactive: Accessibility not granted")
                    self._suppressor_warned = True
                return
        except Exception:
            return
        if self._start_suppressor():
            log("text-rewrite chord is now ACTIVE (Accessibility granted)")
            self._suppressor_warned = False

    def _intercept(self, event_type, event):
        """Event-tap filter for the dedicated suppressor tap (macOS). Return None
        to swallow the event, or the event to let it through. We act ONLY on a
        text-mode arrow pressed while RIGHT Command + RIGHT Option are held:
        swallow it (so the arrow never reaches the app and moves/collapses the
        selection, and so the voice tap never sees it) and fire the rewrite.
        Everything else passes through untouched. Must never raise: on any error
        we return the event so a bug here can't eat the user's typing."""
        try:
            # macOS switching this tap off. We can't re-enable it in place —
            # pynput owns the tap handle and doesn't expose it — so flag it and
            # let _suppressor_check rebuild the listener on its next pass.
            if event_type in (Quartz.kCGEventTapDisabledByTimeout,
                              Quartz.kCGEventTapDisabledByUserInput):
                self._suppressor_disabled = True
                log("macOS switched the text-rewrite tap off — rebuilding it")
                return event
            vk_to_mode = getattr(self, "_text_arrow_vk_to_mode", None)
            if not vk_to_mode:
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode)
            mode = vk_to_mode.get(keycode)
            if mode is None:
                return event  # not one of our rewrite arrows
            flags = Quartz.CGEventGetFlags(event)
            right_cmd = flags & NX_DEVICERCMDKEYMASK
            right_alt = flags & NX_DEVICERALTKEYMASK
            if not (right_cmd and right_alt):
                # Diagnostic: a rewrite arrow with generic ⌘⌥ but not the right-
                # side bits (e.g. left-hand combo, or a keyboard that doesn't
                # report sides). Logged so we can relax the requirement if needed.
                if (flags & Quartz.kCGEventFlagMaskCommand
                        and flags & Quartz.kCGEventFlagMaskAlternate):
                    log("rewrite arrow seen without right-side ⌘⌥ bits; flags=",
                        hex(int(flags)), "(left-hand combo passes through)")
                return event
            # Our chord. Fire on key-DOWN only (the listen-only voice tap never
            # sees this arrow since we suppress it here). Re-resolve mode after any
            # settings reload, then swallow both down and up.
            if event_type == Quartz.kCGEventKeyDown \
                    and not self.recording and not self._text_busy:
                self._maybe_reload()
                mode = getattr(self, "_text_arrow_vk_to_mode", {}).get(keycode, mode)
                self._trigger_text_mode(mode)
            return None  # suppress — keep the selection intact for Cmd+C
        except Exception:
            log_exc("text-rewrite intercept error")
        return event

    def _on_press(self, key):
        """Guarded entry point for a key press.

        The guard is the point: under pynput, an exception raised in here does
        not just lose one keystroke — pynput's _emitter catches it, calls stop()
        on the listener, and the hotkey is dead for the rest of the session with
        nothing logged. QuartzKeyTap guards its own callback too; this covers the
        pynput fallback and anything that calls these handlers directly."""
        try:
            self._on_press_impl(key)
        except Exception:
            log_exc("error handling a key press (ignored)")

    def _on_release(self, key):
        """Guarded entry point for a key release. See _on_press."""
        try:
            self._on_release_impl(key)
        except Exception:
            log_exc("error handling a key release (ignored)")

    def _on_press_impl(self, key):
        # Every special key, when debugging. This used to log unconditionally,
        # which by itself grew yap.log past 3 MB in ordinary use and buried the
        # lines that matter. Set YAP_DEBUG_KEYS=1 to get it back.
        if DEBUG_KEYS and isinstance(key, keyboard.Key):
            log("key pressed:", key)
        # Track modifier state. Command distinguishes the rewrite gesture (⌘⌥ +
        # arrow) from voice (⌥ + arrow), so we never start a recording when ⌘ held.
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl_down = True
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self.alt_down = True
        elif key in (keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            self.cmd_down = True
        # Track the activator (Right Option) being held.
        if key == self._activator:
            self.option_down = True
            # TOGGLE mode: a standalone Right Option tap while a toggle recording
            # is live stops it. _toggle_armed guards this so the START gesture's
            # own Option-down (recording still False) never trips it.
            if self._toggle_armed and self.recording:
                self._toggle_armed = False
                self._finish_recording()
            return
        # Text-rewrite (Right ⌘ + Right ⌥ + arrow) is handled entirely by the
        # separate suppressor tap in _intercept(), which swallows the arrow so it
        # never reaches here or the app. We do NOT trigger it here.
        # An arrow tapped WHILE the activator is held (and Command is NOT) starts
        # recording. The cmd_down guard makes Command the clean switch between
        # "speak" (no ⌘) and "rewrite" (⌘ held).
        if not self.option_down or self.recording or self.cmd_down:
            return
        arrow = self._arrow_names.get(key)
        if arrow is None:
            return
        self._maybe_reload()  # pick up any settings saved since last time
        mode = self.hotkey_map.get(arrow)
        if mode is None:
            return
        # Settings "Test hotkey" is listening: record that we saw a valid voice
        # gesture and stop here — do NOT start a real recording during the test.
        if self._diag_hotkey_waiting:
            self._diag_hotkey_result = mode["name"]
            self._diag_hotkey_event.set()
            return
        self.recording = True
        self.active_mode = mode
        # Snapshot the trigger mode at start, so changing the setting mid-recording
        # can't strand a live recording. _maybe_reload() above already applied any
        # just-saved setting. Arming for toggle happens on the first Option release.
        self._active_trigger_mode = self.config.get("trigger_mode", "hold")
        self._toggle_armed = False
        # Opening the microphone must NOT happen here. This method runs on the
        # CGEventTap callback thread, and macOS disables any event tap whose
        # callback is slow — opening a CoreAudio input stream takes hundreds of
        # milliseconds, more on newer macOS where the permission check sits on
        # the same path. That is how the hotkey used to die permanently: the tap
        # went silent, nothing raised, nothing was logged. Hand the slow part to
        # a worker and return immediately.
        self._record_start = time.time()
        self._set_state("recording")  # _tick shows the live countdown
        tail = ("— tap Right Option again to stop"
                if self._active_trigger_mode == "toggle"
                else "— keep holding Right Option, release it to finish")
        log("RECORDING started for mode:", mode["name"],
            "trigger:", self._active_trigger_mode, tail)
        threading.Thread(target=self._open_microphone, daemon=True,
                         name="yap-mic-open").start()

    def _open_microphone(self):
        """Open the input stream off the event-tap thread (see _on_press).

        If the device can't be opened we have to undo the recording state that
        _on_press optimistically set."""
        try:
            self.recorder.start(self.config.get("input_device", ""))
        except Exception as e:
            with self._lock:
                if not self.recording:
                    return  # already stopped by a release; nothing to undo
                self.recording = False
                self.active_mode = None
                self._active_trigger_mode = "hold"
                self._toggle_armed = False
            self._record_start = None
            self._set_state("idle")
            log("ERROR starting microphone:", e)
            # Don't fail silently — tell the user, since the most common cause is
            # a Mac with no usable default mic (e.g. a Mac Mini) or an unplugged
            # device. notify() falls back to a log line when run from Terminal.
            notify("Yap couldn't start the microphone",
                   "Check that a mic is connected, then pick it in "
                   "Settings → Microphone.")

    def _on_release_impl(self, key):
        # Clear modifier state.
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl_down = False
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self.alt_down = False
        elif key in (keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            self.cmd_down = False
        # Releasing the activator (Right Option) stops and processes a recording…
        if key != self._activator:
            return
        self.option_down = False
        # …in HOLD mode. In TOGGLE mode, the first release after starting does NOT
        # stop — instead it arms the next standalone Option tap to stop (handled in
        # _on_press). Stray releases (no live recording) fall through harmlessly.
        if self.recording and self._active_trigger_mode == "toggle":
            self._toggle_armed = True
            return
        self._finish_recording()

    def _finish_recording(self):
        """Stop recording and start processing. Safe to call from either the
        key-release handler or the auto-stop timer (whichever happens first)."""
        with self._lock:
            if not self.recording:
                return
            self.recording = False
            # Reset toggle state here so EVERY stop path — hold release, the
            # toggle-stop tap, and the 7-minute auto-stop in _tick — stays
            # consistent (and a later stray Option tap can't re-fire).
            self._toggle_armed = False
            self._active_trigger_mode = "hold"
        self._record_start = None
        mode = self.active_mode
        self.active_mode = None
        self._set_state("working")
        # Closing the stream is a PortAudio call and belongs off this thread for
        # the same reason opening it does (see _on_press): _finish_recording runs
        # on the event-tap callback thread, which must return immediately or
        # macOS disables the tap. The worker now owns stop() as well as the
        # network call.
        threading.Thread(target=self._stop_and_process, args=(mode,),
                         daemon=True, name="yap-process").start()

    def _stop_and_process(self, mode):
        """Close the microphone, then transcribe. Runs on a worker thread."""
        try:
            wav_bytes = self.recorder.stop()
        except Exception:
            # Recorder.stop() already guards PortAudio, so this is a last resort
            # — but a recording that can't be closed must still not strand the
            # UI in the "working" state forever.
            log_exc("ERROR stopping the recorder")
            self._set_state("idle")
            return
        log("RECORDING stopped, audio bytes:", len(wav_bytes))
        self._process(wav_bytes, mode)

    def _process(self, wav_bytes, mode):
        delivered = False  # set when text was pasted, so we show the "done" check
        saved = None       # safety copy on disk; cleared once we no longer need it
        try:
            # Ignore accidental taps (less than ~0.3s of audio).
            if len(wav_bytes) < SAMPLE_RATE * 2 * 0.3:
                log("audio too short, skipping (did the mic record anything?)")
                return
            provider, api_key, model = _active_provider(self.config)
            if not api_key or api_key.startswith("PASTE_YOUR"):
                name = "OpenAI" if provider == "openai" else "Gemini"
                log("no API key set for", provider)
                notify("Yap", f"No {name} API key set",
                       f"Open Settings and paste your {name} API key.")
                return
            # Park the audio on disk BEFORE the network call, so a failed upload
            # can never take the recording with it.
            saved = _save_recording(wav_bytes)
            # Size is logged next to the timing so slow yaps can be told apart
            # at a glance: a big clip is upload time, a small slow one is the
            # provider's own latency.
            log(f"sending to {provider} ({model})… "
                f"{len(wav_bytes) / 1e6:.1f}MB audio "
                f"({len(wav_bytes) / (SAMPLE_RATE * 2):.0f}s)")
            text = transcribe(wav_bytes, mode["prompt"], provider, api_key, model)
            # Log only the length, never the content — the transcript/rewrite
            # is the user's private text and must not be persisted to the log.
            log(f"model returned {len(text)} chars")
            if text:
                paste_text(text)
                log("pasted.")
                self.last_yap = text
                self._set_state("done")  # brief green check, then auto-revert to idle
                delivered = True
                # Transcribed and delivered: drop the safety copy immediately.
                _discard_recording(saved)
                saved = None
            else:
                log("model returned empty text.")
        except requests.HTTPError as e:
            body = _redact(getattr(e.response, "text", "")[:400])
            log("model HTTP error:", _redact(str(e)), body)
            notify("Yap", "Model busy, try again", "The model was overloaded.")
        except OSError as e:
            # Connection died / timed out even after every retry.
            log("model connection error:", _redact(str(e))[:300])
        except Exception as e:
            log_exc("ERROR in _process")
            notify("Yap", "Something went wrong", str(e))
        finally:
            if saved is not None:
                # We got here without delivering text — keep the audio and say
                # so out loud, instead of dropping it silently.
                log(f"RECORDING KEPT (not transcribed): {saved}")
                notify("Yap", "Couldn't reach the model — recording saved",
                       f"Your audio is safe in {saved.parent}. Try again in a moment.")
            if not delivered:
                self._set_state("idle")

    # ----- text modes (selected text → provider → paste back) ------------

    def _trigger_text_mode(self, mode):
        """Start a text-rewrite. Fired from the key listener when the
        Control+Option+<letter> chord matches a text mode. Mirrors the voice
        flow: flip to the 'working' icon and do the work off the main thread."""
        with self._lock:
            if self._text_busy or self.recording:
                return
            self._text_busy = True
        self._set_state("working")
        log("TEXT rewrite triggered for mode:", mode["name"])
        threading.Thread(target=self._process_text, args=(mode,), daemon=True).start()

    def _process_text(self, mode):
        delivered = False  # set when text was pasted, so we show the "done" check
        try:
            # Wait briefly for the user to lift Control/Option, so our simulated
            # Cmd+C isn't mixed with the still-held chord (which would void it).
            deadline = time.time() + 1.0
            while (self.ctrl_down or self.alt_down) and time.time() < deadline:
                time.sleep(0.02)
            time.sleep(0.05)  # small settle after the keys come up
            selected = copy_selection()
            if not selected.strip():
                log("text rewrite: nothing selected — doing nothing")
                notify("Yap", "Nothing selected",
                       "Select some text first, then press the rewrite shortcut.")
                return
            provider, api_key, model = _active_provider(self.config)
            if not api_key or api_key.startswith("PASTE_YOUR"):
                name = "OpenAI" if provider == "openai" else "Gemini"
                log("no API key set for", provider)
                notify("Yap", f"No {name} API key set",
                       f"Open Settings and paste your {name} API key.")
                return
            log(f"text rewrite: sending {len(selected)} chars to {provider} ({model})…")
            text = rewrite(selected, mode["prompt"], provider, api_key, model)
            # Log only the length, never the content — the transcript/rewrite
            # is the user's private text and must not be persisted to the log.
            log(f"model returned {len(text)} chars")
            if text:
                paste_text(text)  # replaces the still-selected text in place
                log("pasted rewritten text.")
                self.last_yap = text
                self._set_state("done")  # brief green check, then auto-revert
                delivered = True
            else:
                log("model returned empty text.")
        except requests.HTTPError as e:
            body = _redact(getattr(e.response, "text", "")[:400])
            log("model HTTP error:", _redact(str(e)), body)
            notify("Yap", "Model busy, try again", "The model was overloaded.")
        except Exception as e:
            log_exc("ERROR in _process_text")
            notify("Yap", "Something went wrong", str(e))
        finally:
            self._text_busy = False
            if not delivered:
                self._set_state("idle")


if __name__ == "__main__":
    # First thing, before anything that could fail: make sure a crash leaves a
    # trace. Everything below this line is diagnosable; nothing above it is.
    install_crash_handlers()
    if os.environ.get("YAP_SELFTEST") == "1":
        log("SELFTEST start. frozen=%r" % getattr(sys, "frozen", False))
        # Exercise BOTH providers' audio + text request paths with a dummy key
        # (expect an HTTP/auth error, which proves requests + TLS + the request
        # shape work; an SSL/conn error would prove they don't).
        selftest_models = {"gemini": "gemini-3.1-flash-lite", "openai": "gpt-4o-audio-preview"}
        for prov, mdl in selftest_models.items():
            try:
                transcribe(b"RIFF....", "hi", prov, "dummy-key", mdl, retries=1)
                log(f"SELFTEST {prov} transcribe: returned without raising")
            except Exception as e:
                log(f"SELFTEST {prov} transcribe raised:", repr(e))
            try:
                rewrite("rough text", "Rewrite this.", prov, "dummy-key", mdl, retries=1)
                log(f"SELFTEST {prov} rewrite: returned without raising")
            except Exception as e:
                log(f"SELFTEST {prov} rewrite raised:", repr(e))
        # the text-mode chord letter lookup (vk 15 = R on macOS)
        log("SELFTEST chord_letter(vk=15) ->",
            chord_letter(keyboard.KeyCode(vk=15, char="r")))
        # Clipboard: the accented-text path that used to come out as mojibake
        # ("è" -> "√®") because pbcopy guessed MacRoman with no LANG set.
        sample = "Questo esempio è così"
        try:
            set_clipboard(sample)
            got = get_clipboard()
            log(f"SELFTEST clipboard round-trip: "
                f"{'OK' if got == sample else 'FAIL'} — got {got!r}")
        except Exception:
            log_exc("SELFTEST clipboard FAIL")
        # The actual Cmd+V keystroke is opt-in: it types into whatever app is
        # frontmost, which is rude to do on every self-test run.
        if os.environ.get("YAP_SELFTEST_PASTE") == "1":
            try:
                paste_text(sample); log("SELFTEST paste OK")
            except Exception:
                log_exc("SELFTEST paste FAIL")
        # CA bundle: must resolve to our stable ~/.yap copy, NOT a path in the
        # purgeable temp dir (the cause of "works again only after a restart").
        try:
            import certifi as _c
            log("SELFTEST certifi.where() ->", _c.where())
        except Exception:
            log("SELFTEST certifi unavailable")
        try:
            b = _refresh_ca_bundle(force=True)
            log(f"SELFTEST CA bundle: {b} exists={os.path.exists(b) if b else False}")
        except Exception:
            log_exc("SELFTEST CA bundle FAIL")
        # Regression test for the bug that made Yap stop working until relaunch:
        # simulate macOS purging the temp dir out from under certifi, then make a
        # real HTTPS call. Reaching the server (any HTTP status) proves TLS still
        # verified; the old build died here with OSError and lost the recording.
        try:
            import certifi as _c
            tmp = _c.where()
            purged = False
            if "/var/folders/" in tmp and os.path.exists(tmp):
                os.remove(tmp)
                purged = True
            log(f"SELFTEST simulated temp-dir purge: removed={purged} ({tmp})")
            try:
                transcribe(b"RIFF....", "hi", "gemini", "dummy-key",
                           "gemini-3.1-flash-lite", retries=1)
                log("SELFTEST post-purge HTTPS: reached server (no exception)")
            except requests.HTTPError as e:
                log(f"SELFTEST post-purge HTTPS: OK — reached server "
                    f"({e.response.status_code}), TLS verified after purge")
            except Exception as e:
                log(f"SELFTEST post-purge HTTPS: FAIL — {e.__class__.__name__}: "
                    f"{_redact(str(e))[:120]}")
        except Exception:
            log_exc("SELFTEST purge simulation FAIL")
        # Recording safety net: save, then discard, leaving nothing behind.
        try:
            f = _save_recording(b"RIFF" + b"\0" * 2000)
            log(f"SELFTEST recording saved: {f} exists={f and f.exists()}")
            _discard_recording(f)
            log(f"SELFTEST recording discarded: exists={f and f.exists()}")
            _sweep_old_recordings()
        except Exception:
            log_exc("SELFTEST recording FAIL")
        # Opt-in live round-trip: real recording -> provider -> clipboard, and
        # real selection -> provider -> clipboard, checking accents survive the
        # whole way. Off by default because it spends API credit.
        if os.environ.get("YAP_SELFTEST_E2E") == "1":
            try:
                import wave as _wave
                cfg = load_config()
                prov, key, mdl = _active_provider(cfg)
                clip = os.environ.get("YAP_SELFTEST_WAV", "")
                if clip and os.path.exists(clip):
                    with _wave.open(clip, "rb") as w:
                        frames = w.readframes(w.getnframes())
                    buf = io.BytesIO()
                    with _wave.open(buf, "wb") as o:
                        o.setnchannels(CHANNELS); o.setsampwidth(2)
                        o.setframerate(SAMPLE_RATE); o.writeframes(frames)
                    said = transcribe(
                        buf.getvalue(),
                        "Transcribe exactly as spoken. Output only the transcript.",
                        prov, key, mdl)
                    set_clipboard(said)
                    got = get_clipboard()
                    log(f"SELFTEST E2E voice: transcript={said!r} "
                        f"clipboard={got!r} exact={got == said} "
                        f"mojibake={'√' in got}")
                fixed = rewrite(
                    "questo esempio e cosi, perche e gia cosi",
                    "Fix the Italian accents. Output only the corrected text.",
                    prov, key, mdl)
                set_clipboard(fixed)
                got = get_clipboard()
                log(f"SELFTEST E2E rewrite: result={fixed!r} "
                    f"clipboard={got!r} exact={got == fixed} "
                    f"mojibake={'√' in got}")
            except Exception:
                log_exc("SELFTEST E2E FAIL")
        log("SELFTEST done")
        sys.exit(0)
    log_environment()
    startup_repair()
    if another_copy_is_running():
        # Two copies with the same bundle id both grab the hotkeys, and macOS
        # only honours the permissions granted to ONE of them — the classic
        # "I allowed everything and it still does nothing". Refuse to be the
        # second one rather than fight.
        log("ANOTHER COPY OF YAP IS ALREADY RUNNING — quitting this one.")
        try:
            # Popen, not run: this process exits immediately and lets the
            # dialog outlive it. "giving up after" makes sure an unattended
            # Mac is never left with a modal stuck on screen.
            subprocess.Popen(
                ["osascript", "-e",
                 'display alert "Yap is already running" message "Another copy of '
                 'Yap is already open, and two copies fight over the keyboard '
                 'shortcuts. Quit the other one (its icon is in the menu bar), '
                 'then open this one again." as critical giving up after 60'])
        except Exception:
            pass
        sys.exit(0)
    log("starting. Hold Right Option (⌥) and speak. Watch these lines while you test.")
    YapApp().run()
