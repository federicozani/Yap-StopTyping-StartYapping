"""
Bundles Yap into a double-clickable Yap.app using py2app.

Run:  python3 setup_app.py py2app
Output: dist/Yap.app
"""

import os
import sys

from setuptools import setup

# py2app scans every dependency's source with a recursive AST walker. On
# interpreters with a large package environment this can blow Python's default
# recursion limit (RecursionError in modulegraph). Raising it lets the scan
# finish; it has no effect on the built app, only on this build step.
sys.setrecursionlimit(10000)


def _find_rpath_dylibs():
    """Some Python builds (notably Anaconda/conda) link helper libraries like
    libffi via @rpath into the interpreter's lib dir, and py2app doesn't always
    copy them — the bundle then fails to boot ('Library not loaded:
    @rpath/libffi.8.dylib'). Bundling them explicitly as 'frameworks' makes the
    .app self-contained. On Pythons that don't need this (python.org/Homebrew)
    none are found and the list stays empty, so this is a safe no-op there.
    """
    # libffi -> needed by ctypes at boot; libssl/libcrypto -> needed by the ssl
    # module (without them, every HTTPS call to Gemini fails with "the SSL module
    # is not available"). The Settings/onboarding UI is now an embedded WKWebView
    # (WebKit is a system framework), so the old Tcl/Tk dylibs are no longer needed.
    candidates = ["libffi.8.dylib", "libssl.3.dylib", "libcrypto.3.dylib"]
    lib_dirs = [os.path.join(p, "lib")
                for p in {sys.prefix, getattr(sys, "base_prefix", sys.prefix)}]
    found = []
    for name in candidates:
        for d in lib_dirs:
            path = os.path.join(d, name)
            if os.path.exists(path):
                found.append(path)
                break
    return found


def _tree_data_files(folder):
    """Recursively collect every file under `folder` as py2app data_files,
    preserving the relative directory layout inside the bundle's Resources.
    Used to ship the web UI (web/) and the menu-bar state icons (assets/).
    No-op when the folder doesn't exist."""
    entries = []
    if not os.path.isdir(folder):
        return entries
    for dirpath, _dirs, files in os.walk(folder):
        # Never ship a config.json — the user's API key lives only in
        # ~/.yap/config.json and must never be baked into the shared bundle.
        real = [f for f in files if not f.startswith(".") and f != "config.json"]
        if not real:
            continue
        dest = os.path.relpath(dirpath, ".")
        entries.append((dest, [os.path.join(dirpath, f) for f in real]))
    return entries


def _app_version():
    """Read APP_VERSION straight out of yap.py, by text, so the built bundle's
    version always matches what the app shows in Settings. Parsed rather than
    imported: importing yap.py here would drag in rumps/pynput/sounddevice just
    to read one string."""
    import re
    src = open("yap.py", encoding="utf-8").read()
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', src, re.M)
    if not m:
        raise SystemExit("setup_app.py: couldn't find APP_VERSION in yap.py")
    return m.group(1)


APP = ["yap.py"]
# Ship the embedded web UI (Settings/onboarding/quick panel) and the menu-bar
# lifecycle icons so they're available inside the bundle's Resources.
# tools/ ships too: the "Reset Yap completely…" menu item runs
# tools/reset-yap.sh from inside the bundle.
DATA_FILES = (_tree_data_files("web") + _tree_data_files("assets")
              + _tree_data_files("tools"))
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "LSUIElement": True,  # menu bar only, no Dock icon / no app window
        "CFBundleName": "Yap",
        "CFBundleDisplayName": "Yap",
        # A stable identifier so macOS treats every rebuild as the same app
        # (helps it remember the Microphone / Accessibility / Input Monitoring
        # permissions instead of seeing a brand-new app each time). Note: this
        # differs from the old "com.parla.app", so macOS will ask for the three
        # permissions once more on Yap's first launch — expected after the rename.
        "CFBundleIdentifier": "com.yap.app",
        # Both stamped from yap.py's APP_VERSION — this is what Finder's
        # "Get Info" shows, and it must agree with the About pane.
        "CFBundleShortVersionString": _app_version(),
        "CFBundleVersion": _app_version(),
        # numpy and _cffi_backend are compiled for the BUILD machine's CPU, so
        # this bundle only runs on Apple Silicon (see README → Distribution).
        # 11.0 is the first macOS on that hardware, and requiring native
        # execution stops anything launching Yap under Rosetta, where the
        # arm64 extensions would fail to load.
        "LSMinimumSystemVersion": "11.0",
        "LSRequiresNativeExecution": True,
        "NSMicrophoneUsageDescription": "Yap records your voice to transcribe it.",
    },
    # sounddevice ships its PortAudio binary inside the _sounddevice_data
    # package; both must stay UNZIPPED (listed here) so the .dylib can be
    # dlopen'd at runtime — dylibs can't load from inside pythonNN.zip.
    # charset_normalizer is requests' (lazily imported) encoding detector;
    # naming it here ensures py2app bundles it so requests is fully functional.
    # WebKit/Foundation/AppKit (PyObjC) power the embedded web-view UI.
    # AVFoundation/Quartz/ApplicationServices (PyObjC) back the onboarding
    # permission detection + prompts (Microphone / Input Monitoring /
    # Accessibility) in permissions.py.
    "packages": ["rumps", "pynput", "sounddevice", "_sounddevice_data",
                 "numpy", "requests", "charset_normalizer",
                 "WebKit", "Foundation", "AppKit",
                 "AVFoundation", "Quartz", "ApplicationServices"],
    # webui.py and permissions.py are imported lazily (inside methods) by
    # yap.py, so name them explicitly to be sure py2app bundles them as real
    # importable modules.
    "includes": ["webui", "permissions"],
    "iconfile": "yap.icns",
    # Bundle helper dylibs the interpreter loads via @rpath (see above).
    "frameworks": _find_rpath_dylibs(),
}

# ---------------------------------------------------------------------------
# Keep macOS permissions working across rebuilds.
#
# py2app leaves the bundle AD-HOC signed, and an ad-hoc signature's "designated
# requirement" — the identity macOS remembers when you grant a permission — is
# the bundle's CONTENT HASH. It changes on every rebuild, so macOS treats each
# build as a different app and quietly stops honouring Input Monitoring and
# Accessibility: the toggles still read ON in System Settings while the hotkey
# silently does nothing, and only `tccutil reset` + re-granting fixes it.
#
# Re-signing with a stable self-signed certificate makes the designated
# requirement `identifier "com.yap.app" and certificate root = H"…"`, which is
# identical for every future build — so the permissions survive.
#
# Create the certificate once:  tools/make_signing_cert.sh
# If it isn't there, we leave the ad-hoc signature alone and just warn.
# ---------------------------------------------------------------------------

SIGN_CERT_NAME = "Yap Local Dev"
SIGN_KEYCHAIN = os.path.expanduser("~/Library/Keychains/yap-signing.keychain-db")


def _codesign_bundle():
    import subprocess

    app = os.path.join("dist", "Yap.app")
    if not os.path.isdir(app):
        return
    if not os.path.exists(SIGN_KEYCHAIN):
        print("\n*** WARNING: no signing keychain — the app stays ad-hoc signed,\n"
              "*** so macOS will drop Input Monitoring/Accessibility after this\n"
              "*** build. Run tools/make_signing_cert.sh once to fix that.\n")
        return
    try:
        subprocess.run(["security", "unlock-keychain", "-p", "yapdev",
                        SIGN_KEYCHAIN], check=True, capture_output=True)
        out = subprocess.run(["security", "find-certificate", "-c", SIGN_CERT_NAME,
                              "-Z", SIGN_KEYCHAIN],
                             check=True, capture_output=True, text=True).stdout
        sha1 = ""
        for line in out.splitlines():
            if "SHA-1 hash" in line:
                sha1 = line.split()[-1]
                break
        if not sha1:
            print("*** WARNING: signing certificate not found; left ad-hoc signed.")
            return
        subprocess.run(["codesign", "--force", "--deep", "--sign", sha1,
                        "--keychain", SIGN_KEYCHAIN, app],
                       check=True, capture_output=True)
        req = subprocess.run(["codesign", "-d", "-r-", app],
                             capture_output=True, text=True).stderr
        print("\n==> signed with the stable certificate; permissions will persist.")
        for line in (req or "").splitlines():
            if "designated" in line:
                print("    " + line.strip())
    except subprocess.CalledProcessError as e:
        print("*** WARNING: signing failed, app left ad-hoc signed:",
              (e.stderr or b"").decode()[:200] if isinstance(e.stderr, bytes)
              else str(e.stderr)[:200])


setup(
    app=APP,
    name="Yap",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

# py2app has finished writing dist/Yap.app by the time setup() returns.
if "py2app" in sys.argv:
    _codesign_bundle()
