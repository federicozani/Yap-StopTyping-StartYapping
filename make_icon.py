#!/usr/bin/env python3
"""
Generate a placeholder app icon (parla.icns) for Parla.

This draws a simple microphone glyph on a rounded gradient tile and packages it
into the macOS .icns format. It uses only the standard library + numpy (already
a Parla dependency) plus the built-in macOS tools `sips` and `iconutil`, so
there is nothing extra to install.

Run:
    python3 make_icon.py
Output:
    parla.icns   (referenced by setup_app.py's "iconfile" option)

REPLACING THIS WITH A REAL ICON
-------------------------------
This is only a placeholder. To use your own artwork, either:
  (a) drop a square 1024x1024 PNG named `parla_icon.png` next to this file and
      run `python3 make_icon.py` again (it converts that PNG instead of drawing
      one), or
  (b) replace `parla.icns` directly with your own .icns file.
Then rebuild the app: `python3 setup_app.py py2app`.
"""

import struct
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SIZE = 1024  # master resolution; smaller sizes are downscaled from this.


def write_png(path: Path, rgba: np.ndarray) -> None:
    """Write an HxWx4 uint8 RGBA array to a PNG using only stdlib zlib/struct."""
    h, w, _ = rgba.shape
    rows = rgba.reshape(h, w * 4)
    # Each PNG scanline is prefixed with a filter-type byte (0 = none).
    prefix = np.zeros((h, 1), dtype=np.uint8)
    raw = np.hstack([prefix, rows]).tobytes()
    compressed = zlib.compress(raw, 9)

    def chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", compressed))
        f.write(chunk(b"IEND", b""))


def rounded_rect_mask(h, w, x0, y0, x1, y1, radius):
    """Boolean mask of a filled rounded rectangle within [x0,x1) x [y0,y1).

    Uses the clamped-distance method: a pixel is inside when its distance to the
    rectangle's inner core (the rect shrunk by `radius` on every side) is within
    `radius`. This rounds the corners cleanly and, when radius == half the width,
    naturally produces a capsule shape.
    """
    radius = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
    ys, xs = np.mgrid[0:h, 0:w]
    cx = np.clip(xs, x0 + radius, x1 - 1 - radius)
    cy = np.clip(ys, y0 + radius, y1 - 1 - radius)
    return ((xs - cx) ** 2 + (ys - cy) ** 2) <= radius ** 2


def draw_placeholder() -> np.ndarray:
    """Draw the placeholder: a microphone on a rounded blue→indigo gradient tile."""
    s = SIZE
    img = np.zeros((s, s, 4), dtype=np.uint8)

    # Rounded tile with a vertical gradient (top -> bottom).
    tile = rounded_rect_mask(s, s, int(s * 0.07), int(s * 0.07),
                             int(s * 0.93), int(s * 0.93), radius=int(s * 0.18))
    top = np.array([88, 120, 246])    # bright blue
    bot = np.array([124, 74, 226])    # indigo/violet
    t = (np.arange(s) / (s - 1))[:, None]            # 0..1 down the rows
    grad = (top[None, :] * (1 - t) + bot[None, :] * t).astype(np.uint8)  # s x 3
    img[..., :3] = grad[:, None, :]
    img[..., 3] = np.where(tile, 255, 0)

    white = np.array([245, 247, 255], dtype=np.uint8)
    cx = s // 2

    # Microphone capsule: a vertical rounded bar (the mic body).
    cap = rounded_rect_mask(s, s, cx - int(s * 0.115), int(s * 0.27),
                            cx + int(s * 0.115), int(s * 0.57),
                            radius=int(s * 0.115))
    # Stem below the capsule.
    stem = rounded_rect_mask(s, s, cx - int(s * 0.018), int(s * 0.57),
                             cx + int(s * 0.018), int(s * 0.70),
                             radius=int(s * 0.018))
    # Base bar.
    base = rounded_rect_mask(s, s, cx - int(s * 0.13), int(s * 0.70),
                             cx + int(s * 0.13), int(s * 0.735),
                             radius=int(s * 0.017))
    mic = cap | stem | base
    img[mic] = np.concatenate([white, [255]])
    return img


def master_png():
    """Return (path, is_temp): the user's parla_icon.png if present, else a
    freshly drawn placeholder written to a temp file the caller should delete."""
    user_png = HERE / "parla_icon.png"
    if user_png.exists():
        print(f"Using your artwork: {user_png.name}")
        return user_png, False
    print("No parla_icon.png found — drawing a placeholder microphone icon.")
    tmp = HERE / "._parla_master.png"
    write_png(tmp, draw_placeholder())
    return tmp, True


def build_icns(master: Path) -> Path:
    """Downscale the master PNG into an .iconset and pack it into parla.icns."""
    iconset = HERE / "parla.iconset"
    iconset.mkdir(exist_ok=True)
    # (pixel size, filename) pairs for the standard macOS iconset.
    specs = [
        (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
    ]
    for px, name in specs:
        subprocess.run(
            ["sips", "-z", str(px), str(px), str(master), "--out",
             str(iconset / name)],
            check=True, capture_output=True, text=True,
        )
    icns = HERE / "parla.icns"
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
        check=True, capture_output=True, text=True,
    )
    # Clean up the intermediate iconset.
    for f in iconset.iterdir():
        f.unlink()
    iconset.rmdir()
    return icns


def main():
    master, is_temp = master_png()
    try:
        icns = build_icns(master)
    except FileNotFoundError:
        sys.exit("Error: this script needs macOS's `sips` and `iconutil` tools.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"Error building .icns:\n{e.stderr or e}")
    finally:
        if is_temp and master.exists():
            master.unlink()
    print(f"Wrote {icns.relative_to(HERE)}  ✓")
    print("Now rebuild the app:  python3 setup_app.py py2app")


if __name__ == "__main__":
    main()
