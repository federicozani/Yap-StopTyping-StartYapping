# Yap — Implementation Notes (Tkinter ↔ Design)

Practical, honest guidance for implementing the Yap design in the **real Python app**. Read alongside `DESIGN-SPEC.md` and `design-tokens.json`.

---

## 1. Mapping the design to existing app elements

The current app already has the functional pieces below. The design **re-skins and reorganizes** them — it does not change what they do. Map them like this:

| Existing app element | Design counterpart (spec §) | Notes |
|---|---|---|
| **API key field** | Form field §F, rendered **secure/obscured** with a show/hide eye toggle | Put it on the **"Voice & model"** sidebar pane. Label "OpenAI / API key" (`eyebrowSm`), helper text for where to get one. Error state when the key is rejected. |
| **Model field** (e.g. whisper model / LLM) | Dropdown §E.2 (popup button) | Also on "Voice & model". If it's free-text today, prefer converting to a dropdown of known models; keep a text fallback for custom endpoints. |
| **Per-mode *name* field** | Prompt-editor header title §I + form field §F | Each "mode" = one button (Yap it / Sum it up / custom). The name is an editable text field shown as the card title. |
| **Per-mode *hotkey*** | Hotkey row + keycap §H | The keycap is clickable to rebind; show "Press keys…" while capturing, conflict error if already bound. |
| **Per-mode *prompt*** | Prompt editor multiline §I | The big text area. The yellow inline highlight is decorative only — don't try to replicate rich-text; a plain multiline `Text` widget is fine. |
| **Add button** | Dashed "add" button §G / "＋ New" chip §I | Appends a new blank mode card. |
| **Remove button** | Ghost/destructive button §G | Per-mode; confirm before deleting a mode that has a bound hotkey. |
| **Save button** | Primary button §G | If you can, **autosave** and make Save a reassurance rather than a requirement. |
| Recording trigger (global hotkey) | Menu-bar **Recording** state §A | Starts the 7:00 countdown. |
| Transcribe vs. summarize behavior | The two shipped modes (Yap it / Sum it up) §H | They differ **only by prompt** — verbatim cleanup vs. summary. Reinforce that in the UI so users understand custom modes are the same mechanism. |
| Processing/"please wait" indicator | Menu-bar **Working** state §A (bubbles) | Replace any hourglass/spinner with the rising-bubbles treatment. |

**New things the design introduces** (not pure reskins) that you may need to add:
- The **7-minute max recording** with a **live countdown** in the menu bar and **auto-stop** at 0:00.
- The **quick panel** popover (if the app currently only has a menu, this is a richer dropdown).
- The **menu-bar state machine**: Idle → Recording → Working → Done → Idle.

---

## 2. What's straightforward in Tkinter

- **Overall window, sidebar + content split** — `Frame`/`grid`/`pack` handle this fine. Fixed-width sidebar + expanding content via `grid_columnconfigure(weight=...)`.
- **Plain labels, text entries, multiline prompt** — `ttk.Entry`, `tk.Text`. The API key, model, mode name, and prompt all map to standard widgets.
- **Solid-color fills, dividers, basic layout/spacing** — trivial.
- **Popup menus / dropdowns (functionally)** — `ttk.Combobox` or `tk.OptionMenu` give working dropdowns (styling is the catch — see below).
- **Resize behavior** — grid weights reproduce "sidebar fixed, content fluid" exactly.
- **Global hotkeys + the countdown timer** — `after(1000, ...)` loops drive the mm:ss countdown and auto-stop; global capture is via `pynput`/`pyobjc` regardless of GUI toolkit.

## 3. What's hard (and how to fake it) in Tkinter

| Design feature | Tkinter reality | Workaround |
|---|---|---|
| **Gradients** (icons, buttons, header, toggle-on) | No native gradient anywhere. | Pre-render gradient PNGs (the `assets/` exports) and place them as `Label(image=...)`; or draw a gradient on a `Canvas` pixel-by-pixel/with PIL. PIL (`Pillow`) is the pragmatic choice — generate gradient tiles once at startup. |
| **Rounded corners** on cards/fields/chips | `Frame`s are always square. | Draw rounded rects on a `Canvas` (`create_polygon`/arc), or use rounded PNG backgrounds via PIL with an alpha mask. Tedious for many widgets. |
| **Squircle app icon** | n/a (it's the .icns) | Generate the `.icns` from a 1024px master using a real superellipse mask in PIL or a design tool — don't ship a plain rounded-rect. |
| **Drop shadows / inner highlight** | None. | Bake into PNGs, or skip on minor elements. Window/panel shadows are provided by macOS itself for real windows/popovers. |
| **Custom toggle switch** | `ttk.Checkbutton` looks nothing like the spec. | Canvas-drawn pill + knob, or two PNGs (on/off) swapped on click. |
| **Styled dropdown** (pill, chevron, purple border) | `ttk.Combobox` styling is very limited and OS-dependent. | Either accept the near-default look, or build a Canvas/Menu-backed custom control. |
| **Focus ring / hover states** | No focus ring; hover needs manual `<Enter>`/`<Leave>` bindings. | Bind events and redraw borders. Real effort across many controls. |
| **Animations** (countdown is easy; **bubbles, waveform, mascot float, sonar rings** are not) | `Canvas` + `after()` can animate, but it's all hand-rolled. | Countdown text: trivial. Bubbles/waveform: Canvas loops, moderate effort. Mascot float + rings: skippable polish — static fish is acceptable for v1. |
| **Menu-bar status item** | `tkinter` cannot create an `NSStatusItem`. | Use **`rumps`** (PyObjC-based) for the menu-bar item, template image, and state swaps — this is essentially required; stock Tk has no menu-bar presence. |
| **Web fonts** (Bricolage Grotesque, Hanken Grotesk) | Tk only sees installed/registered fonts. | Bundle the `.ttf`s and load via `tkextrafont`/PIL, or **substitute**: display → a heavy geometric/grotesque you can register; UI → system font (`SF Pro`/Helvetica Neue). Document the substitution. |

## 4. Recommendation: should you stay on Tkinter?

**Honest take: Tkinter can technically render this, but you will spend most of your time fighting it** — every rounded gradient card, custom toggle, styled dropdown, and animation is hand-built on a Canvas or pre-rendered as images. The result is brittle and hard to maintain, and the menu-bar item (the core of the product) isn't even possible in pure Tk.

Ranked options:

1. **PyObjC / AppKit (recommended for a true native feel).** You're already on macOS-only with a menu-bar app. AppKit gives you `NSStatusItem`, native vibrancy, real rounded/gradient layers (`CALayer`, `CAGradientLayer`), native popovers (`NSPopover` = the quick panel almost for free), native switches/popups, and Core Animation for the bubbles/waveform. Highest fidelity to this design, best platform integration. Cost: steeper API, more Obj-C-flavored code. **Use `rumps` for the menu-bar layer regardless.**

2. **Embedded web view (fastest path to pixel-fidelity).** Render the actual design as HTML/CSS (you already have it) inside a `pywebview` / `WKWebView` window, and bridge to Python for hotkeys, transcription, and storage. You get gradients, rounded corners, fonts, focus rings, and animations **for free and exactly as designed**. Keep the menu-bar item native (`rumps`) and let it open the web-view window. Best effort-to-fidelity ratio if the team is comfortable with a JS↔Python bridge. Downside: heavier runtime, less "native" feel for form controls (mitigated by styling them yourself, which the design already specifies).

3. **Stay on Tkinter (only if you must).** Viable for a **minimum** version: keep Settings functional with mostly default-styled widgets, pre-render the gradient icon/buttons as PNGs, do the countdown, and accept a plain fish (no bubble/waveform animation). Add `rumps` for the menu bar. Expect the result to look noticeably less polished than the comps.

**Suggested split if you want native + fast:** `rumps`/PyObjC for the **menu-bar icon + states + popover** (the always-on surface that must feel native), and an **embedded web view** for the **Settings and onboarding** windows (where the gradient/rounded/animated richness lives). This gets you the best of both with the least custom-drawing.

---

## 5. Assets provided

All PNGs in `assets/` are **@2x**. Display font (Bricolage Grotesque) may render as a serif fallback **in the snapshots only** — that's a capture artifact; the real font is specified in `design-tokens.json` (`typography.fontFamily.display`). Use the tokens, not the pixels, for exact type.

| File | Shows |
|---|---|
| `01-full-board.png` | All surfaces together (overview) |
| `02-app-icon.png` | App / Dock icon |
| `03-menubar-context.png` | Icon sitting in a real menu bar + tint options |
| `04-menubar-states.png` | Idle → Recording (countdown) → Working (bubbles) → Done |
| `05-welcome-1-what-it-does.png` | Onboarding step 1 |
| `06-welcome-2-hotkeys.png` | Onboarding step 2 (hotkey rows) |
| `07-welcome-3-custom-prompts.png` | Onboarding step 3 (prompt editor) |
| `08-quick-panel.png` | Menu-bar dropdown / quick panel |
| `09-settings.png` | Settings window (General pane) |

If you need vector/source for the fish glyph, it's a small inline SVG in `Yap.dc.html` (search for `viewBox="0 0 128 104"`); the geometry is also transcribed in `DESIGN-SPEC.md §A`.
