#!/usr/bin/env python3
"""
Generate Yap's artwork from the design tokens + fish geometry in design/.

Produces:
  * assets/menubar-idle.png       — monochrome TEMPLATE fish (macOS tints it,
                                     so it looks right on light and dark bars)
  * assets/menubar-recording.png  — white fish on the recording gradient tile
  * assets/menubar-working.png    — white fish on the purple gradient tile + bubbles
  * assets/menubar-done.png       — white fish on brand gradient + green check
  * assets/app-logo.png           — 512px in-app logo (squircle + fish)
  * yap.icns                      — the macOS app icon (squircle, brand gradient,
                                     sonar rings, fish), built via iconutil

The menu-bar fish is drawn from the SVG geometry in design/DESIGN-SPEC.md
(viewBox 0 0 128 104). Colors come from design/design-tokens.json.

Run:  python3 make_icon.py
Needs: Pillow + numpy (build-time only — the generated PNG/icns are committed,
so the running app never imports this file).
"""

import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
SS = 4  # supersampling factor for crisp anti-aliased edges

# ---- colors (from design/design-tokens.json) ------------------------------
BRAND_PURPLE = "#9B45E8"
BRAND_PINK = "#FF5BA6"
APP_STOPS = ["#A347EC", "#C247CB", "#FF5BA6"]   # app-icon 3-stop diagonal
REC_FROM, REC_TO = "#E0479A", "#FF5BA6"          # status.recordingGradient
WORK_FROM, WORK_TO = "#7C46E8", "#B048D0"        # working chip gradient
SUCCESS_GREEN = "#28C840"
FISH_EYE = "#3A1155"
TAIL_PINK = "#FF8EC4"


def hx(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------------------
# Gradients (numpy → PIL)
# ---------------------------------------------------------------------------

def linear_gradient(size, stops, angle_deg):
    """stops = list of hex colors evenly spaced along `angle_deg`."""
    w = h = size
    ang = math.radians(angle_deg)
    # projection of each pixel onto the gradient axis, normalised to 0..1
    xs = np.linspace(0, 1, w)[None, :].repeat(h, 0)
    ys = np.linspace(0, 1, h)[:, None].repeat(w, 1)
    t = xs * math.cos(ang) + ys * math.sin(ang)
    t = (t - t.min()) / (t.max() - t.min())
    cols = np.array([hx(s) for s in stops], dtype=float)
    pos = np.linspace(0, 1, len(cols))
    out = np.zeros((h, w, 3))
    for c in range(3):
        out[..., c] = np.interp(t, pos, cols[:, c])
    return Image.fromarray(out.astype("uint8"), "RGB")


# ---------------------------------------------------------------------------
# The fish glyph (design viewBox 0 0 128 104)
# ---------------------------------------------------------------------------

def _fish_layer(side, color, with_eye, eye_dark=(58, 17, 85), tail_tint=None):
    """Render the fish into a transparent `side`x`side` RGBA image at SS scale.

    color    : (r,g,b) fill for the body+tail.
    with_eye : 'hole' (transparent eye, for the template glyph), 'dark' (filled
               dark eye + white highlight), or None.
    """
    S = side * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Fit the 128x104 glyph into the square with a little padding, centered.
    pad = 0.10
    fw = S * (1 - 2 * pad)
    fh = fw * 104 / 128
    ox = (S - fw) / 2
    oy = (S - fh) / 2

    def P(x, y):
        return (ox + x / 128 * fw, oy + y / 104 * fh)

    body = color
    tail = tail_tint or color

    # Tail (triangle pointing into the body from the left).
    d.polygon([P(34, 52), P(8, 33), P(8, 71)], fill=tail + (255,))
    # Body (ellipse).
    bx0, by0 = P(24, 22)
    bx1, by1 = P(108, 82)
    d.ellipse([bx0, by0, bx1, by1], fill=body + (255,))
    # Small rounded mouth nub at the right tip.
    mx0, my0 = P(104, 47)
    mx1, my1 = P(116, 58)
    d.ellipse([mx0, my0, mx1, my1], fill=body + (255,))

    if with_eye == "hole":
        # Punch a transparent hole so the eye reads as negative space (template).
        ex0, ey0 = P(79, 36)
        ex1, ey1 = P(97, 54)
        d.ellipse([ex0, ey0, ex1, ey1], fill=(0, 0, 0, 0))
    elif with_eye == "dark":
        ex0, ey0 = P(79, 36)
        ex1, ey1 = P(97, 54)
        d.ellipse([ex0, ey0, ex1, ey1], fill=eye_dark + (255,))
        # white highlight dot
        hx0, hy0 = P(83, 39)
        hx1, hy1 = P(89, 45)
        d.ellipse([hx0, hy0, hx1, hy1], fill=(255, 255, 255, 255))

    return img.resize((side, side), Image.LANCZOS)


def _rounded_tile(side, gradient_img, radius_frac=0.30, margin_frac=0.06):
    """A rounded-square tile filled with `gradient_img`, with transparent margin."""
    S = side * SS
    tile = gradient_img.resize((S, S), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    m = int(S * margin_frac)
    r = int(S * radius_frac)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], radius=r, fill=255)
    tile.putalpha(mask)
    return tile.resize((side, side), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Menu-bar state icons (square; rumps displays them at 20x20 pt)
# ---------------------------------------------------------------------------

MENU_SIDE = 44  # bitmap px; shown at 20pt (retina-crisp)


def make_menubar_icons():
    ASSETS.mkdir(exist_ok=True)

    # Idle: monochrome template fish (alpha only; macOS tints it).
    idle = _fish_layer(MENU_SIDE, (0, 0, 0), with_eye="hole")
    idle.save(ASSETS / "menubar-idle.png")

    def tiled_state(grad_from, grad_to, accent):
        grad = linear_gradient(MENU_SIDE * SS, [grad_from, grad_to], 135)
        tile = _rounded_tile(MENU_SIDE, grad)
        fish = _fish_layer(MENU_SIDE, (255, 255, 255), with_eye="dark", eye_dark=hx(FISH_EYE))
        # shrink the fish a touch so it sits inside the tile
        fs = int(MENU_SIDE * 0.82)
        fish = fish.resize((fs, fs), Image.LANCZOS)
        off = (MENU_SIDE - fs) // 2
        tile.alpha_composite(fish, (off, off))
        accent(tile)
        return tile

    # Recording: pink gradient tile + white dot (top-right).
    def rec_accent(tile):
        d = ImageDraw.Draw(tile)
        r = MENU_SIDE * 0.13
        cx, cy = MENU_SIDE * 0.78, MENU_SIDE * 0.24
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))
    tiled_state(REC_FROM, REC_TO, rec_accent).save(ASSETS / "menubar-recording.png")

    # Working: purple gradient tile + three rising bubbles.
    def work_accent(tile):
        d = ImageDraw.Draw(tile)
        for (fx, fy, fr) in [(0.30, 0.20, 0.07), (0.50, 0.12, 0.05), (0.68, 0.22, 0.06)]:
            cx, cy, r = MENU_SIDE * fx, MENU_SIDE * fy, MENU_SIDE * fr
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 235))
    tiled_state(WORK_FROM, WORK_TO, work_accent).save(ASSETS / "menubar-working.png")

    # Done: brand gradient tile + green check badge (bottom-right).
    def done_accent(tile):
        S = MENU_SIDE * SS
        big = tile.resize((S, S), Image.LANCZOS)
        d = ImageDraw.Draw(big)
        r = S * 0.20
        cx, cy = S * 0.76, S * 0.76
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hx(SUCCESS_GREEN) + (255,),
                  outline=(255, 255, 255, 255), width=int(S * 0.022))
        lw = int(S * 0.035)
        d.line([(cx - r * 0.45, cy + r * 0.02), (cx - r * 0.08, cy + r * 0.40),
                (cx + r * 0.52, cy - r * 0.42)], fill=(255, 255, 255, 255),
               width=lw, joint="curve")
        tile.paste(big.resize((MENU_SIDE, MENU_SIDE), Image.LANCZOS), (0, 0))
    tiled_state(BRAND_PURPLE, BRAND_PINK, done_accent).save(ASSETS / "menubar-done.png")

    print("menu-bar icons → assets/menubar-{idle,recording,working,done}.png")


# ---------------------------------------------------------------------------
# App icon (squircle, brand gradient, sonar rings, fish)
# ---------------------------------------------------------------------------

def _squircle_mask(S, n=5.0, inset=0.0):
    """Apple-style superellipse mask at resolution S."""
    yy, xx = np.mgrid[0:S, 0:S].astype(float)
    cx = cy = (S - 1) / 2
    a = (S / 2) * (1 - inset)
    v = (np.abs((xx - cx) / a) ** n) + (np.abs((yy - cy) / a) ** n)
    mask = (v <= 1.0).astype("uint8") * 255
    return Image.fromarray(mask, "L")


def make_app_icon_master(side=1024):
    grad = linear_gradient(side, APP_STOPS, 145).convert("RGBA")
    grad.putalpha(_squircle_mask(side, n=5.0, inset=0.02))

    # Two faint concentric "sonar" rings, clipped to the tile.
    rings = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rings)
    cx = cy = side / 2
    for frac, alpha in [(0.40, 56), (0.30, 30)]:
        r = side * frac
        rd.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, alpha),
                   width=max(2, int(side * 0.012)))
    clipped = Image.composite(rings.getchannel("A"),
                              Image.new("L", (side, side), 0),
                              _squircle_mask(side, n=5.0, inset=0.02))
    rings.putalpha(clipped)
    grad.alpha_composite(rings)

    # Fish (white body, pink tail, dark eye), centered.
    fish = _fish_layer(side, (255, 255, 255), with_eye="dark",
                       eye_dark=hx(FISH_EYE), tail_tint=hx(TAIL_PINK))
    fs = int(side * 0.62)
    fish = fish.resize((fs, fs), Image.LANCZOS)
    grad.alpha_composite(fish, ((side - fs) // 2, (side - fs) // 2))
    return grad


def make_icns():
    master = make_app_icon_master(1024)
    ASSETS.mkdir(exist_ok=True)
    master.resize((512, 512), Image.LANCZOS).save(ASSETS / "app-logo.png")
    iconset = HERE / "yap.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    specs = [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
             (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
             (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x")]
    for px, name in specs:
        master.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{name}.png")
    icns = HERE / "yap.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    shutil.rmtree(iconset)
    print(f"app icon → {icns.name}  (in-app logo → assets/app-logo.png)")


if __name__ == "__main__":
    make_menubar_icons()
    make_icns()
    print("done.")
