#!/bin/bash
# Run the ring mouse from a terminal, without FunOura. FunOura's Hands Free and Remote tabs do
# the same thing with buttons. Use a terminal app that's allowed Bluetooth and Accessibility.
#
#   ./run.sh                   # 10 minutes, using your saved calibration
#   ./run.sh 30                # 30 minutes
#   ./run.sh --recalibrate     # redo the five setup poses first
#   ./run.sh --remote slides   # a presentation remote instead of the cursor (or --remote media)
#   ./run.sh --flip-scroll     # other flags go to ringmouse.py (python3 ringmouse.py --help)
#
# Quit with Ctrl-C (not Ctrl-Z).
cd "$(dirname "$0")" || exit 1
# Ctrl-Z makes both programs quit cleanly; this wrapper must not pause meanwhile, or the
# terminal takes over and they get frozen on their way out with the ring still streaming.
trap '' TSTP
minutes=10
if [[ "$1" =~ ^[0-9]+$ ]]; then
    minutes=$1
    shift
fi
# The paired ring, from ../ring.json (written by ../pair.sh).
read -r address key < <(python3 - <<'PY' 2>/dev/null
import json, pathlib
root = pathlib.Path("..").resolve()
cfg = json.loads((root / "ring.json").read_text())
key = pathlib.Path(cfg["key_file"])
print(cfg["address"], key if key.is_absolute() else root / key)
PY
)
if [ -z "$address" ]; then
    echo "No ring is paired yet. Run ./pair.sh in the FunOura folder first." >&2
    exit 1
fi
[ -f calibration.json ] || cp defaults/calibration.json calibration.json
[ -f taps.json ] || cp defaults/taps.json taps.json
../open_oura/target/release/oura --name "" --address "$address" \
    --key-file "$key" accel --seconds $((minutes * 60)) --jsonl \
    | python3 ringmouse.py --move --click --speed 800 --deadzone 8 "$@"
