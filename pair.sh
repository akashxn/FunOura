#!/bin/bash
# Pair FunOura with your ring: install a new auth key on a factory-reset ring, switch on its heart
# rate and blood oxygen measurements, and save which ring it is to ring.json.
#
# Pairing takes the ring over from the Oura app. Use a spare or retired ring. See the README.
# Run it in a terminal app that macOS lets use Bluetooth (it asks the first time).
set -euo pipefail
cd "$(dirname "$0")"
OURA=open_oura/target/release/oura
KEY=ring.key

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

[ -x "$OURA" ] || fail "Run ./setup.sh first: it builds the ring client this needs."
if [ -f ring.json ]; then
  echo "A ring is already paired (ring.json). Pairing again replaces it."
  read -r -p "Pair a ring anyway? [y/N] " again
  [[ "$again" =~ ^[Yy]$ ]] || exit 0
fi

bold "Before you start"
cat <<'TXT'
  • Pairing installs FunOura's own key on the ring. The Oura app and your Oura
    account stop working with that ring until you reset it and set it up there
    again. Use a spare or retired ring, not the one you rely on.
  • The ring must be factory-reset first. With the Gen3 / Ring 4 charging dock:
      1. Take the ring off the dock. Put it back and wait about 2 seconds.
      2. Flip the dock, ring and all, upside down: wait for BLUE.
      3. Upright: wait for RED.  4. Upside down: MAGENTA.  5. Upright: YELLOW.
      Yellow means the reset started; a few minutes later the light blinks blue.
    (Details: open_oura/docs/factory-reset.md)
  • Keep any other Oura ring (and phones paired to it) away, or its Bluetooth off.
TXT
read -r -p "Is the ring factory-reset, charged and next to this Mac? [y/N] " ready
[[ "$ready" =~ ^[Yy]$ ]] || { echo "No problem. Run ./pair.sh again when it is."; exit 0; }

bold "Looking for Oura rings (about 25 seconds)"
echo "If this stops with \"Abort trap: 6\", your terminal app isn't allowed Bluetooth: see Troubleshooting in the README."
status=0
scan=$("$OURA" scan 2>&1) || status=$?  # lists devices whose name contains "Oura"
[ "$status" -ne 134 ] || fail "macOS didn't let this terminal app use Bluetooth. See \"Bluetooth\" under Troubleshooting in the README."
ids=$(printf '%s\n' "$scan" | grep -Eo '[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}' | awk '!seen[$0]++')
[ -n "$ids" ] || fail "No ring found. Put the ring on its charger for a moment to wake it, then run ./pair.sh again."
echo
echo "A freshly reset ring usually shows up as \"Oura <its serial number>\"."
i=0
while read -r id; do
  i=$((i + 1))
  echo "  $i) $(printf '%s\n' "$scan" | grep -m1 "$id" | sed "s/ *($id)//" | tr -s ' ')   [$id]"
done <<< "$ids"
read -r -p "Which one is the reset ring? [1-$i] " pick
address=$(printf '%s\n' "$ids" | sed -n "${pick}p")
[ -n "$address" ] || fail "That isn't one of the numbers above."

bold "Pairing with $address"
[ -f "$KEY" ] && mv "$KEY" "$KEY.old"
"$OURA" --name "" --address "$address" --key-file "$KEY" pair
[ -s "$KEY" ] || fail "Pairing didn't finish, so nothing was saved. Reset the ring and try again."

bold "Switching on heart rate and blood oxygen"
"$OURA" --name "" --address "$address" --key-file "$KEY" features --enable-hr --enable-spo2 \
  || echo "That didn't finish; the Health tab may stay empty. Run ./pair.sh again later to retry."

bold "Checking the ring"
"$OURA" --name "" --address "$address" --key-file "$KEY" info || true

printf '{"address": "%s", "key_file": "%s"}\n' "$address" "$KEY" > ring.json
chmod 600 "$KEY"
echo
bold "Paired. Open FunOura: open funoura/FunOura.app"
