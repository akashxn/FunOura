#!/usr/bin/env python3
"""FunOura's server: the one program that talks to the ring. It takes readings, works out the Health
metrics and runs ScreenOura (Hands Free, Remote and calibration in the app), for FunOura and for the
web dashboard at http://127.0.0.1:8787.

FunOura starts it for you. To run it on its own, from a terminal allowed to use Bluetooth:

    ./dashboard.sh        # serves http://127.0.0.1:8787 and opens it

The ring comes from ../ring.json, which ../pair.sh writes: {"address": ..., "key_file": ...}.

Only one thing talks to the ring at a time: a reading, or ScreenOura.
Set OURA_BIN to a stand-in program to try the dashboard without the ring, and
SCREENOURA_DRY_RUN=1 to run ScreenOura without moving or clicking the real cursor (or playing sounds).
"""

import json
import math
import os
import queue
import re
import signal
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import health

HERE = Path(__file__).resolve().parent
PLAY = HERE.parent
OURA = os.environ.get("OURA_BIN", str(PLAY / "open_oura/target/release/oura"))
RING_CONFIG = PLAY / "ring.json"  # written by pair.sh: which ring, and its auth key
DB = HERE / "oura.db"
RINGMOUSE = PLAY / "ringmouse"
PORT = int(os.environ.get("PORT", "8787"))
DRY_RUN = os.environ.get("SCREENOURA_DRY_RUN") == "1"
HOST_APP = os.environ.get("OURA_PLAY_HOST_APP", "your terminal app")  # the app macOS credits with Bluetooth and the cursor
SETTINGS = HERE / "screenoura.json"  # ScreenOura's tuning from the dashboard (run.sh keeps its own flags)
DEFAULTS = {"speed": 800.0, "deadzone": 8.0, "scroll_px": 400.0, "flip_scroll": False, "steer_on_start": True}
LIMITS = {"speed": (300.0, 2000.0), "deadzone": (3.0, 15.0), "scroll_px": (100.0, 1500.0)}

REMOTE_PROFILES = ("slides", "media")  # ringmouse.py --remote
HR_SECONDS = 30
SCREEN_SECONDS = 1800  # the ring's stream is time-boxed; ScreenOura reconnects when it ends
HISTORY_HOURS = 12
SPO2 = (-13.4, -5.1, 105.2)  # Ring 4 "SpO2 simple" quadratic, see open_oura/docs/spo2-calibration.md

UNREACHABLE = ("Couldn't reach the ring. Make sure it's close by and charged; putting it on "
               "the charger for a moment wakes it up.")
TURNED = ("The ring reads your hand upside down compared with its setup, so it has probably turned around "
          "on your finger. Turn it back the way it sat then, or redo the setup with Calibrate in FunOura.")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def paired_ring():
    """(address, key file) of the paired ring, or None before pair.sh has run."""
    try:
        cfg = json.loads(RING_CONFIG.read_text())
        key = Path(cfg["key_file"])
        return cfg["address"], key if key.is_absolute() else PLAY / key
    except (OSError, ValueError, KeyError, TypeError):
        return None


NOT_PAIRED = "No ring is paired yet. In Terminal, run ./pair.sh in the FunOura folder, then try again."


def ring_cmd(*args):
    # Always pinned to the paired ring's address: never "whichever Oura ring is nearby".
    address, key = paired_ring()
    return [OURA, "--name", "", "--address", address, "--key-file", str(key), "--db", str(DB), *args]


def clean(line):
    return ANSI.sub("", line).split("\r")[-1].strip()


def unreachable(text):
    return "no matching Oura ring" in text or "connecting to ring" in text


def load_settings():
    try:
        saved = json.loads(SETTINGS.read_text())
    except (OSError, json.JSONDecodeError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


# --- live updates ----------------------------------------------------------------

class Hub:
    """Fans server-sent events out to every open dashboard tab."""

    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def send(self, kind, **data):
        msg = json.dumps({"type": kind, **data})
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


hub = Hub()


# --- what the ring logged --------------------------------------------------------

def history():
    """Today's heart rate and skin temperature as the ring logged them, from the local database."""
    empty = {"heart": [], "temperature": [], "oxygen": [], "synced_at": None}
    if not DB.exists():
        return empty
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        ref_ts, synced = db.execute("select max(ring_timestamp), max(captured_unix) from events").fetchone()
        if ref_ts is None:
            return empty
        boot = db.execute("select max(ring_timestamp) from events where name='ring_start'").fetchone()[0] or 0
        since = ref_ts - HISTORY_HOURS * 36000  # ring time is in tenths of a second

        def rows(name):
            return db.execute(
                "select ring_timestamp, decoded_json from events where name=? and ring_timestamp>=? "
                "and ring_timestamp>=? and decoded_json is not null order by ring_timestamp",
                (name, max(since, boot), boot)).fetchall()

        def when(ts):
            return round(synced - (ref_ts - ts) / 10)

        heart = []
        for ts, js in rows("green_ibi_quality_event"):
            bpm = [b for b in json.loads(js).get("hr_bpm", []) if 35 <= b <= 200]
            if bpm:
                heart.append([when(ts), round(statistics.median(bpm))])
        temperature = []
        for ts, js in rows("temp_event"):
            temps = json.loads(js).get("temps_c", [])[:2]  # the two probes against the skin
            if temps:
                temperature.append([when(ts), round(sum(temps) / len(temps), 2)])
        oxygen = []
        for ts, js in rows("spo2_r_pi_event"):
            for r in json.loads(js).get("r", []):
                a, b, c = SPO2
                oxygen.append([when(ts), min(max(a * r * r + b * r + c, 85.0), 100.0)])
        return {"heart": heart, "temperature": temperature, "oxygen": oxygen, "synced_at": synced,
                "health": health_summary()}
    finally:
        db.close()


def health_summary():
    """FunOura's Health tab metrics; a problem here must never stop a sync from reporting."""
    try:
        return health.compute(DB)
    except Exception as e:
        print(f"health metrics failed: {e!r}", flush=True)
        return {}


def rmssd(ibis):
    """Beat-to-beat variability (RMSSD, ms), skipping implausible and jumpy beats."""
    ok = [i for i in ibis if 300 <= i <= 2000]
    diffs = [b - a for a, b in zip(ok, ok[1:]) if abs(b - a) <= 0.2 * a]
    if len(diffs) < 5:
        return None
    return round(math.sqrt(sum(d * d for d in diffs) / len(diffs)))


# --- the ring --------------------------------------------------------------------

class Ring:
    """Runs one ring operation at a time and remembers the latest readings."""

    def __init__(self):
        self.lock = threading.Lock()
        self.busy = None  # "heart" | "temperature" | "oxygen" | "battery" | "screenoura"
        self.readings = {}
        self.proc = None  # the running measurement command
        self.screen = None  # ScreenOura's processes and state
        self.activity = deque(maxlen=8)
        self.settings = load_settings()

    def state(self):
        screen = self.screen or {}
        return {"busy": self.busy, "readings": self.readings, "settings": self.settings,
                "screenoura": {"running": self.busy == "screenoura",
                               "connected": screen.get("connected", False),
                               "steering": screen.get("steering", False),
                               "stopping": screen.get("stopping", False),
                               "turned": screen.get("turned", False),
                               "full": screen.get("full"), "range": screen.get("range"),
                               "activity": list(self.activity), "calib": screen.get("calib"),
                               "remote": screen.get("remote") if self.busy == "screenoura" else None,
                               "calibrated": (RINGMOUSE / "calibration.json").exists()}}

    def _claim(self, what):
        with self.lock:
            if self.busy:
                return f"The ring is busy with {self.busy}. Try again when it's done."
            paired = paired_ring()
            if not paired:
                return NOT_PAIRED
            # a ring mouse started from Terminal (ringmouse/run.sh) holds the ring through the same key file
            others = subprocess.run(["pgrep", "-f", str(paired[1])], capture_output=True, text=True).stdout.split()
            if others:
                return ("Another program is using the ring, probably ScreenOura in a terminal. "
                        "Stop it there first.")
            self.busy = what
        hub.send("busy", what=what)
        return None

    def _release(self):
        self.busy = None
        self.proc = None
        hub.send("busy", what=None)

    def _run(self, *args):
        """Run a ring command, returning (ok, output)."""
        self.proc = subprocess.Popen(ring_cmd(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True)
        out = self.proc.communicate()[0]
        return self.proc.returncode == 0, out

    def _record(self, name, **reading):
        reading["at"] = time.time()
        self.readings[name] = reading
        hub.send("reading", name=name, **reading)

    def measure(self, what):
        problem = self._claim(what)
        if problem:
            return problem
        target = {"heart": self._heart, "temperature": self._temperature,
                  "oxygen": self._oxygen, "battery": self._battery}[what]
        threading.Thread(target=self._guarded, args=(what, target), daemon=True).start()
        return None

    def _guarded(self, what, target):
        try:
            target()
        except Exception as e:  # keep the dashboard alive and say what happened
            hub.send("error", name=what, message=f"Something went wrong: {e}")
        finally:
            self._release()

    def _heart(self):
        self.proc = subprocess.Popen(ring_cmd("live-hr", "--seconds", str(HR_SECONDS)),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        beats, output = [], []
        for line in self.proc.stdout:
            output.append(line)
            if line.startswith("Streaming live heart rate"):  # connected: the 30 s start now
                hub.send("heart_start", seconds=HR_SECONDS)
            m = re.search(r"(\d+) bpm \(IBI (\d+) ms\)", line)
            if m:
                bpm, ibi = int(m.group(1)), int(m.group(2))
                beats.append((bpm, ibi))
                hub.send("beat", bpm=bpm, ibi=ibi)
        self.proc.wait()
        if unreachable("".join(output)):
            hub.send("error", name="heart", message=UNREACHABLE)
        elif not beats:
            hub.send("error", name="heart",
                     message="No heartbeats came through. Is the ring snug on your finger? Keep your hand still.")
        else:
            self._record("heart", value=round(statistics.median(b for b, _ in beats)), unit="bpm",
                         hrv=rmssd([i for _, i in beats]), beats=len(beats))

    def _sync(self, name):
        ok, out = self._run("sync")
        if not ok:
            hub.send("error", name=name, message=UNREACHABLE if unreachable(out) else
                     "Syncing the ring's log didn't finish. Try again.")
        return ok

    def _temperature(self):
        if not self._sync("temperature"):
            return
        h = history()
        hub.send("history", **h)
        if not h["temperature"]:
            hub.send("error", name="temperature", message="The ring hasn't logged a temperature yet. "
                     "Wear it for a few minutes and check again.")
            return
        at, value = h["temperature"][-1]
        self._record("temperature", value=round(value, 1), unit="°C", logged=at, worn=value >= 30)

    def _oxygen(self):
        if not self._sync("oxygen"):
            return
        h = history()
        hub.send("history", **h)
        if not h["oxygen"]:
            self._record("oxygen", value=None, unit="%",
                         note="The ring measures blood oxygen while you sleep. Nothing logged yet: "
                              "wear it tonight and check in the morning.")
            return
        latest_night = [v for t, v in h["oxygen"] if t >= h["oxygen"][-1][0] - 10 * 3600]
        self._record("oxygen", value=round(statistics.median(latest_night)), unit="%",
                     logged=h["oxygen"][-1][0])

    def _battery(self):
        ok, out = self._run("info")
        m = re.search(r"Battery\s*:\s*(\d+)%", out)
        if m:
            self._record("battery", value=int(m.group(1)), unit="%")
        else:
            hub.send("error", name="battery", message=UNREACHABLE if unreachable(out) or not ok else
                     "The ring didn't report its battery. Try again.")

    # --- ScreenOura: the ring as a mouse ---

    def start_screen(self, calibrate=False, remote=None):
        """Start the ring mouse; with calibrate, it walks through the five setup poses first. With
        remote ("slides" or "media"), gestures press keys instead of moving the cursor."""
        if remote not in (None, *REMOTE_PROFILES):
            return "That isn't a remote profile."
        if not calibrate and not (RINGMOUSE / "calibration.json").exists():
            return ("ScreenOura needs its one-time setup first: use Calibrate in FunOura, "
                    "or run ringmouse/run.sh --recalibrate in Terminal and follow the five poses.")
        problem = self._claim("screenoura")
        if problem:
            return problem
        self.screen = {"stopping": False, "connected": False, "steering": False, "remote": remote}
        self._note("Starting setup" if calibrate else "Starting remote" if remote else "Starting")
        self._launch(steer=True if remote else self.settings["steer_on_start"], calibrate=calibrate, remote=remote)
        return None

    def remote_profile(self, profile):
        """Switch the running remote between slides and media."""
        s = self.screen
        if profile not in REMOTE_PROFILES:
            return "That isn't a remote profile."
        if self.busy != "screenoura" or not s or not s.get("remote"):
            return "The remote isn't running."
        if not self._send(remote=profile):
            return "The remote didn't answer. Stop it and start it again."
        s["remote"] = profile
        self._note(f"Remote: {profile}")
        return None

    def next_step(self):
        """Setup is waiting for the hand to be in position: go on (Enter, in the terminal version)."""
        s = self.screen
        if self.busy != "screenoura" or not s or not s.get("calib"):
            return "Setup isn't running."
        if not self._send(next=True):
            return "The ring mouse didn't answer. Stop it and start again."
        return None

    def _launch(self, steer, calibrate=False, remote=None):
        accel = subprocess.Popen(ring_cmd("accel", "--seconds", str(SCREEN_SECONDS), "--jsonl"),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        s = self.settings
        control, control_w = os.pipe()  # dashboard -> ring mouse: steering and settings, live
        cmd = [sys.executable, "-u", "ringmouse.py", "--recalibrate" if calibrate else "--require-saved",
               "--status", "--control-fd", str(control),
               "--speed", str(s["speed"]), "--deadzone", str(s["deadzone"]), "--scroll-px", str(s["scroll_px"])]
        if remote:  # keys instead of the cursor
            cmd += ["--remote", remote] + (["--quiet", "--no-press"] if DRY_RUN else [])
        else:
            cmd += ["--quiet"] if DRY_RUN else ["--move", "--click"]  # a dry run leaves the Mac alone: no cursor, no sounds
        if s["flip_scroll"]:
            cmd.append("--flip-scroll")
        if steer:
            cmd.append("--steer")
        mouse = subprocess.Popen(cmd, cwd=RINGMOUSE, stdin=accel.stdout, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, pass_fds=(control,))
        os.close(control)
        accel.stdout.close()  # the mouse owns the pipe; if it exits, the ring program notices
        self.screen.update(accel=accel, mouse=mouse, control=os.fdopen(control_w, "w"), started=time.time())
        threading.Thread(target=self._watch_accel, args=(accel,), daemon=True).start()
        threading.Thread(target=self._watch_mouse, args=(accel, mouse), daemon=True).start()

    def _send(self, **cmd):
        """Tell the running ring mouse something; False if it has already gone."""
        s = self.screen
        try:
            with self.lock:
                s["control"].write(json.dumps(cmd) + "\n")
                s["control"].flush()
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _note(self, text):
        self.activity.appendleft({"at": time.time(), "text": text})
        s = self.screen or {}
        hub.send("screen", connected=s.get("connected", False), steering=s.get("steering", False),
                 stopping=s.get("stopping", False), turned=s.get("turned", False), full=s.get("full"),
                 remote=s.get("remote") if self.busy == "screenoura" else None,
                 range=s.get("range"), activity=list(self.activity))

    def _status(self, m):
        """A '@{json}' line from the ring mouse."""
        s, kind = self.screen, m.pop("t", None)
        if kind == "aim":
            hub.send("aim", **m)
        elif kind == "ready":  # samples are actually flowing
            s.update(connected=True, full=m.get("full"), range=m.get("range"))
            self._note("Connected to the ring")
        elif kind == "steer":
            s["steering"] = m["on"]
            self._note("Steering on" if m["on"] else "Steering off")
        elif kind == "turned":  # the cursor can't start: the ring sits differently from its setup
            s["turned"] = True
            hub.send("error", name="screenoura", message=TURNED)
            self._note("The ring seems turned around")
        elif kind == "calib":  # a setup step: show it, keeping a failed try's reason until a pose lands
            if m.get("stage") in ("prompt", "countdown", "measure") and (s.get("calib") or {}).get("problem"):
                m.setdefault("problem", s["calib"]["problem"])
            s["calib"] = None if m.get("stage") == "done" else m
            hub.send("calib", **m)
            if m.get("stage") == "done":
                self._note("Setup saved")
        elif kind == "remote":
            self._note(m.get("action", "Pressed"))
        elif kind == "click":
            self._note({1: "Clicked", 2: "Double-clicked"}.get(m.get("count"), "Triple-clicked"))
        elif kind == "scroll":
            self._note(f"Scrolled {m.get('dir')}" + (" further" if m.get("boost", 1) > 1 else ""))

    def _watch_accel(self, accel):
        for line in accel.stderr:
            line = clean(line)
            if unreachable(line):
                hub.send("error", name="screenoura", message=UNREACHABLE)

    def _watch_mouse(self, accel, mouse):
        for line in mouse.stdout:
            if line.startswith("@"):
                try:
                    self._status(json.loads(line[1:]))
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
                continue
            line = clean(line)
            if line.startswith("NEEDS_CALIBRATION"):
                hub.send("error", name="screenoura", message="ScreenOura needs its one-time setup first. "
                         "Use Calibrate in FunOura.")
            elif line.startswith("macOS hasn't allowed"):
                hub.send("error", name="screenoura", message=f"ScreenOura can't move the cursor: turn on {HOST_APP} in "
                         "System Settings > Privacy & Security > Accessibility, then start it again.")
        mouse.wait()
        try:
            accel.wait(timeout=8)
        except subprocess.TimeoutExpired:
            accel.terminate()  # never kill it: it has to switch the ring's stream off on the way out
            accel.wait()
        s = self.screen
        try:
            s["control"].close()
        except OSError:
            pass
        ran = time.time() - s["started"]
        if not s["stopping"] and s.get("connected") and ran > 60:
            steer = s["steering"]  # carry steering on/off over to the new connection
            s.update(connected=False, steering=False)
            self._note("Reconnecting (the ring's stream timed out)")
            self._launch(steer=steer)
            return
        s.update(connected=False, steering=False, stopping=False)
        self._note("Stopped")
        self.screen = None
        self._release()

    def stop_screen(self):
        s = self.screen
        if self.busy != "screenoura" or not s:
            return "ScreenOura isn't running."
        if s["stopping"]:
            return None
        s["stopping"] = True
        self._note("Stopping")
        # The cursor stops right away; the ring program then switches the stream off and exits.
        self._send(quit=True)
        s["accel"].send_signal(signal.SIGTERM)
        threading.Thread(target=self._reap, args=(s["mouse"],), daemon=True).start()
        return None

    def _reap(self, mouse):
        try:
            mouse.wait(timeout=6)
        except subprocess.TimeoutExpired:
            mouse.kill()

    def steer_screen(self, on):
        s = self.screen
        if not isinstance(on, bool):
            return "Say whether steering should be on or off."
        if self.busy != "screenoura" or not s or not s.get("connected") or s["stopping"]:
            return "ScreenOura isn't connected to the ring right now."
        if not self._send(steer=on):
            return "ScreenOura didn't answer. Stop it and start it again."
        return None

    def update_settings(self, changes):
        new = {}
        for key, (lo, hi) in LIMITS.items():
            if key in changes:
                try:
                    value = float(changes[key])
                except (TypeError, ValueError):
                    value = math.nan
                if not math.isfinite(value):
                    return f"{key} needs a number."
                new[key] = min(max(value, lo), hi)
        for key in ("flip_scroll", "steer_on_start"):
            if key in changes:
                new[key] = bool(changes[key])
        if not new:
            return "Nothing to change."
        self.settings.update(new)
        SETTINGS.write_text(json.dumps(self.settings, indent=2) + "\n")
        live = {k: v for k, v in new.items() if k != "steer_on_start"}
        if live and self.busy == "screenoura" and self.screen:
            self._send(**live)  # applies at once; a new connection starts with the saved values
        hub.send("settings", **self.settings)
        return None

    def shutdown(self):
        """Leave the ring tidy when the dashboard quits."""
        if self.busy == "screenoura" and self.screen:
            self.stop_screen()
            deadline = time.time() + 10
            while self.busy and time.time() < deadline:
                time.sleep(0.1)
        elif self.busy:
            was = self.busy
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                self.proc.send_signal(signal.SIGCONT)  # in case Ctrl-Z paused it
                self.proc.wait()
            if was == "heart":  # cut short, live heart rate would stay on; put it back to automatic
                subprocess.run(ring_cmd("features", "--enable-hr"), capture_output=True, timeout=60)


ring = Ring()


# --- web server ------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _page(self, name):
        data = (HERE / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._page("index.html")
        elif path in ("/game", "/game.html"):  # Quick Draw, a target range to play with ScreenOura
            self._page("game.html")
        elif path in ("/pong", "/pong.html"):  # Ring Pong, against the computer with ScreenOura
            self._page("pong.html")
        elif path in ("/invaders", "/invaders.html"):  # Ring Invaders: tilt to move, tap to fire
            self._page("invaders.html")
        elif path in ("/flappy", "/flappy.html"):  # Flappy Ring: tap to flap
            self._page("flappy.html")
        elif path == "/api/state":
            self._json(200, ring.state())
        elif path == "/api/history":
            self._json(200, history())
        elif path == "/api/stream":
            self._stream()
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        # Only this page may drive the ring: browsers can't add this header cross-site
        # without a preflight we never answer, so other websites can't start the mouse.
        if self.headers.get("X-Oura-Play") != "1":
            self._json(403, {"error": "forbidden"})
            return
        path = self.path.split("?")[0]
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "The request wasn't valid JSON."})
            return
        if not isinstance(body, dict):
            body = {}
        m = re.fullmatch(r"/api/measure/(heart|temperature|oxygen|battery)", path)
        if m:
            problem = ring.measure(m.group(1))
        elif path == "/api/screenoura/start":
            problem = ring.start_screen()
        elif path == "/api/screenoura/calibrate":
            problem = ring.start_screen(calibrate=True)
        elif path == "/api/screenoura/next":
            problem = ring.next_step()
        elif path == "/api/remote/start":
            problem = ring.start_screen(remote=body.get("profile"))
        elif path == "/api/remote/profile":
            problem = ring.remote_profile(body.get("profile"))
        elif path == "/api/screenoura/stop":
            problem = ring.stop_screen()
        elif path == "/api/screenoura/steer":
            problem = ring.steer_screen(body.get("on"))
        elif path == "/api/screenoura/settings":
            problem = ring.update_settings(body)
        else:
            self._json(404, {"error": "not found"})
            return
        self._json(409 if problem else 202, {"error": problem} if problem else {"ok": True})

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = hub.subscribe()
        try:
            self.wfile.write(f"data: {json.dumps({'type': 'state', **ring.state()})}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f"data: {msg}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": still here\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            hub.unsubscribe(q)


def main():
    url = f"http://127.0.0.1:{PORT}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"The dashboard is already running at {url}. Opening it.")
        subprocess.run(["open", url])
        return
    server.daemon_threads = True

    def quit_cleanly(*_):
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGTSTP):
        signal.signal(sig, quit_cleanly)  # Ctrl-Z too: a paused dashboard would strand the ring

    print(f"Oura Play dashboard at {url}  (Ctrl-C to quit)")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if os.environ.get("OURA_PLAY_NO_BROWSER") != "1":
        subprocess.run(["open", url])
    ring.measure("battery")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping: leaving the ring tidy...")
        ring.shutdown()
        server.shutdown()
        print("Done.")


if __name__ == "__main__":
    main()
