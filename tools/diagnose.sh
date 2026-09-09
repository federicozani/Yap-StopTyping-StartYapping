#!/bin/bash
# Yap — "it doesn't work on this Mac" diagnostic.
#
# Run this ON THE MAC WHERE YAP MISBEHAVES:
#     bash diagnose.sh
#
# It only reads; it changes nothing. It prints the handful of things that
# actually stop Yap working on a second Mac, and the exact command to fix each.
#
# Everything it prints is ALSO written to ~/Desktop/yap-diagnostic.txt, so
# there is one file to send back rather than a screenful to copy.

OUT="${YAP_DIAG_OUT:-$HOME/Desktop/yap-diagnostic.txt}"
# Send everything below to the screen AND to the file.
exec > >(tee "$OUT") 2>&1

echo "=============================================="
echo " Yap diagnostic — $(date)"
echo "=============================================="
echo

echo "--- This Mac ---"
echo "macOS   : $(sw_vers -productVersion) ($(sw_vers -buildVersion))"
ARCH=$(uname -m)
echo "Processor: $ARCH"
if [ "$ARCH" != "arm64" ]; then
  echo "  *** PROBLEM: Yap is built for Apple Silicon and cannot run on this"
  echo "  *** Intel Mac. It will quit with a message at launch. You need a"
  echo "  *** build made on an Intel Mac."
fi
echo

echo "--- Copies of Yap on this Mac ---"
# Spotlight alone is not enough: it does not index every folder, and it misses
# a freshly built copy. Combine it with a direct search of the usual places so
# a second copy can't hide from this check.
# Spotlight alone is not enough: it does not index every folder and misses a
# freshly built copy. `find` alone is not enough either — macOS blocks reading
# Desktop/Documents/Downloads from Terminal unless it has been granted access,
# so those searches quietly fail. Combine all three, including the path of the
# copy that is actually running, which is authoritative regardless.
RUNNING_APP=""
RUNPID=$(pgrep -x Yap 2>/dev/null | head -1)
if [ -n "$RUNPID" ]; then
  RUNNING_APP=$(ps -o command= -p "$RUNPID" 2>/dev/null | sed 's|/Contents/MacOS/Yap.*||')
fi
COPIES=$( { mdfind -name "Yap.app" 2>/dev/null
            find /Applications ~/Applications ~/Desktop ~/Downloads ~/Documents \
                 -maxdepth 4 -name "Yap.app" -type d 2>/dev/null
            ls -d /Applications/Yap.app ~/Applications/Yap.app \
                  ~/Downloads/Yap.app ~/Desktop/Yap.app 2>/dev/null
            [ -n "$RUNNING_APP" ] && echo "$RUNNING_APP"
          } | grep -v "/dist/" | sort -u )
N=0
while IFS= read -r app; do
  [ -z "$app" ] && continue
  N=$((N+1))
  V=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$app/Contents/Info.plist" 2>/dev/null)
  MARK=""
  [ "$app" = "$RUNNING_APP" ] && MARK="   <-- this is the one running"
  echo "  [$N] $app   (version ${V:-unknown})$MARK"
done <<< "$COPIES"
if [ "$N" -gt 1 ]; then
  echo "  *** PROBLEM: more than one copy. Two copies fight over the keyboard"
  echo "  *** shortcut, and macOS grants permissions to only one of them."
  echo "  *** Fix: drag every copy but /Applications/Yap.app to the Trash,"
  echo "  ***      then empty it, then reboot."
fi
[ "$N" -eq 0 ] && echo "  (none found)"
echo

echo "--- Is Yap running right now? ---"
if pgrep -x Yap >/dev/null 2>&1; then
  echo "  yes — pid $(pgrep -x Yap | tr '\n' ' ')"
  echo "  (if you see no icon in the menu bar, that is the 'running but"
  echo "   invisible' case — the menu bar is probably full.)"
else
  echo "  no"
fi
echo

APP=/Applications/Yap.app
if [ ! -d "$APP" ]; then
  echo "*** Yap is not in /Applications. Move it there in Finder before granting"
  echo "*** permissions — macOS ties them to the app's location."
  APP=""
fi

if [ -n "$APP" ]; then
echo "--- Is it still marked as downloaded? ---"
Q=$(xattr -p com.apple.quarantine "$APP" 2>/dev/null)
if [ -n "$Q" ]; then
  echo "  quarantine flag: $Q"
  echo "  *** PROBLEM: macOS may run a downloaded app from a hidden read-only"
  echo "  *** copy (App Translocation). Permissions can NEVER stick to it."
  echo "  *** Fix: xattr -dr com.apple.quarantine $APP"
else
  echo "  clean (no quarantine flag)"
fi
echo

echo "--- Signature (the app) ---"
codesign -dv --verbose=4 "$APP" 2>&1 | sed 's/^/  /'
codesign -d -r- "$APP" 2>&1 | grep designated | sed 's/^/  /'
if codesign --verify --deep --strict "$APP" 2>/dev/null; then
  echo "  signature is intact"
else
  echo "  *** PROBLEM: the signature is broken — the app was modified or the"
  echo "  *** copy is incomplete. Copy Yap.app over again."
  codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -5 | sed 's/^/  /'
fi
echo

echo "--- Signature (the bundled Python framework) ---"
# A nested component that claims Apple's identifier but is signed with our own
# certificate is exactly what newer macOS refuses to load. If the app is being
# killed at launch for a code-signing reason, it shows up here.
FW="$APP/Contents/Frameworks/Python3.framework"
if [ -d "$FW" ]; then
  codesign -dv --verbose=4 "$FW" 2>&1 | grep -E "^Identifier|^TeamIdentifier|^Authority|^Format" | sed 's/^/  /'
  if codesign --verify --strict "$FW" 2>/dev/null; then
    echo "  nested framework signature is intact"
  else
    echo "  *** PROBLEM: the nested Python framework fails verification."
  fi
else
  echo "  (not found)"
fi
echo
fi

echo "--- Permissions macOS has recorded ---"
DB="$HOME/Library/Application Support/com.apple.TCC/TCC.db"
if [ -r "$DB" ]; then
  sqlite3 "$DB" "select service, auth_value from access where client='com.yap.app';" 2>/dev/null \
    | sed 's/^/  /' || echo "  (cannot read — Terminal needs Full Disk Access to see this)"
else
  echo "  (cannot read the permission database — that's normal)"
fi
echo "  Check by eye in System Settings > Privacy & Security:"
echo "    Microphone / Input Monitoring / Accessibility — Yap must be ON in all three."
echo
echo "  *** If a toggle is already ON but Yap still does nothing, macOS is holding"
echo "  *** a stale entry from the OLD copy. That is the usual cause. Fix:"
echo "  ***   tccutil reset Microphone com.yap.app"
echo "  ***   tccutil reset ListenEvent com.yap.app"
echo "  ***   tccutil reset Accessibility com.yap.app"
echo "  *** then reopen Yap and allow all three again when asked."
echo

echo "--- Menu-bar icon state ---"
# macOS remembers, per app, whether a status item is visible and where it sits.
# When the menu bar is full it hides the item and PERSISTS that, so the icon
# never comes back on later launches either. These keys are that memory.
DEF=$(defaults read com.yap.app 2>/dev/null)
if [ -n "$DEF" ]; then
  echo "$DEF" | sed 's/^/  /'
  if echo "$DEF" | grep -qi "NSStatusItem Visible.*0"; then
    echo "  *** PROBLEM: macOS has recorded the menu-bar icon as HIDDEN."
    echo "  *** Usually means the menu bar was full when Yap last started."
    echo "  *** Fix: defaults delete com.yap.app; then reopen Yap."
  fi
else
  echo "  (no saved preferences — normal on a first run)"
fi
echo

echo "--- Crash reports ---"
# The Exception Type / Termination Reason lines say what actually killed it:
# a code-signing kill, a native crash inside CoreAudio/WebKit, or something else.
FOUND=0
for DIR in "$HOME/Library/Logs/DiagnosticReports" "/Library/Logs/DiagnosticReports"; do
  [ -d "$DIR" ] || continue
  while IFS= read -r rep; do
    [ -z "$rep" ] && continue
    FOUND=$((FOUND+1))
    echo
    echo "  === $rep"
    echo "  --- when: $(stat -f '%Sm' "$rep" 2>/dev/null)"
    grep -m1 -E '"exception"|Exception Type'      "$rep" 2>/dev/null | sed 's/^/  /'
    grep -m1 -E '"termination"|Termination Reason' "$rep" 2>/dev/null | sed 's/^/  /'
    grep -m1 -E '"is_corpse"|Crashed Thread'       "$rep" 2>/dev/null | sed 's/^/  /'
    echo "  --- first 40 lines of the report:"
    head -40 "$rep" 2>/dev/null | sed 's/^/  | /'
  done <<< "$(ls -t "$DIR"/Yap-*.ips "$DIR"/Yap_*.ips "$DIR"/python*-*.ips 2>/dev/null | head -3)"
done
if [ "$FOUND" -eq 0 ]; then
  echo "  (none found — Yap has not crashed in a way macOS recorded)"
fi
echo

echo "--- Was Yap blocked by the system? ---"
# amfid/taskgated refusing the signature shows up here and nowhere else.
log show --last 2h --style compact \
    --predicate 'process == "amfid" OR process == "taskgated" OR process == "kernel"' 2>/dev/null \
  | grep -i -E "yap|com\.yap\.app" | tail -20 | sed 's/^/  /' \
  || echo "  (log show unavailable)"
echo
echo "--- What macOS logged for Yap itself (last 2h) ---"
log show --last 2h --style compact --predicate 'process == "Yap"' 2>/dev/null \
  | tail -40 | sed 's/^/  /' || echo "  (log show unavailable)"
echo

echo "--- What Yap itself reported ---"
if [ -f "$HOME/.yap/yap.log" ]; then
  YLOG="$HOME/.yap/yap.log"
  echo "  ($(wc -l < "$YLOG" | tr -d ' ') lines total, $(du -h "$YLOG" | cut -f1))"
  echo
  echo "  --- every launch banner, warning and error in the whole log:"
  # The log runs to tens of thousands of lines, which is too much to send. These
  # are the lines that ever matter: which build started, and everything that
  # went wrong.
  grep -n -E "Yap [0-9]|running from:|WARNING|ERROR|FATAL|Traceback|TRANSLOCATED|not granted|INACTIVE|re-enabled|menu-bar" "$YLOG" \
    2>/dev/null | tail -120 | sed 's/^/  /'
  echo
  echo "  --- last 200 lines:"
  tail -200 "$YLOG" | sed 's/^/  /'
else
  echo "  ~/.yap/yap.log does not exist — Yap has never started on this Mac."
  echo "  *** That points at a launch failure, not a permission problem."
fi
echo

echo "--- Native crash trace, if any ---"
if [ -f "$HOME/.yap/yap-crash.log" ]; then
  cat "$HOME/.yap/yap-crash.log" | sed 's/^/  /'
else
  echo "  (none — this file only exists from Yap 3.2 onward, after a hard crash)"
fi
echo

echo "=============================================="
echo " Saved to: $OUT"
echo " Send that file for a diagnosis."
echo "=============================================="
