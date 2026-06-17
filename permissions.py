#!/usr/bin/env python3
"""
macOS permission detection + prompting for Yap.

Yap needs three system permissions to work end-to-end:

  • Microphone       — to record your voice (AVFoundation).
  • Input Monitoring — to detect the hold-to-talk hotkey (Core Graphics
                       "listen event" access; this is what pynput relies on).
  • Accessibility    — to paste the result (simulate Cmd+V) into the focused
                       app (AXIsProcessTrusted…).

Everything here is isolated from the rest of the app and defensively wrapped:
if a framework symbol is missing on some macOS version, the functions degrade
to "unknown" / no-op rather than crashing onboarding. Detection returns one of
"granted" | "denied" | "unknown" — treat anything other than "granted" as
"still needs the user", and only "denied" as "we already asked and were turned
down" (so the UI can show a Settings shortcut).

All of these calls are safe on a background thread EXCEPT the request_* prompts,
which present UI and should be called on the main thread.
"""

import subprocess

# Three permission keys used throughout the app + web UI.
MICROPHONE = "microphone"
INPUT_MONITORING = "inputMonitoring"
ACCESSIBILITY = "accessibility"

ACCESSIBILITY_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)


# Lazy logging — importing yap at module load would be circular (yap imports us).
def _log(msg):
    try:
        import yap
        yap.log(msg)
    except Exception:
        pass


def _log_exc(prefix):
    try:
        import yap
        yap.log_exc(prefix)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Microphone (AVFoundation)
# ---------------------------------------------------------------------------

def microphone_status():
    # AVAuthorizationStatus: 0 notDetermined, 1 restricted, 2 denied, 3 authorized.
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        st = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        if st == 3:
            return "granted"
        if st in (1, 2):
            return "denied"
        return "unknown"  # notDetermined → we can still prompt
    except Exception:
        _log_exc("microphone_status failed")
        return "unknown"


def request_microphone(callback):
    """Fire the Microphone prompt. `callback(granted: bool)` runs on a background
    queue when the user responds — the caller must marshal back to the main
    thread before touching any UI."""
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

        def handler(granted):
            try:
                callback(bool(granted))
            except Exception:
                _log_exc("microphone callback failed")

        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, handler)
    except Exception:
        _log_exc("request_microphone failed")
        try:
            callback(False)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Input Monitoring (Core Graphics "listen event" access — same permission pynput
# uses for the global hotkey listener).
# ---------------------------------------------------------------------------

def input_monitoring_status():
    try:
        from Quartz import CGPreflightListenEventAccess
        return "granted" if CGPreflightListenEventAccess() else "denied"
    except Exception:
        # Fall back to IOKit's HID access check if the CG symbol is unavailable.
        try:
            from Quartz import (IOHIDCheckAccess, kIOHIDRequestTypeListenEvent,
                                kIOHIDAccessTypeGranted, kIOHIDAccessTypeDenied)
            st = IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)
            if st == kIOHIDAccessTypeGranted:
                return "granted"
            if st == kIOHIDAccessTypeDenied:
                return "denied"
            return "unknown"
        except Exception:
            _log_exc("input_monitoring_status failed")
            return "unknown"


def request_input_monitoring():
    """Show the Input Monitoring prompt. Returns immediately (the dialog is
    non-blocking); the granted state is observed later via polling."""
    try:
        from Quartz import CGRequestListenEventAccess
        return bool(CGRequestListenEventAccess())
    except Exception:
        try:
            from Quartz import IOHIDRequestAccess, kIOHIDRequestTypeListenEvent
            return bool(IOHIDRequestAccess(kIOHIDRequestTypeListenEvent))
        except Exception:
            _log_exc("request_input_monitoring failed")
            return False


# ---------------------------------------------------------------------------
# Accessibility (AXIsProcessTrusted…) — needed to simulate Cmd+V paste.
# ---------------------------------------------------------------------------

def accessibility_status():
    try:
        from ApplicationServices import AXIsProcessTrusted
        return "granted" if AXIsProcessTrusted() else "denied"
    except Exception:
        try:
            from HIServices import AXIsProcessTrusted
            return "granted" if AXIsProcessTrusted() else "denied"
        except Exception:
            _log_exc("accessibility_status failed")
            return "unknown"


def request_accessibility_prompt():
    """Use the official trusted-process prompt so macOS shows its dialog and adds
    Yap to the Accessibility list. Returns the current trusted state (the user
    flips the toggle out-of-process; observed later via polling)."""
    try:
        try:
            from ApplicationServices import (AXIsProcessTrustedWithOptions,
                                             kAXTrustedCheckOptionPrompt)
        except Exception:
            from HIServices import (AXIsProcessTrustedWithOptions,
                                    kAXTrustedCheckOptionPrompt)
        try:
            key = kAXTrustedCheckOptionPrompt
        except Exception:
            key = "AXTrustedCheckOptionPrompt"
        return bool(AXIsProcessTrustedWithOptions({key: True}))
    except Exception:
        _log_exc("request_accessibility_prompt failed")
        return False


def open_accessibility_settings():
    """Open System Settings straight to Privacy → Accessibility."""
    try:
        subprocess.run(["open", ACCESSIBILITY_PANE], check=False)
    except Exception:
        _log_exc("open_accessibility_settings failed")


# ---------------------------------------------------------------------------
# Combined snapshot + request dispatch used by the onboarding flow.
# ---------------------------------------------------------------------------

def snapshot():
    """Current status of all three permissions, as the web UI expects them."""
    return {
        MICROPHONE: microphone_status(),
        INPUT_MONITORING: input_monitoring_status(),
        ACCESSIBILITY: accessibility_status(),
    }


def request(which, mic_callback=None):
    """Trigger the prompt for a single permission by key. `mic_callback` is only
    used for the microphone (its result is asynchronous)."""
    if which == MICROPHONE:
        request_microphone(mic_callback or (lambda _g: None))
    elif which == INPUT_MONITORING:
        request_input_monitoring()
    elif which == ACCESSIBILITY:
        request_accessibility_prompt()
