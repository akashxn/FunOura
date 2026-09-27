#!/bin/bash
# Build FunOura.app, the menu bar app. setup.sh in the folder above runs this for you.
#   ./build.sh          builds FunOura.app here
#   ./build.sh --open   builds it and starts it
set -euo pipefail
cd "$(dirname "$0")"
APP=FunOura.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp Info.plist "$APP/Contents/Info.plist"
# Where this clone lives, so the app finds the server even if FunOura.app is moved to /Applications.
/usr/libexec/PlistBuddy -c "Add :FunOuraHome string $(cd .. && pwd)" "$APP/Contents/Info.plist"
cp Assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"  # made from Assets/logo-midnight.png
swiftc -O -parse-as-library -swift-version 5 -target "$(uname -m)-apple-macosx14.0" \
  Sources/*.swift -o "$APP/Contents/MacOS/FunOura"
xattr -cr "$APP"  # files from the Desktop or a download carry metadata that codesign refuses
# Ad-hoc signing sometimes fails on the first try; try a few times.
for attempt in 1 2 3; do
  codesign -s - --force "$APP" 2>/dev/null && break
  [ "$attempt" = 3 ] && { echo "codesign failed: run ./build.sh again" >&2; exit 1; }
  sleep 1
done
echo "Built $(pwd)/$APP"
if [ "${1:-}" = "--open" ]; then
  osascript -e 'quit app "FunOura"' 2>/dev/null || true  # a clean quit also stops its server
  sleep 2
  open "$APP"
fi
