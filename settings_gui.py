#!/usr/bin/env python3
"""
Visual settings editor for Parla (opens in its own window).

Launched by parla.py when you click "Edit prompts & hotkeys…".
Reads and writes the same file the app uses: ~/.parla/config.json

Built with Tkinter, Python's built-in GUI toolkit, so there is nothing extra
to install. It runs as a separate little program from the menu bar app, which
keeps things simple and avoids the two apps fighting over the screen.
"""

import json
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

CONFIG_PATH = Path.home() / ".parla" / "config.json"

# You HOLD Right Option (⌥) and TAP one of these arrows to pick the mode.
# Friendly label <-> the arrow name stored in the config.
HOTKEY_LABELS = {
    "Right Option + ← Left": "left",
    "Right Option + ↑ Up": "up",
    "Right Option + → Right": "right",
    "Right Option + ↓ Down": "down",
}
LABEL_BY_KEY = {v: k for k, v in HOTKEY_LABELS.items()}


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


class SettingsWindow:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.rows = []  # one dict of widgets per mode

        root.title("Parla Settings")
        root.geometry("640x720")

        # --- API key + model ---------------------------------------------
        top = ttk.Frame(root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="Gemini API key").grid(row=0, column=0, sticky="w")
        self.api_var = tk.StringVar(value=self.config.get("api_key", ""))
        ttk.Entry(top, textvariable=self.api_var, width=52).grid(
            row=0, column=1, sticky="we", padx=6)
        ttk.Label(top, text="Model").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.model_var = tk.StringVar(value=self.config.get("model", "gemini-2.5-flash"))
        ttk.Entry(top, textvariable=self.model_var, width=52).grid(
            row=1, column=1, sticky="we", padx=6, pady=(6, 0))
        top.columnconfigure(1, weight=1)

        ttk.Label(root, text="Modes — hold a key and speak:",
                  padding=(12, 6)).pack(anchor="w")

        # --- scrollable list of modes ------------------------------------
        container = ttk.Frame(root)
        container.pack(fill="both", expand=True, padx=12)
        canvas = tk.Canvas(container, highlightthickness=0)
        scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.modes_frame = ttk.Frame(canvas)
        self.modes_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.modes_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        for mode in self.config.get("modes", []):
            self._add_row(mode)

        # --- bottom buttons ----------------------------------------------
        bottom = ttk.Frame(root, padding=12)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="+ Add mode", command=lambda: self._add_row()).pack(side="left")
        ttk.Button(bottom, text="Save", command=self.save).pack(side="right")
        ttk.Button(bottom, text="Cancel", command=root.destroy).pack(side="right", padx=6)

    def _add_row(self, mode=None):
        mode = mode or {"name": "New mode", "hotkey": "left", "prompt": ""}
        frame = ttk.LabelFrame(self.modes_frame, text="", padding=8)
        frame.pack(fill="x", pady=6)

        line = ttk.Frame(frame)
        line.pack(fill="x")
        ttk.Label(line, text="Name").pack(side="left")
        name_e = ttk.Entry(line, width=18)
        name_e.insert(0, mode.get("name", ""))
        name_e.pack(side="left", padx=6)
        ttk.Label(line, text="Hotkey").pack(side="left", padx=(12, 0))
        hk_var = tk.StringVar(
            value=LABEL_BY_KEY.get(mode.get("hotkey", "left"), "Right Option + ← Left"))
        ttk.OptionMenu(line, hk_var, hk_var.get(), *HOTKEY_LABELS.keys()).pack(side="left", padx=6)
        ttk.Button(line, text="Remove", command=lambda: self._remove_row(frame)).pack(side="right")

        ttk.Label(frame, text="Prompt (plain-English instruction for Gemini):").pack(
            anchor="w", pady=(6, 0))
        prompt_t = tk.Text(frame, height=4, wrap="word")
        prompt_t.insert("1.0", mode.get("prompt", ""))
        prompt_t.pack(fill="x")

        self.rows.append({"name": name_e, "hotkey": hk_var, "prompt": prompt_t, "frame": frame})

    def _remove_row(self, frame):
        self.rows = [r for r in self.rows if r["frame"] is not frame]
        frame.destroy()

    def save(self):
        modes = []
        used = set()
        for r in self.rows:
            key = HOTKEY_LABELS[r["hotkey"].get()]
            if key in used:
                messagebox.showerror(
                    "Duplicate hotkey",
                    f"Two modes use {r['hotkey'].get()}. Each hotkey can only be used once.")
                return
            used.add(key)
            modes.append({
                "name": r["name"].get().strip() or "Mode",
                "hotkey": key,
                "prompt": r["prompt"].get("1.0", "end-1c").strip(),
            })
        if not modes:
            messagebox.showerror("No modes", "Add at least one mode.")
            return

        self.config["api_key"] = self.api_var.get().strip()
        self.config["model"] = self.model_var.get().strip() or "gemini-2.5-flash"
        self.config["modes"] = modes
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2)
        messagebox.showinfo("Saved", "Settings saved. Parla picks them up automatically.")
        self.root.destroy()


def main():
    root = tk.Tk()
    SettingsWindow(root)
    # Bring the window to the front (it's launched from a background menu-bar app).
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()


if __name__ == "__main__":
    main()
