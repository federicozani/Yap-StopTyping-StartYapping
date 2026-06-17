"""
Bundles Parla into a double-clickable Parla.app using py2app.

Run:  python3 setup_app.py py2app
Output: dist/Parla.app
"""

from setuptools import setup

APP = ["parla.py"]
# settings_gui.py ships alongside so the visual editor is available.
DATA_FILES = ["settings_gui.py"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "LSUIElement": True,  # menu bar only, no Dock icon / no app window
        "CFBundleName": "Parla",
        "NSMicrophoneUsageDescription": "Parla records your voice to transcribe it.",
    },
    "packages": ["rumps", "pynput", "sounddevice", "numpy", "requests"],
    "includes": ["tkinter"],
}

setup(
    app=APP,
    name="Parla",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
