# Patches to open_oura

FunOura uses [open_oura](https://github.com/Th0rgal/open_oura) by Thomas Marchand (MIT License) to talk to the ring. `setup.sh` clones it into `open_oura/`, checks out commit `c36cd720512a9472f6caf75701052ea8461e8a1e`, and applies `open_oura-funoura.patch`, which:

- adds `oura accel --jsonl`, printing each accelerometer sample as a JSON line so the ring mouse can read the stream from a pipe
- ends the accelerometer stream cleanly when the reader goes away, on SIGTERM, SIGHUP or Ctrl-Z, and after 5 seconds of silence, so the ring never keeps streaming unattended

open_oura's copyright and license notice stay with its code in `open_oura/LICENSE`.
