#!/usr/bin/env python3
"""
Embedded web-view UI for Yap (Settings, onboarding, quick panel).

Why a web view: the finalized design uses gradients, rounded corners, custom
fonts and small animations that are painful in Tkinter. We render the design as
HTML/CSS in a native WKWebView (WebKit is a macOS system framework) and bridge
to Python for reading/writing the config. WebKit + PyObjC ship cleanly inside
the py2app bundle, unlike heavier wrappers.

The app stays a `rumps` menu-bar app; these windows are opened from menu / panel
callbacks, which run on the main thread (WKWebView must be used on the main
thread).
"""

import json
import os

import objc
from AppKit import (
    NSApp, NSBackingStoreBuffered, NSColor, NSWindow, NSPanel, NSScreen,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
    NSPopUpMenuWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp,
    NSEventTypeRightMouseUp, NSEventModifierFlagControl,
    NSMenu, NSMenuItem, NSView,
    NSApplicationActivationPolicyRegular, NSApplicationActivationPolicyAccessory,
    NSViewWidthSizable, NSViewHeightSizable, NSViewMinYMargin,
)
from Foundation import NSObject, NSURL, NSMakeRect
from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController

# Keep strong references so windows/controllers aren't garbage-collected while
# they're on screen (PyObjC won't retain them for us).
_LIVE = set()

# Whether the standard Edit menu (copy/paste/…) has been installed yet.
_EDIT_MENU_INSTALLED = False


# Height of the HTML title bar (.titlebar in app.css). The drag strip must match.
TITLEBAR_HEIGHT = 38.0


class _DragStrip(NSView):
    """An invisible view laid over the HTML title bar so the window can be
    dragged by it.

    The windows use NSWindowStyleMaskFullSizeContentView, so the WKWebView
    covers the whole frame — including where the system title bar would be. A
    web view swallows every mouse event and reports mouseDownCanMoveWindow =
    False, which left the window impossible to move: there was no draggable
    surface anywhere on it. (`-webkit-app-region: drag` is a Chromium
    extension; WebKit ignores it, so the CSS title bar can't help here.)

    This strip sits above the web view in the content view and answers YES, so
    AppKit drags the window when a click lands on it. It belongs to the content
    view, which sits BELOW the frame's title-bar view, so the traffic-light
    buttons keep receiving their own clicks."""

    def mouseDownCanMoveWindow(self):
        return True

    def acceptsFirstMouse_(self, event):
        # Drag the window on the very first click, even when Yap isn't active.
        return True


def _set_activation_policy(regular):
    """Yap is an LSUIElement (menu-bar-only) app, and an accessory app can't
    reliably take and hold front-most position: Settings would open behind
    whatever you were using, or sink the moment focus moved. Becoming a Regular
    app for as long as a real window is open makes macOS treat that window
    normally — it comes to the front, stays in its z-order, and joins Cmd-Tab.
    A Dock icon appears while it's open and goes away with it."""
    try:
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular if regular
                                   else NSApplicationActivationPolicyAccessory)
    except Exception:
        pass


def _web_windows_open():
    """How many real (non-panel) web windows are on screen."""
    return sum(1 for c in _LIVE if isinstance(c, YapWebWindow))


def _to_py(obj):
    """Best-effort convert a bridged NSDictionary/NSArray/NSString to plain
    Python via JSON round-trip-free recursion."""
    if isinstance(obj, dict) or hasattr(obj, "allKeys"):
        return {str(k): _to_py(obj[k]) for k in obj.keys()}
    if isinstance(obj, (list, tuple)) or hasattr(obj, "count") and not isinstance(obj, str):
        try:
            return [_to_py(x) for x in obj]
        except TypeError:
            return obj
    return obj


class YapWebWindow(NSObject):
    """A titled, optionally-resizable window hosting a WKWebView, with a
    `yap` message handler bridging JS → Python."""

    # ---- WKScriptMessageHandler -----------------------------------------
    def userContentController_didReceiveScriptMessage_(self, ucc, message):
        try:
            body = message.body()
            try:
                data = json.loads(body) if isinstance(body, str) else _to_py(body)
            except Exception:
                data = _to_py(body)
            action = (data or {}).get("action")
            payload = (data or {}).get("payload")
            if self._on_message is not None:
                self._on_message(action, payload, self)
        except Exception:
            import traceback
            traceback.print_exc()

    # ---- NSWindowDelegate ------------------------------------------------
    def windowWillClose_(self, notification):
        _LIVE.discard(self)
        # Last real window gone -> back to a menu-bar-only app (Dock icon goes).
        if _web_windows_open() == 0:
            _set_activation_policy(False)
        if self._on_close is not None:
            try:
                self._on_close(self)
            except Exception:
                pass

    # ---- python-side API -------------------------------------------------
    def eval_js(self, script):
        try:
            self.webview.evaluateJavaScript_completionHandler_(script, None)
        except Exception:
            import traceback
            traceback.print_exc()

    def push_js(self, fn_name, obj):
        """Call a JS function with a single JSON argument."""
        self.eval_js("window.%s && window.%s(%s);" % (fn_name, fn_name, json.dumps(obj)))

    def show_window(self):
        # Become a Regular app FIRST: an accessory app can't reliably come to
        # the front, which is why Settings used to open behind other windows.
        _set_activation_policy(True)
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()
        # The Accessory -> Regular switch is applied asynchronously by the
        # WindowServer, so an activation issued in the same turn can be ignored
        # and the window opens behind whatever you were using. Re-assert on the
        # next runloop turns, once the new policy has actually taken hold.
        for delay in (0.05, 0.20):
            self.performSelector_withObject_afterDelay_("raiseWindow", None, delay)

    def raiseWindow(self):
        try:
            if not self.window.isVisible():
                return
            NSApp.activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)
        except Exception:
            pass

    def close_window(self):
        self.window.close()


def _install_edit_menu():
    """Give the app an Edit menu wired to the standard responder actions, so
    Cmd+C/V/X/A/Z work in the web-view text fields.

    Yap is a menu-bar (LSUIElement) rumps app with no application menu, so the
    WKWebView's inputs receive no standard editing commands. The menu bar isn't
    visible for an accessory app, but its key equivalents are still routed to
    the key window's first responder (the web view), which is all we need.
    Idempotent: only the first call builds the menu."""
    global _EDIT_MENU_INSTALLED
    if _EDIT_MENU_INSTALLED:
        return
    main = NSMenu.alloc().init()

    edit_item = NSMenuItem.alloc().init()
    main.addItem_(edit_item)
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    edit_item.setSubmenu_(edit_menu)

    def add(title, action, key, shift=False):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, action, key)
        if shift:
            from AppKit import NSEventModifierFlagCommand, NSEventModifierFlagShift
            item.setKeyEquivalentModifierMask_(
                NSEventModifierFlagCommand | NSEventModifierFlagShift)
        edit_menu.addItem_(item)

    add("Undo", "undo:", "z")
    add("Redo", "redo:", "z", shift=True)
    edit_menu.addItem_(NSMenuItem.separatorItem())
    add("Cut", "cut:", "x")
    add("Copy", "copy:", "c")
    add("Paste", "paste:", "v")
    add("Select All", "selectAll:", "a")

    NSApp().setMainMenu_(main)
    _EDIT_MENU_INSTALLED = True


def open_window(html_path, web_root, title, size=(720, 600),
                resizable=True, min_size=(620, 460),
                on_message=None, on_close=None):
    """Create and show a web-view window. Must be called on the main thread."""
    _install_edit_menu()
    ctrl = YapWebWindow.alloc().init()
    ctrl._on_message = on_message
    ctrl._on_close = on_close

    config = WKWebViewConfiguration.alloc().init()
    ucc = WKUserContentController.alloc().init()
    ucc.addScriptMessageHandler_name_(ctrl, "yap")
    config.setUserContentController_(ucc)

    w, h = size
    frame = NSMakeRect(0, 0, w, h)
    webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
    ctrl.webview = webview

    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
             | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskFullSizeContentView)
    if resizable:
        style |= NSWindowStyleMaskResizable
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, style, NSBackingStoreBuffered, False)
    win.setTitle_(title)
    # Let the web content draw under the title bar (the design has its own
    # gradient header); the traffic-light buttons stay.
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(NSWindowTitleHidden)
    win.setBackgroundColor_(NSColor.whiteColor())
    if resizable:
        win.setContentMinSize_(min_size)
    # The web view fills the window; an invisible strip over the HTML title bar
    # gives the window something draggable (see _DragStrip).
    container = NSView.alloc().initWithFrame_(frame)
    webview.setFrame_(frame)
    webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    container.addSubview_(webview)
    strip = _DragStrip.alloc().initWithFrame_(
        NSMakeRect(0, h - TITLEBAR_HEIGHT, w, TITLEBAR_HEIGHT))
    strip.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
    container.addSubview_(strip)
    win.setContentView_(container)
    win.setReleasedWhenClosed_(False)
    win.setDelegate_(ctrl)
    win.center()
    ctrl.window = win

    url = NSURL.fileURLWithPath_(html_path)
    # Allow read access to the PARENT of web/ (the Resources root) so the page
    # can also load sibling assets like ../assets/app-logo.png.
    read_root = os.path.dirname(os.path.normpath(web_root)) or web_root
    root = NSURL.fileURLWithPath_isDirectory_(read_root, True)
    webview.loadFileURL_allowingReadAccessToURL_(url, root)

    _LIVE.add(ctrl)
    ctrl.show_window()
    return ctrl


class YapPanel(NSObject):
    """A borderless, NON-ACTIVATING panel hosting a WKWebView — the quick panel.

    Non-activating is the key: the panel never becomes the key window, so the
    app you're dictating into keeps focus and the simulated Cmd+V paste still
    lands there. That makes it safe to show the live "Listening…" surface during
    a recording without breaking the core flow."""

    def userContentController_didReceiveScriptMessage_(self, ucc, message):
        try:
            body = message.body()
            try:
                data = json.loads(body) if isinstance(body, str) else _to_py(body)
            except Exception:
                data = _to_py(body)
            if self._on_message is not None:
                self._on_message((data or {}).get("action"), (data or {}).get("payload"), self)
        except Exception:
            import traceback
            traceback.print_exc()

    def push_js(self, fn_name, obj):
        try:
            self.webview.evaluateJavaScript_completionHandler_(
                "window.%s && window.%s(%s);" % (fn_name, fn_name, json.dumps(obj)), None)
        except Exception:
            pass

    def is_visible(self):
        return bool(self.window.isVisible())

    def resize_to(self, w, h):
        self.window.setContentSize_((w, h))

    def show_at(self, anchor):
        """Show anchored under `anchor` = (x, y, w, h) screen rect of the status
        item button. Falls back to the top-right corner under the menu bar."""
        w = self.window.frame().size.width
        h = self.window.frame().size.height
        screen = NSScreen.mainScreen().frame()
        if anchor:
            ax, ay, aw, ah = anchor
            x = ax + aw - w
            y = ay - h - 4
        else:
            x = screen.origin.x + screen.size.width - w - 12
            y = screen.origin.y + screen.size.height - h - 36
        # keep on-screen
        x = max(screen.origin.x + 6, min(x, screen.origin.x + screen.size.width - w - 6))
        self.window.setFrameOrigin_((x, y))
        self.window.orderFrontRegardless()

    def hide_now(self):
        self.window.orderOut_(None)


def open_panel(html_path, web_root, size=(344, 500), on_message=None):
    """Create the quick panel (hidden until show_at). Main thread only."""
    ctrl = YapPanel.alloc().init()
    ctrl._on_message = on_message

    config = WKWebViewConfiguration.alloc().init()
    ucc = WKUserContentController.alloc().init()
    ucc.addScriptMessageHandler_name_(ctrl, "yap")
    config.setUserContentController_(ucc)

    w, h = size
    frame = NSMakeRect(0, 0, w, h)
    webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
    webview.setWantsLayer_(True)
    webview.layer().setCornerRadius_(18.0)
    webview.layer().setMasksToBounds_(True)
    ctrl.webview = webview

    style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, style, NSBackingStoreBuffered, False)
    panel.setLevel_(NSPopUpMenuWindowLevel)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setFloatingPanel_(True)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
    panel.setContentView_(webview)
    panel.setReleasedWhenClosed_(False)
    ctrl.window = panel

    url = NSURL.fileURLWithPath_(html_path)
    read_root = os.path.dirname(os.path.normpath(web_root)) or web_root
    webview.loadFileURL_allowingReadAccessToURL_(
        url, NSURL.fileURLWithPath_isDirectory_(read_root, True))

    _LIVE.add(ctrl)
    return ctrl


# ---------------------------------------------------------------------------
# Menu-bar status-item click routing
# ---------------------------------------------------------------------------

class _StatusClick(NSObject):
    """Target for the menu-bar status item's button. A normal (left) click runs
    `on_left`; a right-click or Control-click runs `on_right`."""

    def clicked_(self, sender):
        try:
            ev = NSApp.currentEvent()
            right = False
            if ev is not None:
                right = (ev.type() == NSEventTypeRightMouseUp
                         or bool(ev.modifierFlags() & NSEventModifierFlagControl))
            fn = self._on_right if right else self._on_left
            if fn is not None:
                fn()
        except Exception:
            import traceback
            traceback.print_exc()


def install_status_click(button, on_left, on_right):
    """Route clicks on the menu-bar status item's `button` to Python callables.
    Returns the handler (kept alive in _LIVE). Caller must first detach the
    status item's menu (setMenu_(None)) so the button fires this action."""
    handler = _StatusClick.alloc().init()
    handler._on_left = on_left
    handler._on_right = on_right
    button.setTarget_(handler)
    button.setAction_("clicked:")
    button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
    _LIVE.add(handler)
    return handler
