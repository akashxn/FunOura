#!/bin/bash
# One-time setup: checks the tools, builds open_oura (the ring client) and builds FunOura.app.
# Safe to run again: it skips what's already done. Next step after this: ./pair.sh
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)
OPEN_OURA_REPO=https://github.com/Th0rgal/open_oura.git
OPEN_OURA_COMMIT=c36cd720512a9472f6caf75701052ea8461e8a1e  # the version patches/ was made against

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }
ok() { printf '\033[32m✓\033[0m %s\n' "$1"; }

bold "Checking what FunOura needs"
[ "$(uname)" = Darwin ] || fail "FunOura is a Mac app."
major=$(sw_vers -productVersion | cut -d. -f1)
[ "$major" -ge 14 ] || fail "FunOura needs macOS 14 (Sonoma) or newer; this Mac has $(sw_vers -productVersion)."
ok "macOS $(sw_vers -productVersion)"
xcode-select -p >/dev/null 2>&1 && command -v swiftc >/dev/null \
  || fail "Xcode Command Line Tools are missing. Install them with: xcode-select --install"
ok "Swift $(swiftc --version 2>/dev/null | head -1 | sed 's/.*version \([0-9.]*\).*/\1/')"
command -v git >/dev/null || fail "git is missing (it comes with the Command Line Tools: xcode-select --install)"
command -v python3 >/dev/null || fail "python3 is missing (it comes with the Command Line Tools: xcode-select --install)"
ok "Python $(python3 -c 'import platform; print(platform.python_version())')"
if ! command -v cargo >/dev/null; then
  [ -x "$HOME/.cargo/bin/cargo" ] && export PATH="$HOME/.cargo/bin:$PATH"
fi
command -v cargo >/dev/null || fail "Rust is missing. Install it with:
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
then open a new terminal and run ./setup.sh again."
ok "Rust $(cargo --version | cut -d' ' -f2)"

bold "Building open_oura, the ring client (by Thomas Marchand, MIT)"
if [ ! -d open_oura/.git ]; then
  git clone --quiet "$OPEN_OURA_REPO" open_oura
fi
cd open_oura
if ! grep -q '"jsonl"\|jsonl: bool' crates/oura-cli/src/main.rs 2>/dev/null; then
  git checkout --quiet "$OPEN_OURA_COMMIT"
  git apply "$ROOT/patches/open_oura-funoura.patch"
  ok "Applied FunOura's patch (accelerometer stream as JSON lines)"
fi
cargo build --release --quiet
ok "open_oura/target/release/oura"
cd "$ROOT"

bold "Getting the ring mouse ready"
for f in calibration.json taps.json; do
  if [ ! -f "ringmouse/$f" ]; then
    cp "ringmouse/defaults/$f" "ringmouse/$f"
    ok "Starter ringmouse/$f (made on someone else's hand: Calibrate in the app to fit yours)"
  fi
done

bold "Building FunOura.app"
funoura/build.sh >/dev/null
ok "funoura/FunOura.app"

echo
bold "Done. Next:"
if [ -f ring.json ]; then
  echo "  open funoura/FunOura.app"
else
  echo "  1. ./pair.sh                   pair your ring (read the warning in the README first)"
  echo "  2. open funoura/FunOura.app    then click the ring icon in the menu bar"
fi
