"""
Bundles Parla into a double-clickable Parla.app using py2app.

Run:  python3 setup_app.py py2app
Output: dist/Parla.app
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
    # is not available"); libtcl/libtk -> needed by tkinter (settings window).
    candidates = ["libffi.8.dylib", "libssl.3.dylib", "libcrypto.3.dylib",
                  "libtcl8.6.dylib", "libtk8.6.dylib"]
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


def _tcltk_data_files():
    """Bundle the Tcl/Tk script libraries (init.tcl etc.) so tkinter can
    initialise inside the .app. py2app copies these automatically for
    framework Pythons but not for conda, so we add them as data files under
    Resources/lib/<tclX>. parla.py points TCL_LIBRARY/TK_LIBRARY here at runtime.
    Empty (no-op) when these dirs don't exist (e.g. non-conda Python).
    """
    base = os.path.join(getattr(sys, "base_prefix", sys.prefix), "lib")
    entries = []
    for name in ("tcl8.6", "tk8.6", "tcl8"):
        root = os.path.join(base, name)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            if not files:
                continue
            dest = os.path.join("lib", os.path.relpath(dirpath, base))
            entries.append((dest, [os.path.join(dirpath, f) for f in files]))
    return entries


APP = ["parla.py"]
# settings_gui.py ships alongside so the visual editor is available; the Tcl/Tk
# script libraries are bundled so the Tkinter settings window works in the .app.
DATA_FILES = ["settings_gui.py"] + _tcltk_data_files()
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "LSUIElement": True,  # menu bar only, no Dock icon / no app window
        "CFBundleName": "Parla",
        "NSMicrophoneUsageDescription": "Parla records your voice to transcribe it.",
    },
    # sounddevice ships its PortAudio binary inside the _sounddevice_data
    # package; both must stay UNZIPPED (listed here) so the .dylib can be
    # dlopen'd at runtime — dylibs can't load from inside python312.zip.
    # charset_normalizer is requests' (lazily imported) encoding detector;
    # naming it here ensures py2app bundles it so requests is fully functional.
    "packages": ["rumps", "pynput", "sounddevice", "_sounddevice_data",
                 "numpy", "requests", "charset_normalizer"],
    # settings_gui is imported by parla.py when relaunched in settings mode, so
    # it must be a real importable module inside the bundle (not just a data file).
    "includes": ["tkinter", "settings_gui"],
    "iconfile": "parla.icns",
    # Bundle helper dylibs the interpreter loads via @rpath (see above).
    "frameworks": _find_rpath_dylibs(),
}

setup(
    app=APP,
    name="Parla",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
