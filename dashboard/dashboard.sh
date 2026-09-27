#!/bin/bash
# Start FunOura's server on its own and open the web dashboard in the browser.
# You don't need this when FunOura is running: the app starts the server itself.
# Run it from a terminal app that's allowed Bluetooth and Accessibility. Quit with Ctrl-C.
cd "$(dirname "$0")" || exit 1
exec python3 -u server.py
