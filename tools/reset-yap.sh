#!/bin/bash
# Yap — FULL RESET. Makes this Mac forget Yap completely, so the next launch is
# a genuine first run: onboarding from scratch, no API key, macOS asking for all
# three permissions again.
#
#     bash reset-yap.sh            # asks before deleting anything
#     bash reset-yap.sh --dry-run  # shows what it would do, changes nothing
#
# It will ask for your password: the Accessibility and Input Monitoring records
# live in a SYSTEM database, and plain `tccutil reset` as a normal user does not
# clear them — which is why the toggles keep reading "on" after a reset.

set -u
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

say()  { echo "$@"; }
run()  { if [ $DRY -eq 1 ]; then echo "    would run: $*"; else "$@" >/dev/null 2>&1; fi; }
zap()  { if [ -e "$1" ]; then if [ $DRY -eq 1 ]; then echo "    would delete: $1"; else rm -rf "$1" && echo "    deleted: $1"; fi; else echo "    (absent):    $1"; fi; }

echo "=================================================="
echo " Yap — full reset"
[ $DRY -eq 1 ] && echo " DRY RUN — nothing will be changed"
echo "=================================================="
echo

# ---------------------------------------------------------------- 1. copies
echo "1. Copies of Yap on this Mac"
COPIES=$(mdfind -name "Yap.app" 2>/dev/null; ls -d /Applications/Yap.app ~/Applications/Yap.app ~/Downloads/Yap.app ~/Desktop/Yap*.app 2>/dev/null)
COPIES=$(echo "$COPIES" | sed '/^$/d' | sort -u)
if [ -z "$COPIES" ]; then
  echo "    none found"
else
  while IFS= read -r a; do
    V=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$a/Contents/Info.plist" 2>/dev/null)
    echo "    $a   (version ${V:-unknown})"
  done <<< "$COPIES"
  N=$(echo "$COPIES" | wc -l | tr -d ' ')
  if [ "$N" -gt 1 ]; then
    echo
    echo "    *** KEEP ONLY ONE. Delete the others by hand and empty the Trash."
    echo "    *** Two copies share one bundle id, so macOS cannot tell them apart"
    echo "    *** and the permissions you grant may belong to the copy you are"
    echo "    *** NOT running."
  fi
fi
echo

if [ $DRY -eq 0 ]; then
  echo "This will delete your Yap settings and API key on THIS Mac, and revoke"
  echo "its permissions. The app itself is not deleted."
  printf "Type  yes  to continue: "
  read -r ANS
  [ "$ANS" = "yes" ] || { echo "Cancelled."; exit 0; }
  echo
fi

# ------------------------------------------------------------------ 2. quit
echo "2. Quitting Yap"
run osascript -e 'tell application "Yap" to quit'
sleep 2
run pkill -f "Yap.app/Contents/MacOS/Yap"
sleep 1
if pgrep -f "Yap.app/Contents/MacOS/Yap" >/dev/null 2>&1; then
  echo "    *** still running — quit it from the menu bar, then run this again"
else
  echo "    not running"
fi
echo

# --------------------------------------------------------------- 3. settings
echo "3. Yap's own settings, key and logs"
zap "$HOME/.yap"
echo "    ... and the OLD Parla folder, which Yap copies your key back FROM"
echo "    on first run if it is left behind:"
zap "$HOME/.parla"
echo

# ------------------------------------------------------ 4. macOS leftovers
echo "4. macOS caches for com.yap.app"
zap "$HOME/Library/Caches/com.yap.app"
zap "$HOME/Library/WebKit/com.yap.app"
zap "$HOME/Library/HTTPStorages/com.yap.app"
zap "$HOME/Library/HTTPStorages/com.yap.app.binarycookies"
zap "$HOME/Library/Saved Application State/com.yap.app.savedState"
zap "$HOME/Library/Preferences/com.yap.app.plist"
zap "$HOME/Library/Application Support/com.yap.app"
zap "$HOME/Library/Containers/com.yap.app"
zap "$HOME/Library/Group Containers/com.yap.app"
run defaults delete com.yap.app
echo

# --------------------------------------------------- 4b. quarantine + leak
echo "4b. Download flag and key-leak check"
if [ -d /Applications/Yap.app ]; then
  QN=$(xattr -r -p com.apple.quarantine /Applications/Yap.app 2>/dev/null | wc -l | tr -d " ")
  if [ "${QN:-0}" != "0" ]; then
    echo "    $QN file(s) marked as downloaded — REMOVING."
    echo "    (A quarantined app is blocked by Gatekeeper, and when it does run"
    echo "     macOS may run it from a randomised read-only copy, where granted"
    echo "     permissions can never stick. Never re-add this flag.)"
    run xattr -dr com.apple.quarantine /Applications/Yap.app
  else
    echo "    clean — no download flag"
  fi
  if grep -rEl 'AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9]{20,}' /Applications/Yap.app >/dev/null 2>&1; then
    echo "    *** WARNING: an API key is baked into the app bundle."
    echo "    *** Do NOT share this build, and rotate that key."
  else
    echo "    no API key inside the app bundle"
  fi
fi
echo

# --------------------------------------------------- 5. re-register FIRST
echo "5. Making sure macOS can find the app"
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
if [ -d /Applications/Yap.app ]; then
  [ -x "$LSREG" ] && run "$LSREG" -f /Applications/Yap.app
  echo "    registered /Applications/Yap.app"
else
  echo "    *** /Applications/Yap.app does not exist."
  echo "    *** tccutil can only reset a bundle id macOS can resolve, so the"
  echo "    *** next step WILL fail. Put Yap.app in /Applications first."
fi
echo

# ------------------------------------------------------------ 6. permissions
echo "6. Permissions (needs your password)"
echo "    Watch for 'No such bundle identifier' below — that means the reset"
echo "    did NOT happen, and is the usual reason a toggle stays on."
FAILED=0
for S in Microphone ListenEvent Accessibility PostEvent All; do
  printf "    %-14s : " "$S"
  if [ $DRY -eq 1 ]; then echo "would reset (user + sudo)"; continue; fi
  OUT=$(tccutil reset "$S" com.yap.app 2>&1); RC=$?
  OUT2=$(sudo tccutil reset "$S" com.yap.app 2>&1); RC2=$?
  if [ $RC -eq 0 ] || [ $RC2 -eq 0 ]; then
    echo "reset"
  else
    echo "FAILED -> ${OUT2:-$OUT}"
    FAILED=1
  fi
done
if [ "$FAILED" = "1" ]; then
  echo
  echo "    *** At least one reset failed. Remove the rows by hand instead:"
  echo "    *** System Settings > Privacy & Security > Microphone /"
  echo "    *** Input Monitoring / Accessibility — click Yap, press  -  ."
fi
echo

echo "=================================================="
echo " Now:"
echo "   1. REBOOT. macOS caches permission decisions until you do."
echo "   2. Open Yap. You should get the welcome screen with NO API key."
echo "   3. Allow all three permissions when asked."
echo "   4. Quit and reopen Yap once — Input Monitoring only takes"
echo "      effect after a restart."
echo "   5. Check the menu-bar menu says the version you expect."
echo "=================================================="
