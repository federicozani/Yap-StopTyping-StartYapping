# Yap — Design Specification (Developer Handoff)

> **What this is.** Yap is a macOS **menu-bar dictation app**. It lives as a fish icon in the system menu bar; the user holds a hotkey to dictate, and Yap drops clean text at the cursor. This document specifies every surface of the product so a developer who has never seen the design can rebuild it faithfully.
>
> **Implementation reality.** The shipping app is a **native macOS desktop application written in Python (currently Tkinter)** — *not* a web page. All measurements below are in **points/pixels at 1x** (1 pt ≈ 1 px on a standard display; assets in `assets/` are exported at **@2x**). Colors are sRGB hex. Where a visual will be hard to reproduce in a basic GUI toolkit, it is flagged inline with ⚠️ and discussed fully in `IMPLEMENTATION-NOTES.md`.
>
> Reusable values (palette, type scale, spacing, radii, shadows, motion) live in `design-tokens.json`; this doc references them by name (e.g. `brand.gradientPrimary`).

---

## 0. Surface inventory

| # | Surface | Native window type | Asset |
|---|---------|--------------------|-------|
| A | **Menu-bar icon** + its 4 lifecycle states | `NSStatusItem` (template image) | `assets/03-menubar-context.png`, `assets/04-menubar-states.png` |
| B | **App icon** (Dock / Finder / .app bundle) | `.icns` bundle icon | `assets/02-app-icon.png` |
| C | **Welcome / onboarding** — 3 steps | A normal app window (~700pt wide) | `assets/05/06/07-*.png` |
| D | **Quick panel** — the menu-bar dropdown | Borderless popover anchored to the status item | `assets/08-quick-panel.png` |
| E | **Settings** — sidebar + content | Standard resizable app window | `assets/09-settings.png` |

Full board overview: `assets/01-full-board.png`.

The **two surfaces that matter most for the native build** are **E (Settings)** — the real configuration window with API key, model, and per-mode rows — and **D (Quick panel)** and **A (the icon states)**. Onboarding (C) is lower priority and can ship later.

---

## A. Menu-bar icon & lifecycle states

The icon is a **simplified side-view fish** (oval body, triangular tail pointing left, one round eye). In the menu bar it renders as a **template image** that the OS tints (so it adapts to light/dark menu bars). Within the app's own colored chips it can be filled white or with the brand gradient.

**Glyph geometry** (from the source SVG, viewBox `0 0 128 104`):
- Tail: triangle from `(30,52)` out to a notch at left (`~(9,35)` and `(9,69)`).
- Body: ellipse centered `(66,52)`, radii `rx≈42 / ry≈30`.
- Eye: filled circle, center `~(88,45)`, `r≈9`, color `fish.eye #3A1155`, with a small white highlight dot.
- Optional mouth: small ellipse near `(108,52)`.

Render target in the menu bar: **~18×14 pt** glyph inside the status item. ⚠️ The menu bar expects a **monochrome template PDF/PNG**; the gradient versions are only for in-app chips.

### The 4 lifecycle states (see `assets/04-menubar-states.png`)

| State | Trigger | Icon treatment | Extra |
|-------|---------|----------------|-------|
| **Idle** | default | Plain template fish, no chip. Tinted by OS (`ink.850` on light bars, white on dark). | — |
| **Recording** | user presses/holds a dictation hotkey | Fish chip swaps to a **pill on the recording gradient** (`status.recordingGradient` `#E0479A → #FF5BA6`). Shows a **pulsing white dot** + a **live mm:ss countdown** in `keycapSm` tabular figures (white). | Countdown starts at **7:00** and counts **down** (max recording length). At `0:00` recording **auto-stops** and advances to *Working*. A small `7:00 MAX` tag may appear in onboarding to explain the cap. ⚠️ tabular/monospaced digits + per-second redraw. |
| **Working** | hotkey released (or auto-stop) | Fish chip on `brand.purpleDeep → magentaAlt` gradient (`#7C46E8 → #B048D0`), white fish, with **3 small white bubbles rising and fading** above it. | This is the "system is thinking" indicator — **bubbles, not a spinner / not an hourglass**, to stay on the fish theme. `shadow.menubarWorking`. ⚠️ requires a short looping animation. |
| **Done** | text delivered | Returns to the idle template fish with a **green success check** badge (`status.successGreen #28C840`, white ✓, `14pt` circle, 2px white ring) briefly overlaid, then fades back to Idle. | Hold ~1s then revert. |

**Chip dimensions** (recording/working pills): height ~**21 pt**, radius `radius.menubarGlyph (7)`, internal padding ~`4pt × 7–8pt`, gap `4–5pt` between glyph and label/dot. Recording dot: `7pt` white circle. Bubbles: `2.5–4pt` white circles.

**Behavior note:** the icon is the *only* persistent status surface, so its state must always reflect reality — if transcription fails, show an error treatment (recommend the chip flashing `status.recordingDot #FF4D4D` with a `!`), not a silent revert.

---

## B. App icon

A **rounded-square (squircle) tile** with the brand gradient and the fish.

- Tile: Apple superellipse / squircle. At the **172pt** preview render the corner radius is **39pt** (`radius.iconSquircleRatio ≈ 0.227` of the side). For the **1024×1024 master** use the macOS squircle mask (≈ 232px radius equivalent). ⚠️ A plain rounded-rect is visibly wrong against other macOS icons — use a true superellipse.
- Fill: diagonal gradient `#A347EC 0% → #C247CB 45% → #FF5BA6 100%` (≈145°).
- Inside, two faint concentric **white rings** (sonar/"listening" motif), `6pt` stroke at `rgba(255,255,255,.22)` and `.12`, clipped to the tile.
- White fish, centered, ~**116×96pt** glyph, with pink tail (`fish.tailPink`), belly tint, dark eye + highlight, and 1–2 tiny white "bubble" dots near the top-right.
- Shadows: `shadow.appIcon` (drop + inner top highlight).

The 54pt **masthead/in-app logo** variant uses the same tile at `radius 14`, gradient `brand.gradientPrimary`, white fish, `shadow.appIconSm`.

---

## C. Welcome / onboarding (3 steps)

Three sequential screens shown on first launch, each inside a **macOS window chrome** (titlebar with traffic-light dots `status.trafficRed/Yellow/Green`, title "Welcome to Yap" in `bodyXs` weight 600 `ink.550`, titlebar gradient `surface.windowTitlebarTop → Bottom`). Window radius `radius.cardLg (13)`, border `border.hairline06`, `shadow.window`. Content width ≈ **680pt**.

A **3-dot progress indicator** sits near the footer of each step: the active step is a **26×7pt rounded bar** on `brand.gradientPrimary`; inactive steps are `7pt` dots in `#E1D1ED`.

### Step 1 — "What it does" (`assets/05`)
Centered layout on a `radial-gradient(#faf2ff → #fff)`:
- Animated mascot (⚠️ float + expanding sonar rings + a bobbing "!" speech bubble — see `motion`). ~150pt stage.
- Headline `heroDisplay` `ink.900`: **"Stop typing. Start yapping."** (2 lines).
- Lead `leadBody` `ink.600`, max-width ~430pt, centered.
- Primary CTA button: **"Let's yap →"** (see Buttons / Primary).
- Sub-caption `caption` `ink.300`: "Takes 30 seconds · no account needed".

### Step 2 — "The hotkeys" (`assets/06`)
Left-aligned. Headline `windowTitle` "Two keys. Zero typing." + `body` intro. Then the **hotkey rows** (see component §H). Two filled rows ("Yap it" / "Sum it up") plus a **dashed "Add your own hotkey" row**. Footer: ghost "← Back", progress dots, primary "Next →".

### Step 3 — "Make it yours" (`assets/07`)
Headline "Every button is just a prompt." + intro. Contains the **prompt editor** component (§I) populated with the user's "Task list" example, a **chip row** of the user's buttons, and a primary CTA "Start yapping 🎉".

---

## D. Quick panel (menu-bar dropdown)

A **borderless popover**, **344pt wide**, radius `radius.panel (18)`, `shadow.quickPanel`, border `border.hairline08`, anchored under the status item. Top-down regions:

1. **Header bar** — full-width `brand.gradientPrimary` (135°, `#7C46E8 → #FF5BA6`), padding `15×17pt`. White fish glyph (`26×21pt`) + wordmark "Yap" (`panelTitle`, white) on the left; a **settings gear** icon (white, `18pt`, 0.9 opacity) on the right.
2. **Live status block** — `radial-gradient(#faf2ff → #fff)`, centered. A **6-bar waveform** (⚠️ animated, bars `5pt` wide × up to `40pt` tall, colors stepping across the gradient `#9B45E8 → #FF6F94`, vertical scale animation), then **"Listening…"** (`panelTitle` `ink.900`) and a hint **"Release ⌥Space when you're done"** (`bodyXs` `ink.500`). When idle, replace with the resting fish + "Ready to yap".
3. **Action rows** — one per button (Yap it / Sum it up / custom). Each row: `34pt` rounded-square gradient icon tile (`radius.key`), title (`rowTitleSm` `ink.900`) + subtitle (`captionSm` `ink.500`), and a **hotkey badge** on the right (white pill, `1px` colored border, `caption` weight 600). The **active/hovered** row gets a `surface.tintLavender2 #F7F1FC` fill (`radius.control`).
4. **"Last yap" block** — divider (`border.rowLine`), eyebrow "LAST YAP" (`eyebrowSm` `ink.400`) with a "Copy" affordance (`captionSm` weight 600 `brand.purple`), and the transcript in a `surface.tintLavender` rounded box (`radius.chip`), `bodyXs` `ink.700`.
5. **Footer** — `surface.tintLavender3`, top border, centered `footnote` `ink.300`: "⌥Space to yap from anywhere · 142 yaps this week".

Row vertical padding `11pt`, side padding `12pt`, icon-to-text gap `11pt`.

---

## E. Settings window  ← primary native window

Standard resizable macOS window. Titlebar identical to onboarding but titled **"Yap Settings"**. Body is a **two-pane split**: fixed sidebar + flexible content. Window content min-height ~**430pt**.

### E.1 Sidebar (left)
- Width **188pt**, fixed. Background `surface.tintLavender`, right border `border.rowLine`, padding `16×12pt`.
- **Nav items**, top to bottom: General · Buttons & keys · Voice & model · Dictionary · About. Each row: `10×12pt` padding, `radius.chip`, icon + label (`rowTitleSm`).
  - **Selected**: fill `linear-gradient(#F1E2FB → #FCE2EF)`, label `ink.900` weight 700.
  - **Default**: transparent, label `ink.550` weight 600.
  - **Hover**: fill `surface.tintLavender2`.
- **Footer card** (pinned bottom): white card, `border.rowLine`, `radius.cardLg`, centered — a small bobbing fish glyph + version line "Yap v1.0 — **"Mackerel"**" (`footnote` `ink.500`, bold name `ink.900`). ⚠️ the gentle bob animation is optional/decorative.

### E.2 Content pane (right)
Padding `26×30pt`. Begins with a **section title** (`sectionTitle` `ink.900`) + one-line description (`bodySm` `ink.500`, `~22pt` margin below). Then a stack of **setting rows** separated by `1px` `border.rowDivider` lines (no line after the last row). Each row: `15pt` vertical padding, label block on the left (title `rowTitle` `ink.900` + helper `caption` `ink.500`), control on the right, space-between.

Row control types used on the **General** pane (`assets/09`):

**Toggle / switch** — track `46×27pt`, radius `14`, knob `21pt` white circle inset `3pt` with `0 1px 3px rgba(0,0,0,.2)`.
- **On**: track `status.toggleOnGradient`, knob right.
- **Off**: track `status.toggleOffTrack #E4D8EF`, knob left.
- **Disabled**: 50% opacity, no pointer.
- (Rows shown: "Launch at login" = on, "Play sound effects" = on, "Auto-paste at cursor" = off.)

**Dropdown / popup button** — pill `surface.tintLavender2`, border `border.purpleField`, radius `radius.key`, padding `8×13pt`, value text `bodySm` weight 600 `ink.900`, a `12pt` chevron in `brand.purple` on the right. (Row shown: "Language" = "English (US)".)
- **Hover**: border `brand.purple` @ ~40%.
- **Focus**: 2px focus ring `brand.purple`.
- **Open**: native popup menu.

**Swatch picker** (row "Menu bar look") — three `34pt` rounded-square swatches (`radius.key`), `8pt` gap: dark (`#3A1155`, white fish), gradient (`brand.gradientPrimary`), light (`surface.chipNeutral`, dark fish). Selected swatch carries a **2px `brand.purple` ring**.

> **The real Settings window also contains** (mapped in `IMPLEMENTATION-NOTES.md`): an **API key** field, a **model** field, and the **per-mode rows** (mode *name* + *hotkey* + *prompt*) with **Add / Remove / Save** buttons. Spec those using the field, prompt-editor, hotkey-row, and button components below.

---

## F. Form fields (for API key, model, mode name, etc.)

Single-line text field:
- Background `white` (or `surface.tintLavender` for read-only), border `1px border.purpleField`, radius `radius.field (14)` for large fields / `radius.control (11)` for compact, padding `13×15pt`, text `body` `ink.800`, placeholder `ink.300`.
- **Hover**: border `border.key`.
- **Focus**: border `brand.purple`, 2px outer ring `rgba(155,69,232,.25)`.
- **Disabled**: background `surface.rowDivider`, text `ink.300`.
- **Error**: border `status.recordingDot #FF4D4D`, helper text below in the same red (`caption`). ⚠️ Tkinter has no native focus ring — see notes.
- **API key** specifically: render as a **secure/obscured** field (•••) with a show/hide eye toggle on the right (reuse the eye icon style).

Field label above: `eyebrowSm` `ink.400`, `8pt` below. Helper text under: `caption` `ink.500`.

---

## G. Buttons

**Primary** — `brand.gradientPrimary`, white label (`buttonLg` 17/700 or `buttonMd` 15/700), radius `radius.cardLg (13)` (large) / `radius.control (11)` (medium), padding `15×34pt` (lg) / `11×24pt` (md), `shadow.buttonPrimary`. **Hover**: brightness +4%, lift shadow. **Active**: scale 0.98. **Disabled**: 45% opacity, no shadow.

**Ghost / secondary** — transparent, label `ui` 14/600 `ink.550` ("← Back"). Hover: `ink.900`.

**Dashed "add" button** — `2px dashed border.dashed`, radius `radius.field`, centered `+` icon + label `brand.purple` 14/700, transparent fill. Hover: fill `surface.tintPurpleHi`.

**Icon button** (gear, copy, eye) — `18pt` line icon, hit target ≥ **44×44pt** ⚠️ (don't ship a tiny clickable region). Default `ink.500`, hover `ink.900` / `brand.purple`.

For the real app's **Remove** button, recommend a ghost/destructive variant: label `status.recordingDot`, hover fill `#FFF0F2`.

---

## H. Hotkey row component (per-mode)

Used in onboarding step 2, the quick panel, and the Settings "Buttons & keys" pane. Horizontal card, `radius.field (15)`, padding `18×20pt`, `18pt` gap:
- **Left:** `52pt` rounded-square gradient **mode icon** (`radius.cardLg`), `shadow.chipPurple`/`chipPink`. "Verbatim/transcribe" modes use the purple gradient; "summary" modes use the pink/coral gradient (`#FF5BA6 → #FF8A5B`).
- **Center:** mode **name** (`hotkeyName` `ink.900`) + a small uppercase **tag** pill (`tag`; purple chip for "verbatim", pink chip for "summary") + one-line description (`bodyXs` `ink.600`).
- **Right:** the **keycap combo** — see below.
- Card fills: purple modes `linear-gradient(#faf2ff → #fff0f8)` border `border.purpleCard`; pink modes `linear-gradient(#fff0f8 → #fff5ef)` border `border.pinkCard`.

**Keycap (`<kbd>`)**: white, `1px` border (`border.key` purple / `border.pinkKey`), **`3px` bottom border** for depth, radius `radius.key`, padding `8×11pt` (modifier) / `8×14–16pt` (named key), text `keycap` `ink.900`. Combo joined by a `+` in `border.key`-ish muted color. Modifier glyphs: `⌥` (Option), `⌘`, `⇧`, `⌃`. Shipped defaults: **Yap it = ⌥Space**, **Sum it up = ⌥S**, custom **Task list = ⌥T**.

States: **Hover** card → border darkens one step + `shadow.promptEditor`. **Recording a new shortcut** (when the user clicks the keycap to rebind) → keycap shows "Press keys…" in `brand.purple`, card border `brand.purple`. **Conflict/error** → keycap border `status.recordingDot` + helper "Already used by Sum it up".

---

## I. Prompt editor component (per-mode prompt)

The heart of customization — "every button is just a prompt." Card, `radius.field (16)`, border `border.purpleField`, `shadow.promptEditor`:
- **Header strip** — `linear-gradient(#faf2ff → #fff0f8)`, bottom border `border.purpleCard`, padding `16×18pt`: `42pt` gradient icon/emoji tile, **mode name** (`cardTitleLg`-ish 16/800 `ink.900`) + "Custom button" subtitle (`captionSm` `ink.500`), and the keycap combo on the right.
- **Body** — padding `16×18pt`. Eyebrow "PROMPT" (`eyebrowSm` `ink.400`). The prompt text sits in a `surface.tintLavender` box, border `border.purpleFieldAlt`, radius `radius.control`, padding `13×15pt`, `body` `ink.800`, line-height 1.6. Inline emphasis can be highlighted with `status.highlightYellow` (radius 4, `0 3pt` padding).
- **Option chips** below the prompt: `captionSm` weight 600 `ink.550` on `surface.chipNeutral`, radius `radius.key`, padding `6×11pt`, `8pt` gap. Examples: "Output: Markdown", "Tone: Casual", "Paste & copy".

This is an **editable multiline text area** in the real app. States = the field states in §F (focus ring, error). The chips are **dropdowns/segmented selectors** controlling output format, tone, and delivery (paste vs. copy).

**Button/mode chip row** (the user's set of modes): pill chips, `radius.chip`, padding `9×14pt`, `rowTitleSm` weight 700 `ink.900`, with an `8pt` color dot or emoji prefix. Background tints per mode (`chipPurple`, `chipPink`, or a blend). The trailing **"＋ New"** chip uses the dashed-button style.

---

## J. Layout & resize behavior

- **Settings window** (E): the **sidebar stays a fixed 188pt**; the **content pane is fluid** and absorbs all width changes. Setting rows are full-width and the right-hand control stays right-aligned (space-between). Vertical overflow scrolls **inside the content pane only** (the sidebar and titlebar stay put). Minimum window content size ≈ **620×460pt**. The per-mode list should scroll within its section if it grows past the viewport.
- **Quick panel** (D): **fixed 344pt width**; height grows with content. It is a popover — it closes on outside click / `Esc`.
- **Onboarding** (C): fixed ~700pt width, not resizable; centered on screen.
- **Alignment:** all section content is **left-aligned** except onboarding step 1, which is **centered**. Labels sit left, controls sit right. Icon tiles are vertically centered to their text block.
- **Density:** comfortable. Row height ~ `44–54pt`. Never let an interactive target drop below **44×44pt**.

---

## K. Tone & copy

Yap's voice is **playful and loud** but never cutesy-to-the-point-of-unclear. Real strings in the design: "Stop typing. Start yapping.", "Two keys. Zero typing.", "Every button is just a prompt.", "Listening…", "Working on it", "your Mac's all ears", "Last yap", "142 yaps this week", version codename "Mackerel". Keep verbs first, drop filler. The fish + bubble motif replaces generic loading/recording iconography everywhere — **no hourglass, no spinner**; use bubbles and the waveform.

---

## L. Hard-to-reproduce inventory (quick index — details in IMPLEMENTATION-NOTES.md)

1. Gradient fills (icons, buttons, headers, toggles) — ⚠️ Tkinter has no native gradient.
2. Rounded corners on arbitrary widgets + the squircle app icon — ⚠️ limited in Tkinter.
3. Drop shadows / inner highlights — ⚠️ not native.
4. Live animations: recording countdown, working bubbles, listening waveform, mascot float/rings — ⚠️ need a timer/Canvas loop.
5. Focus rings, hover states, custom switch/toggle, custom dropdown styling — ⚠️ require Canvas-drawn custom widgets or a different toolkit.
6. Menu-bar template-image state swapping + per-second countdown redraw — ⚠️ doable but fiddly with stock Tk; cleaner via `rumps`/PyObjC.
7. Web-font loading (Bricolage Grotesque / Hanken Grotesk) — ⚠️ must bundle the .ttf and register, or substitute.
