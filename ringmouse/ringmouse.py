"""Ring mouse: steer the macOS cursor with the old Oura ring, tap to click.

Hold the ring hand like a finger gun (index finger pointing, thumb up). Aim
up/down to move the cursor up/down, tip the gun left/right like a plane banking
to move it left/right, and aim at the center to stop. Drop the thumb onto the
side of the index finger ("fire") to click. Palm up to the ceiling for a second
switches it on/off. Every setup step waits for Enter, pressed with the free hand.

    oura ... accel --seconds 900 --jsonl | python3 ringmouse.py --test           # dry run + guided check
    oura ... accel --seconds 900 --jsonl | python3 ringmouse.py                  # dry run: sounds only
    oura ... accel --seconds 900 --jsonl | python3 ringmouse.py --move           # cursor moves
    oura ... accel --seconds 900 --jsonl | python3 ringmouse.py --move --click   # taps click too

With --mode point the cursor goes where you point instead: boxes appear on the
MacBook screen, you point at each one to calibrate, and each hand pose then maps
straight to a spot on the screen.
"""

import argparse
import ctypes
import json
import math
import queue
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

RATE = 50  # ring accelerometer samples per second
SOUNDS = Path("/System/Library/Sounds")

POSES = [
    ("center", "AIM AT THE CENTER",
     "Make a finger gun with the ring hand (index finger pointing, thumb up)\n"
     "and aim it at the middle of the screen. Keep your arm relaxed."),
    ("up", "AIM AT THE TOP",
     "Keep the finger gun and bend at the wrist so it aims at the top edge of the screen."),
    ("down", "AIM AT THE BOTTOM",
     "Keep the finger gun and bend at the wrist so it aims at the bottom edge of the screen."),
    ("right", "TIP TO THE RIGHT",
     "Aim at the center again, then tip the gun to the right like a plane banking right:\n"
     "your thumb leans over toward the right side of the screen."),
    ("flip", "PALM UP (this is the on/off switch)",
     "Turn your hand so your palm faces the ceiling."),
]

TESTS = [
    ("taps", "TAP TEST",
     "Aim the finger gun at the screen. Tap 10 times: drop your thumb onto the side of\n"
     "your index finger and lift it again, like firing. One quick, firm tap every second or two.\n"
     "Each tap that registers pops and shows up here."),
    ("typing", "TYPING TEST",
     "Type a sentence or two normally with both hands (it isn't saved), then press Enter."),
    ("toggle", "ON/OFF TEST",
     "Turn your palm up to the ceiling and hold it until you hear a chime (about 1 second),\n"
     "then aim back at the screen. Do that twice: once for on, once for off."),
]

SWEEPS = [
    ("sweep_up", "SWEEP UP",
     "Hold the finger gun aimed at the screen. Move your whole hand UP quickly, about a\n"
     "hand's length, like flicking a page up. Keep the wrist straight: move the hand, don't\n"
     "tilt it. Then bring it back down slowly. Do this 8 times, a couple of seconds apart."),
    ("sweep_down", "SWEEP DOWN",
     "Same thing downward: move your whole hand DOWN quickly, about a hand's length,\n"
     "then bring it back up slowly. 8 times, a couple of seconds apart."),
    ("steer", "NORMAL STEERING",
     "Aim around the screen like you're moving the cursor (up, down, left, right)\n"
     "for about 15 seconds. No sweeps."),
    ("taps", "TAPS", "Aim at the screen and tap 5 times, like clicking."),
    ("keyboard", "TO THE KEYBOARD AND BACK",
     "Drop your hand onto the keyboard, then raise it back to aiming at the screen.\n"
     "Do that 5 times at your normal pace."),
]

POINT_STEPS = [
    ("center", "Point at the MIDDLE box and hold still."),
    ("tl", "Point at the TOP-LEFT box (aim up, tip the gun left) and hold still."),
    ("tr", "Point at the TOP-RIGHT box (aim up, tip the gun right) and hold still."),
    ("br", "Point at the BOTTOM-RIGHT box (aim down, tip right) and hold still."),
    ("bl", "Point at the BOTTOM-LEFT box (aim down, tip left) and hold still."),
    ("flip", "Turn your palm up to the ceiling and hold (the on/off switch)."),
]
POINT_GRACE = RATE  # a box must be lit 1 s before it can capture
POINT_HOLD = 60  # hold still 1.2 s to capture
POINT_STILL = 1.5  # degrees of movement per 200 ms that still counts as holding still

TAP_CAL = [
    ("taps", "TAP 8 TIMES",
     "Aim the finger gun at the screen like you're about to click. Tap 8 times: drop your\n"
     "thumb onto the side of your index finger and lift it. One tap every second or two.\n"
     "Press Enter when you've done 8."),
    ("moves", "MOVE WITHOUT TAPPING",
     "For about 15 seconds, steer the way you normally would: aim around, tip left and\n"
     "right, stop and start, fast and slow. Don't tap. Then press Enter."),
]

MEASURE_SAMPLES = 75  # 1.5 s held per pose
EMA_ALPHA = 0.2  # orientation smoothing (~100 ms)
STEER_ALPHA = 0.4  # lighter smoothing (~30 ms) for the cursor only; taps and gestures keep the steadier one
VEL_TAU = 0.03  # seconds to ease the cursor speed between samples, which arrive in pairs every 40 ms
TAP_THRESHOLD = 1000.0  # spike size (raw counts) that can be a tap, when there's no taps.json
TAP_PRE = 10  # compare orientation 200 ms before the spike...
TAP_POST = 8  # ...with 160 ms after it
TAP_GAP = 15  # 300 ms between taps: lifting the thumb can jolt the ring again up to ~240 ms later
TAP_MAX_TURN = 20.0  # degrees; a larger orientation change is a hand movement, not a tap
TAP_CONTEXT = 25  # the 500 ms before a spike, for the next two rules:
TAP_STEADY = 25.0  # the hand turned less than this (degrees) just before it; people aim, then tap
TAP_SHARP = 6.0  # the spike is this many times the background shake; a jolt from moving isn't
FLIP_NEAR = 30.0  # degrees from the palm-up pose that count as palm up
FLIP_REARM = 50.0  # leave the palm-up pose by this much before it can toggle again
FLIP_SAMPLES = 50  # hold palm up this long (1 s) to toggle
SWEEP_PUSH = 600.0  # vertical acceleration (raw counts) of the push that starts a sweep...
SWEEP_BRAKE = 1200.0  # ...and of the opposite brake that ends it
SWEEP_LINK = 10  # at most 200 ms between the push and the brake
SWEEP_GAP = 30  # 600 ms before another sweep, so the settle-back isn't a sweep the other way
LOBE_MIN = 250.0  # vertical acceleration below this is noise
OUT_OF_RANGE = 60.0  # degrees from center where the cursor stops (typing, hand resting)
TURNED_OFF = 145.0  # degrees from center: the finger gun reading upside down...
TURNED_SAMPLES = 75  # ...for this long (net 1.5 s) before the cursor ever starts: the ring has turned on the finger
DOUBLE_CLICK_PX = 8  # a quick second tap this close to the first lands on it as a double-click
SCROLL_STREAK_S = 2.0  # sweeping the same way again within this long scrolls further each time...
SCROLL_STREAK_MAX = 3.0  # ...up to this many times the normal distance
STATUS_EVERY_S = 0.08  # live aim updates for the dashboard (--status), ~12 a second


# --- vectors -----------------------------------------------------------------

def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def unit(a):
    n = math.sqrt(dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def minus(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def times(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def angle(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, dot(unit(a), unit(b))))))


def mean(vs):
    return unit((sum(v[0] for v in vs), sum(v[1] for v in vs), sum(v[2] for v in vs)))


# --- macOS cursor ------------------------------------------------------------

class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


class Mouse:
    MOVED, DOWN, UP = 5, 1, 2  # kCGEventMouseMoved, kCGEventLeftMouseDown/Up

    def __init__(self):
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg.CGEventCreate.restype = ctypes.c_void_p
        cg.CGEventCreate.argtypes = [ctypes.c_void_p]
        cg.CGEventGetLocation.restype = CGPoint
        cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
        cg.CGGetActiveDisplayList.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        cg.CGDisplayBounds.restype = CGRect
        cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
        cg.CGEventCreateScrollWheelEvent2.restype = ctypes.c_void_p
        cg.CGEventCreateScrollWheelEvent2.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
        cg.CGPreflightPostEventAccess.restype = ctypes.c_bool
        cg.CGRequestPostEventAccess.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        self.cg, self.cf = cg, cf

        ids = (ctypes.c_uint32 * 16)()
        count = ctypes.c_uint32()
        cg.CGGetActiveDisplayList(16, ids, ctypes.byref(count))
        self.displays = []
        for i in range(count.value):
            r = cg.CGDisplayBounds(ids[i])
            self.displays.append((r.origin.x, r.origin.y, r.size.width, r.size.height))

    def main_display(self):
        """(x, y, width, height) of the built-in/main display."""
        self.cg.CGMainDisplayID.restype = ctypes.c_uint32
        r = self.cg.CGDisplayBounds(self.cg.CGMainDisplayID())
        return r.origin.x, r.origin.y, r.size.width, r.size.height

    def has_access(self):
        return self.cg.CGPreflightPostEventAccess()

    def request_access(self):
        return self.cg.CGRequestPostEventAccess()

    def location(self):
        e = self.cg.CGEventCreate(None)
        p = self.cg.CGEventGetLocation(e)
        self.cf.CFRelease(e)
        return p.x, p.y

    def on_screen(self, x, y):
        return any(dx <= x < dx + w and dy <= y < dy + h for dx, dy, w, h in self.displays)

    def clamp(self, cur, new):
        """Keep the cursor on a display; slide along edges and skip gaps between screens."""
        for cand in (new, (new[0], cur[1]), (cur[0], new[1])):
            if self.on_screen(*cand):
                return cand
        return cur

    def _post(self, kind, x, y, count=1):
        e = self.cg.CGEventCreateMouseEvent(None, kind, CGPoint(x, y), 0)
        if count > 1:
            self.cg.CGEventSetIntegerValueField(e, 1, count)  # kCGMouseEventClickState: apps see a double-click
        self.cg.CGEventPost(0, e)  # kCGHIDEventTap
        self.cf.CFRelease(e)

    def move(self, x, y):
        self._post(self.MOVED, x, y)

    def scroll(self, dy):
        """Scroll by dy pixels; positive moves toward the top of the page."""
        e = self.cg.CGEventCreateScrollWheelEvent2(None, 0, 1, int(dy), 0, 0)  # kCGScrollEventUnitPixel
        self.cg.CGEventPost(0, e)
        self.cf.CFRelease(e)

    def click(self, x, y, count=1):
        """Left click; count 2 (or 3) makes it the second click of a double-click (or triple)."""
        self._post(self.DOWN, x, y, count)
        try:
            time.sleep(0.02)
        finally:
            self._post(self.UP, x, y, count)


class Keys:
    """Key presses for remote mode: arrow keys through CoreGraphics, and media keys (play/pause, next
    track, volume) as the system-defined events the keyboard's media row sends, built with AppKit."""
    MEDIA = {"play": 16, "next": 17, "previous": 18, "volume_up": 0, "volume_down": 1}  # NX_KEYTYPE_*
    ARROWS = {"right": 124, "left": 123}

    def __init__(self):
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = objc.sel_registerName.argtypes = [ctypes.c_char_p]
        # +[NSEvent otherEventWithType:location:modifierFlags:timestamp:windowNumber:context:subtype:data1:data2:]
        self._event = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64, CGPoint,
                                       ctypes.c_uint64, ctypes.c_double, ctypes.c_int64, ctypes.c_void_p,
                                       ctypes.c_int16, ctypes.c_int64, ctypes.c_int64)(("objc_msgSend", objc))
        self._cgevent = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        self.ns_event = objc.objc_getClass(b"NSEvent")
        self.sel_make = objc.sel_registerName(
            b"otherEventWithType:location:modifierFlags:timestamp:windowNumber:context:subtype:data1:data2:")
        self.sel_cg = objc.sel_registerName(b"CGEvent")
        self.cg, self.cf = cg, cf

    def press(self, name):
        if name in self.ARROWS:
            for down in (True, False):
                e = self.cg.CGEventCreateKeyboardEvent(None, self.ARROWS[name], down)
                self.cg.CGEventPost(0, e)  # kCGHIDEventTap
                self.cf.CFRelease(e)
        else:
            key = self.MEDIA[name]
            for state in (0xa, 0xb):  # key down, key up
                e = self._event(self.ns_event, self.sel_make, 14, CGPoint(0, 0), state << 8, 0.0, 0, None,
                                8, (key << 16) | (state << 8), -1)  # NSEventTypeSystemDefined, media key subtype
                self.cg.CGEventPost(0, self._cgevent(e, self.sel_cg))


# What each gesture does in remote mode: (key, what to call it)
REMOTE = {
    "slides": {"tap": ("right", "Next slide"), "double": ("left", "Previous slide"),
               "up": ("volume_up", "Volume up"), "down": ("volume_down", "Volume down")},
    "media": {"tap": ("play", "Play / pause"), "double": ("next", "Next track"),
              "up": ("volume_up", "Volume up"), "down": ("volume_down", "Volume down")},
}


def double_click_seconds():
    """The Mac's double-click speed (System Settings > Accessibility > Pointer Control)."""
    try:
        out = subprocess.run(["defaults", "read", "-g", "com.apple.mouse.doubleClickThreshold"],
                             capture_output=True, text=True, timeout=2).stdout
        return min(max(float(out), 0.2), 2.0)
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0.5


# --- point mode ----------------------------------------------------------------

def point_targets(display):
    """Screen positions of the calibration boxes: the middle and the four corners."""
    x0, y0, w, h = display
    left, right, top, bottom = x0 + 60, x0 + w - 60, y0 + 90, y0 + h - 60
    return {"center": (x0 + w / 2, y0 + h / 2), "tl": (left, top), "tr": (right, top),
            "br": (right, bottom), "bl": (left, bottom)}


class PointMap:
    """Pose angles -> screen position, exact at all five boxes.

    Each triangle between the middle box and two neighbouring corners gets its own
    affine map, so uneven reach up/down/left/right doesn't bend the rest.
    """
    CORNERS = ("tl", "tr", "br", "bl")

    def __init__(self, pts, screen):
        self.c, self.C = pts["center"], screen["center"]
        pairs = zip(self.CORNERS, self.CORNERS[1:] + self.CORNERS[:1])
        self.sectors = [(self._rel(pts[a]), self._rel(pts[b]), screen[a], screen[b]) for a, b in pairs]

    def _rel(self, p):
        return p[0] - self.c[0], p[1] - self.c[1]

    def __call__(self, a, b):
        p = self._rel((a, b))
        best = None
        for u, v, su, sv in self.sectors:
            det = u[0] * v[1] - u[1] * v[0]
            s = (p[0] * v[1] - p[1] * v[0]) / det
            t = (u[0] * p[1] - u[1] * p[0]) / det
            if best is None or min(s, t) > best[0]:
                best = (min(s, t), s, t, su, sv)
        _, s, t, su, sv = best
        cx, cy = self.C
        return cx + s * (su[0] - cx) + t * (sv[0] - cx), cy + s * (su[1] - cy) + t * (sv[1] - cy)


class OneEuro:
    """One Euro filter: smooths hard while the hand is still, barely lags when it moves."""

    def __init__(self, min_cutoff=0.8, beta=0.01, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = None
        self.dx = 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        return 1.0 / (1.0 + 1.0 / (2 * math.pi * cutoff * dt))

    def __call__(self, x, dt):
        if self.x is None:
            self.x = x
            return x
        self.dx += self._alpha(self.d_cutoff, dt) * ((x - self.x) / dt - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x += self._alpha(cutoff, dt) * (x - self.x)
        return self.x


class Overlay:
    """The calibration boxes: small always-on-top squares on the MacBook screen."""
    SIZE = 90
    COLORS = {"idle": "#555555", "wait": "#e53333", "hold": "#ff9900", "palm": "#3366ff"}

    def __init__(self, targets):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.boxes = {}
        s = self.SIZE
        for key, (x, y) in targets.items():
            w = tk.Toplevel(self.root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.geometry(f"{s}x{s}+{int(x - s / 2)}+{int(y - s / 2)}")
            c = tk.Canvas(w, width=s, height=s, bg=self.COLORS["idle"], highlightthickness=0)
            c.pack()
            label = c.create_text(s / 2, s / 2, text="", fill="white", justify="center",
                                  font=("Helvetica", 15, "bold"))
            self.boxes[key] = (c, label)
        self.pump()

    def show(self, key, state):
        """Light up one box ('wait' red, 'hold' orange); 'flip' shows PALM UP in the middle."""
        lit = "center" if key == "flip" else key
        for k, (c, label) in self.boxes.items():
            if k != lit:
                c.configure(bg=self.COLORS["idle"])
                c.itemconfigure(label, text="")
            elif key == "flip":
                c.configure(bg=self.COLORS["palm"] if state == "wait" else self.COLORS["hold"])
                c.itemconfigure(label, text="PALM\nUP")
            else:
                c.configure(bg=self.COLORS[state])
                c.itemconfigure(label, text="POINT\nHERE" if state == "wait" else "HOLD")
        self.pump()

    def pump(self):
        if self.root:
            self.root.update()

    def close(self):
        if self.root:
            self.root.destroy()
            self.root = None


# --- tap calibration -----------------------------------------------------------

def fit_taps(taps, moves):
    """Pick the click threshold from your taps and your ordinary movement.

    Only the spike size is fitted: 8 taps can't pin down more, and fitting the tightest
    rules that just cover them misses taps later (an earlier tap-direction rule dropped
    half of them). The shape rules stay fixed, and the size leans sensitive: the lowest
    threshold that lets the fewest movement jolts through.
    Returns (rules, report); rules is None when there weren't enough taps to go on.
    """
    if len(taps) < 5:
        return None, f"only {len(taps)} taps registered. Tap a bit firmer and run it again."

    def clicks(spikes, thr):
        return sum(s["d2"] > thr and s["turn"] < TAP_MAX_TURN and s["motion"] < TAP_STEADY
                   and s["sharp"] >= TAP_SHARP for s in spikes)

    options = [(thr, clicks(taps, thr), clicks(moves, thr)) for thr in (1000.0, 1200.0, 1500.0, 2000.0)]
    thr, caught, false = min(options, key=lambda o: (o[2], o[0]))
    rules = {"threshold": thr, "max_turn": TAP_MAX_TURN, "caught": caught, "taps": len(taps)}
    report = (f"caught {caught} of {len(taps)} taps, with {false} false clicks from your movement.\n"
              f"Rules: spike over {thr:.0f}, hand steady just before it, finger turns less than "
              f"{TAP_MAX_TURN:.0f}°.")
    if caught < 0.85 * len(taps):
        report += "\nSome taps were soft or came mid-movement: tap firmly while holding the hand still."
    return rules, report


# --- gesture tracking --------------------------------------------------------

class Ring:
    """Turns raw samples into orientation, taps, and the on/off toggle."""

    def __init__(self, tap_threshold, max_turn=TAP_MAX_TURN, steady=TAP_STEADY, sharp=TAP_SHARP):
        self.tap_threshold = tap_threshold
        self.max_turn = max_turn
        self.steady = steady
        self.sharp = sharp
        self.last_spike = None
        self.n = 0
        self.lp = None
        self.fast = None
        self.g = None
        self.hist = deque(maxlen=TAP_PRE + 1)
        self.context = deque(maxlen=TAP_CONTEXT + 1)  # (orientation, spike size) per sample
        self.raw = deque(maxlen=3)
        self.pending = None
        self.last_tap_n = -10**9
        self.basis = None
        self.flip = None
        self.full = {"up": 20.0, "down": 20.0, "right": 20.0}
        self.flip_run = 0
        self.flip_ready = False
        self.active = False
        self.outside_on = False  # steering was switched on for you (see set_active)
        self.upside = 0  # samples reading as the finger gun upside down while waiting to start
        self.turned = False
        self.aimless = False  # remote mode: the hand needn't aim at the screen, so no aim checks
        self.recentered = False
        self.up = self.right = self.off = 0.0
        self.grav = None
        self.vert = 0.0
        self.lobe = None
        self.prev_lobe = None
        self.last_sweep_n = -10**9
        self.ok_hist = deque(maxlen=15)
        self.sweep_ok = False
        self.sweep_start_n = 0
        self.tap_n = 0
        self.pointmap = None
        self.target = None
        self.range_limit = OUT_OF_RANGE

    def sweeping(self):
        """True while the hand is sweeping up/down (and briefly after)."""
        return abs(self.vert) > 800.0 or self.n - self.last_sweep_n < 15

    def calibrate(self, poses):
        """Build the steering frame from the recorded poses; return a problem or None."""
        g0, gu, gd, gr, gf = (poses[k] for k in ("center", "up", "down", "right", "flip"))
        for g, what, fix in ((gu, "aiming at the top", "Bend the wrist further up"),
                             (gd, "aiming at the bottom", "Bend the wrist further down"),
                             (gr, "tipping right", "Tip the gun further over")):
            if angle(g0, g) < 8:
                return f"{what} only moved the ring {angle(g0, g):.0f}°. {fix}."
        if angle(g0, gf) < 60:
            return (f"palm up was only {angle(g0, gf):.0f}° from center. "
                    "Turn the palm fully to the ceiling.")

        # 2D angles around the center pose; the vertical axis is the line through the
        # top and bottom poses, since real wrists don't bend straight or evenly both ways.
        e1 = unit(minus(gu, times(g0, dot(gu, g0))))
        self.basis = (g0, e1, cross(g0, e1))
        (ux, uy), (dx, dy), (rx, ry) = (self._coords(g) for g in (gu, gd, gr))
        n = math.hypot(ux - dx, uy - dy)
        if n < 8:
            self.basis = None
            return "aiming at the bottom looked like aiming at the top. Aim lower."
        # Horizontal runs through the tip-right pose, so tipping never leaks into vertical.
        vaxis = ((ux - dx) / n, (uy - dy) / n)
        full_right = math.hypot(rx, ry)
        raxis = (rx / full_right, ry / full_right)
        det = vaxis[0] * raxis[1] - vaxis[1] * raxis[0]
        if abs(det) < 0.5:
            self.basis = None
            return "tipping right looked like aiming up or down. Tip sideways, don't aim."
        self.axes = (vaxis, raxis, det)
        full_up = self._split(ux, uy)[0]
        full_down = -self._split(dx, dy)[0]
        if full_down < 6:
            self.basis = None
            return "aiming at the bottom didn't move the opposite way from the top. Aim lower."
        self.flip = gf
        self.full = {k: min(max(v, 12.0), 40.0)
                     for k, v in (("up", full_up), ("down", full_down), ("right", full_right))}
        return None

    def calibrate_point(self, poses, screen):
        """Point mode: map poses to screen spots from the five boxes; return a problem or None."""
        g0, gf = poses["center"], poses["flip"]
        corners = PointMap.CORNERS
        if angle(g0, gf) < 60:
            return (f"palm up was only {angle(g0, gf):.0f}° from the middle box. "
                    "Turn the palm fully to the ceiling.")
        if min(angle(gf, poses[k]) for k in corners) < 35:
            return "palm up looked too much like pointing at a corner. Tip less for the corners."
        top = mean([poses["tl"], poses["tr"]])
        e1 = unit(minus(top, times(g0, dot(top, g0))))
        self.basis = (g0, e1, cross(g0, e1))
        pts = {k: self._coords(poses[k]) for k in ("center",) + corners}

        def mid(a, b):
            return (pts[a][0] + pts[b][0]) / 2, (pts[a][1] + pts[b][1]) / 2

        problem = None
        names = {"tl": "top-left", "tr": "top-right", "br": "bottom-right", "bl": "bottom-left"}
        near = [k for k in corners if angle(g0, poses[k]) < 15]
        if math.dist(mid("tl", "bl"), mid("tr", "br")) < 15:
            problem = ("the left and right boxes came out almost the same. Tip the gun left and "
                       "right for them: the ring can't feel you turning toward a box.")
        elif math.dist(mid("tl", "tr"), mid("bl", "br")) < 15:
            problem = "the top and bottom boxes came out almost the same. Aim further up and down."
        elif near:
            problem = (f"the {', '.join(names[k] for k in near)} pose was under 15° from the middle, "
                       "which makes the cursor jumpy. Tip and aim further for the corners.")
        else:
            c = pts["center"]
            dets = []
            for a, b in zip(corners, corners[1:] + corners[:1]):
                u = (pts[a][0] - c[0], pts[a][1] - c[1])
                v = (pts[b][0] - c[0], pts[b][1] - c[1])
                dets.append(u[0] * v[1] - u[1] * v[0])
            if not (all(d > 0 for d in dets) or all(d < 0 for d in dets)):
                problem = ("the boxes came out in a jumbled order. Aim up/down for height and "
                           "tip left/right for sideways.")
        if problem:
            self.basis = None
            return problem
        self.pointmap = PointMap(pts, screen)
        self.flip = gf
        self.range_limit = min(85.0, max(angle(g0, poses[k]) for k in corners) + 20.0)
        return None

    def _split(self, a, b):
        """Split tangent angles into (up, right) along the calibrated axes."""
        (vx, vy), (rx, ry), det = self.axes
        return (a * ry - b * rx) / det, (vx * b - vy * a) / det

    def _coords(self, g):
        """Angles (degrees) of g away from the center pose, along the two tangent directions.

        Distances are true angles from center, so tipping the hand traces a straight
        line even far out; per-axis atan2 bent big tips into a diagonal.
        """
        g0, e1, e2 = self.basis
        c = dot(g, g0)
        t = minus(g, times(g0, c))
        n = math.sqrt(dot(t, t))
        if n < 1e-9:
            return 0.0, 0.0
        theta = math.degrees(math.atan2(n, c))
        return theta * dot(t, e1) / n, theta * dot(t, e2) / n

    def feed(self, v):
        """Process one sample; return a list of (event, detail) tuples."""
        self.n += 1
        self.raw.append(v)
        # Steering state ~300 ms ago: during a sweep the motion swamps gravity, so the
        # orientation reads wildly off and a sweep is judged by where the hand was before.
        self.ok_hist.append(self.active and self.in_range())
        self.lp = v if self.lp is None else tuple(
            p + EMA_ALPHA * (c - p) for p, c in zip(self.lp, v))
        self.fast = v if self.fast is None else tuple(
            p + STEER_ALPHA * (c - p) for p, c in zip(self.fast, v))
        self.g = unit(self.lp)
        self.hist.append(self.g)
        events = self._tap() + self._sweep(v)
        if self.basis is None:
            return events

        self.off = angle(self.g, self.basis[0])
        if self.pointmap:
            self.target = self.pointmap(*self._coords(self.g))
        else:
            self.up, self.right = self._split(*self._coords(unit(self.fast)))
        if self.active and not self.recentered and not self.turned and not self.aimless:
            # Waiting for the hand to reach the middle, but the finger gun keeps reading upside down:
            # the ring has most likely turned around on the finger since the setup, so it never will.
            self.upside = self.upside + 1 if self.off > TURNED_OFF else max(0, self.upside - 1)
            if self.upside >= TURNED_SAMPLES:
                self.turned = True
                events = events + [("turned", "")]
        if self.active and not self.recentered and self.off < 8.0:
            self.recentered = True
        return events + self._toggle()

    def set_active(self, on):
        """Steering on/off from outside (on at start, or the dashboard). The cursor waits until you
        aim at the center, and until then palm up keeps steering on instead of switching it off."""
        self.active = on
        self.recentered = False
        self.outside_on = on

    def _toggle(self):
        d = angle(self.g, self.flip)
        self.flip_run = self.flip_run + 1 if d < FLIP_NEAR else 0
        if d > FLIP_REARM:
            self.flip_ready = True
        if self.flip_ready and self.flip_run >= FLIP_SAMPLES:
            self.flip_ready = False
            if self.active and self.outside_on and not self.recentered and not self.aimless:
                return [("on", "")]  # the usual palm up to start steering, but it's already on
            self.active = not self.active
            self.recentered = False
            self.outside_on = False
            return [("on" if self.active else "off", "")]
        return []

    def _tap(self):
        events = []
        if len(self.raw) == 3:
            a, b, c = self.raw
            jerk = tuple(a[i] - 2 * b[i] + c[i] for i in range(3))
            d2 = math.sqrt(dot(jerk, jerk))
        else:
            d2 = 0.0
        self.context.append((self.g, d2))
        if self.pending is None:
            if d2 > self.tap_threshold and self.n - self.last_tap_n > TAP_GAP:
                # Judge what the hand was doing just before, leaving out the spike's own rise.
                before = list(self.context)[:-3]
                full = len(self.context) == self.context.maxlen
                self.pending = {"n": self.n, "pre": self.hist[0], "d2": d2,
                                "motion": angle(self.context[0][0], self.context[-3][0]) if full else 0.0,
                                "shake": statistics.median(d for _, d in before) if before else 0.0}
        else:
            p = self.pending
            p["d2"] = max(p["d2"], d2)
            if self.n - p["n"] >= TAP_POST:
                turn = angle(p["pre"], self.g)
                sharp = p["d2"] / max(p["shake"], 1.0)
                self.last_spike = {"d2": p["d2"], "turn": turn, "motion": p["motion"], "sharp": sharp}
                detail = (f"spike {p['d2']:.0f}, turned {turn:.1f}°, moved {p['motion']:.0f}° before, "
                          f"{sharp:.0f}× the background")
                ok = (turn < self.max_turn and p["motion"] < self.steady and sharp >= self.sharp
                      and not self.sweeping())
                events.append(("tap" if ok else "rejected", detail))
                self.last_tap_n = self.tap_n = p["n"]
                self.pending = None
        return events

    def _sweep(self, v):
        """Spot a quick up/down hand movement: a push, then a harder opposite brake."""
        self.grav = v if self.grav is None else tuple(
            g + 0.03 * (c - g) for g, c in zip(self.grav, v))
        vert = dot(minus(v, self.grav), unit(self.grav))
        self.vert += 0.4 * (vert - self.vert)
        sign = 1 if self.vert > 0 else -1
        if self.lobe and (abs(self.vert) < LOBE_MIN or self.lobe["sign"] != sign):
            self.prev_lobe = {**self.lobe, "end": self.n}
            self.lobe = None
        if abs(self.vert) < LOBE_MIN:
            return []
        if self.lobe is None:
            self.lobe = {"sign": sign, "peak": 0.0, "start": self.n, "fired": False,
                         "ok": self.ok_hist[0]}
        self.lobe["peak"] = max(self.lobe["peak"], abs(self.vert))
        push = self.prev_lobe
        if (not self.lobe["fired"] and self.lobe["peak"] > SWEEP_BRAKE and push
                and push["sign"] == -sign and push["peak"] > SWEEP_PUSH
                and self.lobe["start"] - push["end"] <= SWEEP_LINK
                and self.n - self.last_sweep_n > SWEEP_GAP):
            self.lobe["fired"] = True
            self.last_sweep_n = self.n
            self.sweep_ok = push["ok"]
            self.sweep_start_n = push["start"]
            return [("sweep", "up" if push["sign"] > 0 else "down")]
        return []

    def in_range(self):
        return self.off < self.range_limit

    def velocity(self, max_speed, deadzone):
        """Cursor velocity in px/s (screen y grows downward)."""
        if not (self.active and self.recentered and self.in_range()) or self.sweeping():
            return 0.0, 0.0

        def speed(theta, full):
            s = (abs(theta) - deadzone) / max(full - deadzone, 1.0)
            return math.copysign(max_speed * min(max(s, 0.0), 1.5) ** 1.6, theta)

        vertical_full = self.full["up"] if self.up > 0 else self.full["down"]
        return speed(self.right, self.full["right"]), -speed(self.up, vertical_full)


class Flow:
    """Walks through the poses (and the optional test) one Enter press at a time.

    Timing counts ring samples, so a replayed recording runs through it the same
    way a live ring does. Without a keyboard (`auto`), prompts advance on their own.
    """

    def __init__(self, steps, ring, auto, play, say, note=lambda **_: None):
        self.steps, self.ring, self.auto, self.play, self.say = steps, ring, auto, play, say
        self.note = note  # tells the dashboard where setup is (--status), so it can show the steps
        self.i = 0
        self.poses = {}
        self.results = {}
        self.tally = {}
        self.count = 0
        if steps:
            self._start_step()
        else:
            self.stage = "done"

    def _start_step(self):
        kind, key, title, text = self.steps[self.i]
        self.say(f"\n── Step {self.i + 1} of {len(self.steps)}: {title} ──\n{text}\n"
                 "→ Press Enter (other hand) when you're ready.")
        self.stage = "prompt"
        self.count = 0
        self._tell("prompt")

    def _tell(self, stage, **extra):
        _, key, title, text = self.steps[min(self.i, len(self.steps) - 1)]
        self.note(stage=stage, step=min(self.i + 1, len(self.steps)), of=len(self.steps), key=key,
                  title=title, text=text.replace("\n", " "), **extra)

    def on_key(self):
        if self.stage == "prompt":
            self._begin()
        elif self.stage == "run":
            self._end_test()

    def on_event(self, kind):
        if self.stage == "run" and kind in self.tally:
            self.tally[kind] += 1

    def on_sample(self):
        self.count += 1
        if self.stage == "prompt" and self.auto and self.count >= 25:
            self._begin()
        elif self.stage == "countdown":
            if self.count % RATE == 1:
                self.play("Tink")
                self.say(f"   {3 - self.count // RATE}...")
                self._tell("countdown", n=3 - self.count // RATE)
            if self.count >= 3 * RATE:
                self.stage = "measure"
                self.collected = []
                self.say("   Hold still...")
                self._tell("measure")
        elif self.stage == "measure":
            self.collected.append(self.ring.g)
            if len(self.collected) >= MEASURE_SAMPLES:
                self._end_pose()
        elif self.stage == "run" and self.auto and self.count >= 20 * RATE:
            self._end_test()

    def _begin(self):
        self.count = 0
        if self.steps[self.i][0] == "pose":
            self.stage = "countdown"
        else:
            self.stage = "run"
            self.tally = {"tap": 0, "rejected": 0, "on": 0, "off": 0}
            self.play("Tink")
            self.say("   GO. Press Enter when you're done.")

    def _end_pose(self):
        key = self.steps[self.i][1]
        self.poses[key] = mean(self.collected)
        self.play("Glass")
        self.say("   ✓ Got it.")
        self._tell("got")
        if key == POSES[-1][0]:
            problem = self.ring.calibrate(self.poses)
            if problem:
                self.play("Basso")
                self.say(f"\nCalibration didn't work: {problem}\nLet's redo the poses.")
                self._tell("failed", problem=problem)
                self.poses = {}
                self.i = 0
                self._start_step()
                return
            f = self.ring.full
            self.say(f"\nCalibrated (ranges: up {f['up']:.0f}°, down {f['down']:.0f}°, "
                     f"tip right {f['right']:.0f}°).")
        self._next()

    def _end_test(self):
        self.results[self.steps[self.i][1]] = self.tally
        self._next()

    def _next(self):
        self.i += 1
        if self.i < len(self.steps):
            self._start_step()
        else:
            self.stage = "done"
            self._tell("done")


class PointFlow:
    """Point-mode calibration: light up each box and capture the pose once the hand holds still."""

    def __init__(self, ring, overlay, screen, play, say):
        self.ring, self.overlay, self.screen, self.play, self.say = ring, overlay, screen, play, say
        self.steps = POINT_STEPS
        self.i = 0
        self.poses = {}
        self.results = {}
        self.tally = {}
        self.stage = "aim"
        self._start_step()

    def on_key(self):
        pass

    def on_event(self, kind):
        pass

    def _start_step(self):
        self.count = 0
        self.hold = []
        self.shown = None
        self.say(f"\n{self.i + 1} of {len(self.steps)}: {self.steps[self.i][1]}")
        self.play("Tink")
        self._show("wait")

    def _show(self, state):
        if state != self.shown:
            self.shown = state
            self.overlay.show(self.steps[self.i][0], state)

    def on_sample(self):
        if self.stage == "done":
            return
        self.count += 1
        if self.count < POINT_GRACE:
            return
        key = self.steps[self.i][0]
        ring = self.ring
        still = angle(ring.g, ring.hist[0]) < POINT_STILL
        moved = key == "center" or angle(ring.g, self.poses["center"]) > (60.0 if key == "flip" else 8.0)
        if still and moved:
            self.hold.append(ring.g)
            self._show("hold")
        else:
            self.hold = []
            self._show("wait")
        if len(self.hold) < POINT_HOLD:
            return
        self.poses[key] = mean(self.hold)
        self.play("Glass")
        self.say("   ✓ Got it.")
        self._tell("got")
        self.i += 1
        if self.i < len(self.steps):
            self._start_step()
            return
        problem = ring.calibrate_point(self.poses, self.screen)
        if problem:
            self.play("Basso")
            self.say(f"\nCalibration didn't work: {problem}\nLet's go again.")
            self.poses = {}
            self.i = 0
            self._start_step()
        else:
            self.stage = "done"
            self.overlay.close()
            self.say("\nCalibrated.")


# --- main --------------------------------------------------------------------

def read_samples(q):
    for line in sys.stdin:
        try:
            s = json.loads(line)
            q.put(("sample", (s["x"], s["y"], s["z"])))
        except (json.JSONDecodeError, KeyError):
            continue
    q.put(("eof", None))


def read_keys(q, tty):
    for _ in tty:
        q.put(("key", None))


def read_control(q, f):
    """Commands from the dashboard, one JSON object per line: steer, settings, quit."""
    for line in f:
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(cmd, dict):
            q.put(("key", None) if cmd.get("next") else ("control", cmd))  # "next" is Enter from the dashboard


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--move", action="store_true", help="actually move the cursor")
    ap.add_argument("--click", action="store_true", help="taps click (needs --move)")
    ap.add_argument("--test", action="store_true", help="guided dry-run check after calibration")
    ap.add_argument("--record-sweeps", action="store_true",
                    help="guided recording of hand sweeps and other movements (no calibration)")
    ap.add_argument("--tap-threshold", type=float, default=TAP_THRESHOLD,
                    help="spike size a tap needs when there's no taps.json")
    ap.add_argument("--speed", type=float, default=1200.0, help="max cursor speed, px/s")
    ap.add_argument("--deadzone", type=float, default=5.0, help="degrees of tilt ignored")
    ap.add_argument("--scroll-px", type=float, default=400.0, help="pixels scrolled per sweep")
    ap.add_argument("--flip-scroll", action="store_true",
                    help="sweep up shows what's below instead of what's above")
    ap.add_argument("--recalibrate", action="store_true", help="redo the setup poses")
    ap.add_argument("--require-saved", action="store_true",
                    help="exit instead of running setup when there's no saved calibration")
    ap.add_argument("--calibrate-taps", action="store_true",
                    help="record your taps and normal movement, and set the click rules from them")
    ap.add_argument("--mode", choices=("joystick", "point"), default="joystick",
                    help="joystick: tilt to move; point: the cursor goes where you point")
    ap.add_argument("--remote", choices=tuple(REMOTE),
                    help="remote control instead of the cursor: taps and flicks press keys "
                         "(slides: next/previous slide; media: play/pause, next track; both: volume)")
    ap.add_argument("--no-press", action="store_true", help="remote mode without pressing keys (a dry run)")
    ap.add_argument("--quiet", action="store_true", help="no sounds")
    ap.add_argument("--steer", action="store_true",
                    help="start with steering on (it still waits until you aim at the center)")
    ap.add_argument("--status", action="store_true",
                    help="also print machine-readable '@{json}' status lines (for the dashboard)")
    ap.add_argument("--control-fd", type=int, help="read dashboard commands (JSON lines) from this fd")
    args = ap.parse_args()
    if args.click and not args.move:
        ap.error("--click needs --move")
    point = args.mode == "point"

    def quit_on_suspend(*_):
        raise KeyboardInterrupt  # Ctrl-Z would leave the ring streaming with nobody watching
    signal.signal(signal.SIGTSTP, quit_on_suspend)
    if point and (args.test or args.record_sweeps):
        ap.error("--test and --record-sweeps work in joystick mode only")

    def play(name):
        if not args.quiet:
            subprocess.Popen(["afplay", str(SOUNDS / f"{name}.aiff")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def say(msg):
        print(f"\r\033[K{msg}", flush=True)

    def status(kind, **data):
        if args.status:
            print("@" + json.dumps({"t": kind, **data}), flush=True)

    def steering(on):
        play("Hero" if on else "Bottle")
        say("   ON: steering" if on else "   OFF")
        status("steer", on=on)

    mouse = Mouse()
    keys = Keys() if args.remote else None
    if (args.move or args.remote) and not mouse.has_access():
        mouse.request_access()
        say("macOS hasn't allowed this app to control the cursor yet.\n"
            "Allow it in System Settings > Privacy & Security > Accessibility, then run again.")
        return

    try:
        tty = open("/dev/tty")
    except OSError:
        tty = None

    log_dir = Path(__file__).parent / "data"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"session-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log = log_path.open("w")

    mode = (f"remote: {args.remote}" if args.remote else "clicks on" if args.click
            else "cursor moves" if args.move else "dry run, nothing moves")
    say(f"Ring mouse ({mode}). Connecting to the ring...")

    q = queue.Queue()
    threading.Thread(target=read_samples, args=(q,), daemon=True).start()
    if tty:
        threading.Thread(target=read_keys, args=(q, tty), daemon=True).start()
    if args.control_fd is not None:
        threading.Thread(target=read_control, args=(q, open(args.control_fd)), daemon=True).start()

    taps_path = Path(__file__).parent / "taps.json"
    if args.calibrate_taps:
        ring = Ring(400.0, max_turn=90.0, steady=180.0, sharp=0.0)  # let every jolt through; the fit decides
    elif taps_path.exists():
        t = json.loads(taps_path.read_text())
        # Older files also hold a tap direction ("axis", "cone"); it's ignored because it rejected real taps.
        ring = Ring(t["threshold"], t.get("max_turn", TAP_MAX_TURN))
        say(f"Using your tap calibration ({t['caught']} of {t['taps']} taps caught). "
            "Run with --calibrate-taps to redo it.")
    else:
        ring = Ring(args.tap_threshold)
    spikes = []
    guided = args.test or args.record_sweeps or args.calibrate_taps
    cal_path = Path(__file__).parent / ("calibration-point.json" if point else "calibration.json")
    screen = point_targets(mouse.main_display())
    reuse = not (guided or args.recalibrate) and cal_path.exists()
    if reuse:
        saved_poses = {k: tuple(v) for k, v in json.loads(cal_path.read_text()).items()}
        problem = (ring.calibrate_point(saved_poses, screen) if point
                   else ring.calibrate(saved_poses))
        if problem:
            say(f"The saved calibration didn't load ({problem}); let's redo it.")
            reuse = False
        else:
            saved = time.strftime("%b %d %H:%M", time.localtime(cal_path.stat().st_mtime))
            say(f"Using your saved calibration from {saved}. "
                "If steering feels off, quit and run again with --recalibrate.")
    if args.require_saved and not reuse and not guided:
        say("NEEDS_CALIBRATION: no saved calibration. Run run.sh in a terminal once to set it up.")
        return
    if args.calibrate_taps:
        steps = [("test", *t) for t in TAP_CAL]
    elif args.record_sweeps:
        steps = [("test", *t) for t in SWEEPS]
    elif reuse:
        steps = []
    else:
        steps = [("pose", *p) for p in POSES] + ([("test", *t) for t in TESTS] if args.test else [])
    flow = None
    overlay = None
    euro = (OneEuro(), OneEuro())
    goal = None
    was_active = False
    x0, y0, dw, dh = mouse.main_display()
    pos = None
    last_posted = prev_posted = None
    vel = (0.0, 0.0)
    pos_hist = deque(maxlen=60)  # (sample, cursor position) for the last ~1.2 s

    def pos_at(n):
        """Cursor position as of sample n, to undo what a tap or sweep jolt did to it."""
        older = [p for k, p in pos_hist if k <= n]
        return older[-1] if older else None

    ring.aimless = bool(args.remote)
    double_click_s = double_click_seconds()
    remote_tap = None  # when a remote tap landed; it's a single tap unless a second follows in time

    def activate(on):
        ring.set_active(on)

    def remote(gesture):
        name, label = REMOTE[args.remote][gesture]
        if not args.no_press:
            keys.press(name)
        status("remote", action=label)
        say(f"   → {label}")
    last_click = (0.0, None, 0)  # when, where, and which click of a double-click it was
    streak = (0.0, 0, 0)  # when, direction, and how many sweeps in a row
    scroll_left = 0.0
    last_status = last_aim = 0.0
    last_tick = time.monotonic()
    live_announced = False
    cal_saved = reuse
    done = False
    try:
        while not done:
            tick_end = time.monotonic() + 0.01
            while not done:
                try:
                    kind, v = q.get_nowait()
                except queue.Empty:
                    break
                if kind == "eof":
                    done = True
                elif kind == "control":
                    if v.get("quit"):
                        done = True  # stop steering now; closing stdin then ends the ring's stream
                    if "steer" in v and ring.basis is not None:
                        on = not ring.active if v["steer"] == "toggle" else bool(v["steer"])
                        if on != ring.active:
                            activate(on)
                            steering(on)
                    for key in ("speed", "deadzone", "scroll_px"):
                        try:
                            setattr(args, key, float(v[key]))
                        except (KeyError, TypeError, ValueError):
                            pass
                    if "flip_scroll" in v:
                        args.flip_scroll = bool(v["flip_scroll"])
                    if args.remote and v.get("remote") in REMOTE:
                        args.remote = v["remote"]
                        remote_tap = None
                elif kind == "key":
                    if flow:
                        flow.on_key()
                        step = flow.steps[min(flow.i, len(flow.steps) - 1)][1]
                        log.write(json.dumps({"n": ring.n, "event": "enter", "stage": flow.stage,
                                              "step": step}) + "\n")
                else:
                    if flow is None:
                        if point and not reuse and not guided:
                            overlay = Overlay(screen)
                            flow = PointFlow(ring, overlay, screen, play, say)
                        else:
                            # driven by the dashboard's Ready button when it's attached, else by Enter
                            flow = Flow(steps, ring, tty is None and args.control_fd is None, play, say,
                                        lambda **d: status("calib", **d))
                    log.write(json.dumps({"n": ring.n + 1, "x": v[0], "y": v[1], "z": v[2]}) + "\n")
                    events = ring.feed(v)
                    flow.on_sample()
                    if pos is not None:
                        pos_hist.append((ring.n, pos))
                    if point and ring.target and flow.stage == "done":
                        if ring.active and not was_active:
                            euro = (OneEuro(), OneEuro())  # start from where the hand points now
                        was_active = ring.active
                        # A tap or sweep jolt fakes a big orientation change; keep the last good spot.
                        if ring.active and ring.in_range() and not (ring.pending or ring.sweeping()):
                            gx = min(max(euro[0](ring.target[0], 1 / RATE), x0), x0 + dw - 1)
                            gy = min(max(euro[1](ring.target[1], 1 / RATE), y0), y0 + dh - 1)
                            goal = (gx, gy)
                    live = flow.stage == "done"
                    for ev, detail in events:
                        log.write(json.dumps({"n": ring.n, "event": ev, "detail": detail}) + "\n")
                        flow.on_event(ev)
                        if not (live or flow.stage == "run"):
                            continue
                        if args.calibrate_taps:
                            if ev in ("tap", "rejected"):
                                step = flow.steps[flow.i][1]
                                spikes.append((step, dict(ring.last_spike)))
                                if step == "taps":
                                    play("Pop")
                                    say(f"   tap #{sum(k == 'taps' for k, _ in spikes)}  ({detail})")
                                else:
                                    say(f"   jolt  ({detail})")
                            continue
                        if ev in ("on", "off"):
                            steering(ev == "on")
                        elif ev == "turned":
                            play("Basso")
                            say("   The ring reads your hand upside down compared with the setup: it has probably turned\n"
                                "   around on your finger. Turn it back the way it sat, or redo the setup: run.sh --recalibrate")
                            status("turned")
                        elif ev == "tap":
                            play("Pop")
                            clicks = 0
                            if live and args.click and ring.active and ring.in_range():
                                target = pos_at(ring.tap_n - 2) or mouse.location()
                                target = (round(target[0]), round(target[1]))
                                when, where, clicks = last_click
                                # A quick second tap on the same spot is a double-click (a third, a triple).
                                if (where and time.monotonic() - when < double_click_s
                                        and math.dist(target, where) <= DOUBLE_CLICK_PX):
                                    target, clicks = where, min(clicks + 1, 3)
                                else:
                                    clicks = 1
                                mouse.click(*target, clicks)
                                pos, last_posted = target, target
                                last_click = (time.monotonic(), target, clicks)
                                status("click", count=clicks)
                            if live and args.remote and ring.active:
                                if remote_tap is not None and time.monotonic() - remote_tap < double_click_s:
                                    remote_tap = None
                                    remote("double")
                                else:
                                    remote_tap = time.monotonic()  # a single tap, once no second one follows
                            count = f" #{flow.tally['tap']}" if flow.stage == "run" else ""
                            what = {0: "", 1: " → click", 2: " → double-click"}.get(clicks, " → triple-click")
                            say(f"   TAP{count}{what}  ({detail})")
                        elif ev == "sweep":
                            play("Morse")
                            if live and args.remote and ring.active and ring.sweep_ok:
                                remote(detail)
                            scrolled = live and args.move and ring.sweep_ok
                            if scrolled:
                                back = pos_at(ring.sweep_start_n - 2)
                                if back and not point:
                                    pos = back
                                    last_posted = (round(back[0]), round(back[1]))
                                    mouse.move(*last_posted)
                                sign = 1 if detail == "up" else -1
                                # Back-to-back sweeps the same way scroll further each time, for long pages.
                                when, way, run = streak
                                run = run + 1 if way == sign and time.monotonic() - when < SCROLL_STREAK_S else 0
                                streak = (time.monotonic(), sign, run)
                                boost = min(1.0 + 0.5 * run, SCROLL_STREAK_MAX)
                                scroll_left += sign * args.scroll_px * boost * (-1 if args.flip_scroll else 1)
                                status("scroll", dir=detail, boost=boost)
                            say(f"   SWEEP {detail.upper()}{' → scroll' if scrolled else ''}")
                        elif ev == "rejected":
                            say(f"   not counted as a tap  ({detail})")
                    if flow.stage == "done" and not cal_saved and ring.basis is not None:
                        cal_saved = True
                        cal_path.write_text(json.dumps(flow.poses))
                    if flow.stage == "done":
                        if guided:
                            done = True
                        elif not live_announced:
                            live_announced = True
                            say("\nReady. Palm up for 1 s switches steering on/off. Ctrl-C quits.")
                            status("ready", range=ring.range_limit, full=ring.full)
                            if args.steer and not ring.active and ring.basis is not None:
                                activate(True)
                                steering(True)
                                say("   Aim at the middle of the screen to start moving the cursor.")

            now = time.monotonic()
            dt, last_tick = now - last_tick, now
            if remote_tap is not None and now - remote_tap >= double_click_s:
                remote_tap = None
                if ring.active:
                    remote("tap")
            live = flow is not None and flow.stage == "done" and not guided
            if overlay:
                overlay.pump()
            if live and args.move and point:
                if ring.active and goal:
                    cur = mouse.location()
                    if pos is None or not was_active:
                        pos = cur
                    k = 1.0 - math.exp(-dt / 0.03)
                    pos = (pos[0] + k * (goal[0] - pos[0]), pos[1] + k * (goal[1] - pos[1]))
                    target = (round(pos[0]), round(pos[1]))
                    if target != last_posted:
                        mouse.move(*target)
                        last_posted = target
                if scroll_left:
                    step = math.copysign(min(abs(scroll_left), 40.0), scroll_left)
                    mouse.scroll(step)
                    scroll_left -= step
            elif live and args.move:
                vx, vy = ring.velocity(args.speed, args.deadzone)
                # Angles arrive in pairs every 40 ms; ease the speed between them.
                k = 1.0 - math.exp(-dt / VEL_TAU)
                vel = (vel[0] + k * (vx - vel[0]), vel[1] + k * (vy - vel[1]))
                cur = mouse.location()
                # macOS sometimes reports a move a tick late; only a position matching
                # neither of the last two moves means the trackpad moved the cursor.
                if (pos is None or last_posted is None or (math.dist(cur, last_posted) > 3
                        and (prev_posted is None or math.dist(cur, prev_posted) > 3))):
                    pos = cur
                if abs(vel[0]) > 1.0 or abs(vel[1]) > 1.0:
                    pos = mouse.clamp(pos, (pos[0] + vel[0] * dt, pos[1] + vel[1] * dt))
                    target = (round(pos[0]), round(pos[1]))
                    if target != last_posted:
                        mouse.move(*target)
                        prev_posted, last_posted = last_posted, target
                elif last_posted is None or math.dist(cur, last_posted) > 3:
                    prev_posted, last_posted = None, cur
                if scroll_left:
                    step = math.copysign(min(abs(scroll_left), 60.0), scroll_left)
                    mouse.scroll(step)
                    scroll_left -= step

            if live and args.status and not point and now - last_aim > STATUS_EVERY_S:
                last_aim = now
                vx, vy = ring.velocity(args.speed, args.deadzone)
                status("aim", up=round(ring.up, 1), right=round(ring.right, 1), off=round(ring.off, 1),
                       on=ring.active, centered=ring.recentered, range=ring.in_range(),
                       moving=abs(vx) > 1.0 or abs(vy) > 1.0)
            if live and now - last_status > 0.5 and sys.stdout.isatty():
                last_status = now
                state = "ON " if ring.active else "off"
                if ring.active and not ring.in_range():
                    state = "ON (out of range)"
                where = (f"x {ring.target[0]:5.0f}  y {ring.target[1]:5.0f}" if point and ring.target
                         else f"up {ring.up:+6.1f}°  right {ring.right:+6.1f}°")
                print(f"\r\033[K{state}  {where}  off-center {ring.off:5.1f}°", end="", flush=True)
            time.sleep(max(0.0, tick_end - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        log.close()
        if overlay:
            overlay.close()

    print()
    if args.calibrate_taps and flow and flow.stage == "done":
        rules, report = fit_taps([s for k, s in spikes if k == "taps"],
                                 [s for k, s in spikes if k == "moves"])
        if rules:
            taps_path.write_text(json.dumps(rules))
            say(f"Tap calibration saved: {report}")
        else:
            say(f"Tap calibration not saved: {report}")
    elif flow and flow.results:
        say("Test results:")
        for key, tally in flow.results.items():
            say(f"  {key:<7} taps {tally['tap']:>2}   not taps {tally['rejected']:>2}   "
                f"on/off switches {tally['on'] + tally['off']}")
    say(f"Session log: {log_path}")


if __name__ == "__main__":
    main()
