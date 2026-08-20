#!/bin/bash
# Create the stable self-signed certificate Yap is signed with.
#
# WHY THIS EXISTS
# py2app leaves the bundle AD-HOC signed. An ad-hoc signature's "designated
# requirement" — the thing macOS remembers when you grant a permission — is the
# bundle's CONTENT HASH:
#     designated => cdhash H"ca3cc5020c5b…"
# That hash changes on every single rebuild, so macOS decides it's a different
# app and silently stops honouring Input Monitoring and Accessibility. The
# toggles still show as ON in System Settings, but the hotkey quietly stops
# working, and the only cure is `tccutil reset` + granting again. Every rebuild.
#
# Signing with a stable certificate instead gives:
#     designated => identifier "com.yap.app" and certificate root = H"44a372f8…"
# The certificate hash never changes, so every future rebuild keeps the same
# identity and the permissions stick.
#
# Run once per machine. Safe to re-run: it recreates the keychain from scratch.
set -e

KEYCHAIN="yap-signing.keychain-db"
KEYCHAIN_PW="yapdev"
CERT_NAME="Yap Local Dev"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> generating a self-signed code-signing certificate"
openssl req -x509 -newkey rsa:2048 -keyout "$WORK/key.pem" -out "$WORK/cert.pem" \
  -days 7300 -nodes -subj "/CN=$CERT_NAME/O=Yap/C=IT" \
  -addext "extendedKeyUsage=critical,codeSigning" \
  -addext "basicConstraints=critical,CA:false" \
  -addext "keyUsage=critical,digitalSignature" 2>/dev/null

# macOS's importer can't read OpenSSL 3's default PKCS12 encryption, so ask for
# the legacy algorithms it does understand.
openssl pkcs12 -export -out "$WORK/yap.p12" -inkey "$WORK/key.pem" \
  -in "$WORK/cert.pem" -passout "pass:$KEYCHAIN_PW" -name "$CERT_NAME" \
  -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 2>/dev/null

echo "==> importing into a dedicated keychain (never prompts for your password)"
security delete-keychain "$KEYCHAIN" 2>/dev/null || true
security create-keychain -p "$KEYCHAIN_PW" "$KEYCHAIN"
security unlock-keychain -p "$KEYCHAIN_PW" "$KEYCHAIN"
security set-keychain-settings "$KEYCHAIN"          # no auto-lock, no timeout
security import "$WORK/yap.p12" -k "$KEYCHAIN" -P "$KEYCHAIN_PW" \
  -T /usr/bin/codesign -A
# Let codesign use the private key without a GUI authorisation prompt.
security set-key-partition-list -S apple-tool:,apple:,codesign: \
  -s -k "$KEYCHAIN_PW" "$KEYCHAIN" >/dev/null 2>&1
# Keep it in the search list so codesign can find it.
security list-keychains -d user -s \
  $(security list-keychains -d user | tr -d ' "' | grep -v "^$KEYCHAIN$") "$KEYCHAIN"

HASH="$(security find-certificate -c "$CERT_NAME" -Z "$KEYCHAIN" | awk '/SHA-1 hash/{print $3}')"
echo "==> done. certificate SHA-1: $HASH"
echo "    setup_app.py picks this up automatically on the next build."
