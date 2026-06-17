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
        real = [f for f in files if not f.startswith(".")]
        if not real:
            continue
        dest = os.path.relpath(dirpath, ".")
        entries.append((dest, [os.path.join(dirpath, f) for f in real]))
    return entries


APP = ["yap.py"]
# Ship the embedded web UI (Settings/onboarding/quick panel) and the menu-bar
# lifecycle icons so they're available inside the bundle's Resources.
DATA_FILES = _tree_data_files("web") + _tree_data_files("assets")
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
        "NSMicrophoneUsageDescription": "Yap records your voice to transcribe it.",
    },
    # sounddevice ships its PortAudio binary inside the _sounddevice_data
    # package; both must stay UNZIPPED (listed here) so the .dylib can be
    # dlopen'd at runtime — dylibs can't load from inside pythonNN.zip.
    # charset_normalizer is requests' (lazily imported) encoding detector;
    # naming it here ensures py2app bundles it so requests is fully functional.
    # WebKit/Foundation/AppKit (PyObjC) power the embedded web-view UI.
    "packages": ["rumps", "pynput", "sounddevice", "_sounddevice_data",
                 "numpy", "requests", "charset_normalizer",
                 "WebKit", "Foundation", "AppKit"],
    # webui.py is imported lazily (inside methods) by yap.py, so name it
    # explicitly to be sure py2app bundles it as a real importable module.
    "includes": ["webui"],
    "iconfile": "yap.icns",
    # Bundle helper dylibs the interpreter loads via @rpath (see above).
    "frameworks": _find_rpath_dylibs(),
}

setup(
    app=APP,
    name="Yap",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
