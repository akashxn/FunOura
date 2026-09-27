"""Health-app style metrics from what the ring logged, for FunOura's Health tab.

The ring hands us beats (IBI), per-minute activity (MET), motion and skin temperature. Some metrics
come straight from those (heart rate, HRV); the rest are simple stand-ins until open_oura decodes the
ring's own summaries. Everything marked a proxy below is an estimate, and the app labels it that way.

Proxies, and what they assume:
  steps          walking-intensity MET minutes × a cadence (no step events on this ring yet)
  calories       MET × body weight (WEIGHT_KG), plus resting burn from Mifflin-St Jeor
  sleep          long, still, low-MET stretches while worn; stages from heart rate within the night
  stress         heart rate above resting plus HRV below your day's typical, while still
  breathing rate respiratory sinus arrhythmia: the rise and fall of beat intervals per minute
  VO2 max        Uth's ratio, 15.3 × max HR / resting HR, with max HR from AGE
  scores         plain weighted blends; no 14-day baselines yet
"""

import json
import math
import sqlite3
import statistics
import time

AGE = 30
WEIGHT_KG = 70.0
HEIGHT_CM = 175.0
STEP_GOAL = 8000
ACTIVE_GOAL_MIN = 30
SLEEP_GOAL_H = 8.0

WINDOW_HOURS = 36  # enough to reach back over last night
GRAPH_HOURS = 12  # matches the existing graphs
WORN_C = 30.0  # skin temperature above this means the ring is on a finger
HR_MAX = 208 - 0.7 * AGE  # Tanaka
BMR_KCAL_DAY = 10 * WEIGHT_KG + 6.25 * HEIGHT_CM - 5 * AGE + 5  # Mifflin-St Jeor


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _pct(values, q):
    s = sorted(values)
    if not s:
        return None
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def _rmssd(diffs):
    return math.sqrt(sum(d * d for d in diffs) / len(diffs)) if len(diffs) >= 5 else None


def _bucket(points, seconds, start, fn=statistics.mean):
    """[[t, v], ...] averaged into buckets of `seconds`, from `start` on."""
    groups = {}
    for t, v in points:
        if t >= start:
            groups.setdefault(int(t // seconds) * seconds, []).append(v)
    return [[t + seconds // 2, round(fn(vs), 2)] for t, vs in sorted(groups.items())]


def compute(db_path):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return _compute(db)
    finally:
        db.close()


def _compute(db):
    ref_ts, synced = db.execute("select max(ring_timestamp), max(captured_unix) from events").fetchone()
    if ref_ts is None:
        return {}
    boot = db.execute("select max(ring_timestamp) from events where name='ring_start'").fetchone()[0] or 0
    since = max(ref_ts - WINDOW_HOURS * 36000, boot)

    def unix(ts):
        return synced - (ref_ts - ts) / 10  # ring time is in tenths of a second

    def rows(*names):
        return [(unix(ts), json.loads(js)) for ts, js in db.execute(
            f"select ring_timestamp, decoded_json from events where name in ({','.join('?' * len(names))}) "
            "and ring_timestamp>=? and decoded_json is not null order by ring_timestamp", (*names, since))]

    now = synced
    lt = time.localtime(now)
    today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    graph_from = now - GRAPH_HOURS * 3600

    # --- when the ring was on a finger, minute by minute (from skin temperature) ---
    temps = []
    for t, d in rows("temp_event", "sleep_temp_event"):
        probes = d.get("temps_c", [])[:2]
        if probes:
            temps.append((t, sum(probes) / len(probes)))
    worn = set()
    for t, c in temps:
        if c >= WORN_C:
            m = int(t // 60)
            worn.update(range(m - 1, m + 2))  # temperature is logged about once a minute

    # --- activity: one MET value per minute ---
    met = {}
    for t, d in rows("activity_information"):
        bins = d.get("met", [])
        if len(bins) < 5:
            continue  # the short packet logged at start-up isn't a real reading
        for i, v in enumerate(bins):  # the event lands at the end of its bins
            m = int((t - (len(bins) - 1 - i) * 60) // 60)
            if m in worn and 0.5 <= v <= 20:
                met[m] = v

    # --- beats: heart rate and within-packet beat differences ---
    beats = []  # (t, bpm)
    packets = []  # (t, [ibi ms])
    for t, d in rows("green_ibi_quality_event", "ibi_and_amplitude_event"):  # day, night
        ibis = [i for i, q in zip(d.get("ibi_ms", []), d.get("quality", [1] * 99)) if 300 <= i <= 2000 and q]
        if not ibis:
            continue
        packets.append((t, ibis))
        bpm = statistics.median(60000 / i for i in ibis)
        if 35 <= bpm <= 200:
            beats.append((t, bpm))
    hr_by_min = {}
    for t, bpm in beats:
        hr_by_min.setdefault(int(t // 60), []).append(bpm)
    hr_by_min = {m: statistics.median(v) for m, v in hr_by_min.items()}

    out = {"assume": {"age": AGE, "weight_kg": WEIGHT_KG, "height_cm": HEIGHT_CM},
           "goals": {"steps": STEP_GOAL, "active_min": ACTIVE_GOAL_MIN, "sleep_h": SLEEP_GOAL_H}}

    # the ring's own 5-minute HRV (logged at night) and its bedtime, when it logs them
    ring_hrv = []
    for t, d in rows("hrv_event"):
        vals = d.get("rmssd_ms", [])
        ring_hrv += [(t - (len(vals) - 1 - i) * 300, v) for i, v in enumerate(vals) if 5 <= v <= 250]
    bedtimes = [(unix(d["bedtime_start_ds"]), unix(d["bedtime_end_ds"])) for _, d in rows("bedtime_period")
                if "bedtime_start_ds" in d and "bedtime_end_ds" in d]

    sleep = _sleep(met, hr_by_min, packets, worn, bedtimes[-1] if bedtimes else None, ring_hrv)
    out["sleep"] = sleep
    asleep = set(range(sleep["start_min"], sleep["end_min"] + 1)) if sleep else set()

    # --- heart rate ---
    today_hr = [v for m, v in hr_by_min.items() if m * 60 >= today]
    still_hr = [(m, v) for m, v in hr_by_min.items() if met.get(m, 1.0) < 1.6]
    rolling = []  # 5-minute medians while still, the floor of which is resting HR
    for m, _ in still_hr:
        near = [v for mm, v in still_hr if m - 2 <= mm <= m + 2]
        if len(near) >= 2:
            rolling.append(statistics.median(near))
    resting = sleep["lowest_hr"] if sleep and sleep.get("lowest_hr") else _pct(rolling, 0.05)
    out["hr"] = None if not today_hr else {
        # the ends of the minute medians, so one missed or doubled beat doesn't set the range
        "avg": round(statistics.mean(today_hr)), "min": round(_pct(today_hr, 0.02)), "max": round(_pct(today_hr, 0.98)),
        "resting": round(resting) if resting else None, "max_predicted": round(HR_MAX)}

    # time in heart-rate zones today, as shares of the minutes with a heart rate
    zones = [0] * 6  # below zone 1, then zones 1-5 at 50/60/70/80/90 % of max HR
    for m, v in hr_by_min.items():
        if m * 60 >= today:
            zones[_clamp(int((v / HR_MAX - 0.4) * 10), 0, 5)] += 1
    total = sum(zones)
    out["hr_zones"] = [round(z / total, 3) for z in zones] if total else None

    # --- HRV: RMSSD over 5-minute windows, only from beats inside the same packet ---
    windows = {}
    for t, ibis in packets:
        diffs = [b - a for a, b in zip(ibis, ibis[1:]) if abs(b - a) <= 0.2 * a]
        windows.setdefault(int(t // 300) * 300, []).extend(diffs)
    hrv = {t + 150: round(r) for t, ds in windows.items() if (r := _rmssd(ds))}
    for t, v in ring_hrv:
        hrv.setdefault(int(t // 300) * 300 + 150, v)
    hrv = [[t, v] for t, v in sorted(hrv.items())]
    out["hrv"] = [p for p in hrv if p[0] >= graph_from]
    today_hrv = [v for t, v in hrv if t >= today]
    out["hrv_avg"] = round(statistics.mean(today_hrv)) if today_hrv else None
    out["hrv_latest"] = hrv[-1][1] if hrv else None

    # --- stress (proxy): 5-minute windows while still ---
    hrv_ref = _pct([v for _, v in hrv], 0.75)
    stress = []
    for t, v in hrv:
        mins = range(int(t // 60) - 2, int(t // 60) + 3)
        hrs = [hr_by_min[m] for m in mins if m in hr_by_min]
        if not hrs or not resting or not hrv_ref or any(met.get(m, 1.0) >= 1.8 for m in mins):
            continue
        if any(m in asleep for m in mins):
            continue  # sleep is recovery, not daytime stress
        load = 0.5 * _clamp((statistics.mean(hrs) - resting) / 30, 0, 1) + 0.5 * _clamp(1 - v / hrv_ref, 0, 1)
        stress.append([t, round(100 * load)])
    out["stress"] = [p for p in stress if p[0] >= graph_from]
    today_stress = [v for t, v in stress if t >= today]
    out["stress_avg"] = round(statistics.mean(today_stress)) if today_stress else None
    out["stress_high_min"] = 5 * sum(v >= 60 for v in today_stress) if today_stress else None
    out["restored_min"] = 5 * sum(v < 25 for v in today_stress) if today_stress else None

    # --- breathing rate (proxy) ---
    out["resp_rate"] = _breathing(packets, met, asleep)

    # --- activity today ---
    today_min = {m: v for m, v in met.items() if m * 60 >= today and m not in asleep}
    steps_min = {m: _clamp((v - 1.6) * 70, 0, 180) for m, v in today_min.items()}
    hourly = {}
    for m, s in steps_min.items():
        hourly[m // 60 * 3600] = hourly.get(m // 60 * 3600, 0) + s
    active_kcal = sum((v - 1) * 3.5 * WEIGHT_KG / 200 for v in today_min.values() if v > 1)
    elapsed = (now - today) / 86400
    out["activity"] = None if not today_min else {
        "steps": round(sum(steps_min.values())),
        "steps_hourly": [[t + 1800, round(s)] for t, s in sorted(hourly.items())],
        "active_kcal": round(active_kcal),
        "total_kcal": round(BMR_KCAL_DAY * elapsed + active_kcal),
        "active_min": sum(v >= 3 for v in today_min.values()),
        "vigorous_min": sum(v >= 6 for v in today_min.values()),
        "light_min": sum(1.6 <= v < 3 for v in today_min.values()),
        "sedentary_min": sum(v < 1.6 for v in today_min.values()),
        "longest_still_min": _longest(m for m, v in today_min.items() if v < 1.6),
        "move_hours": len({m // 60 for m, v in today_min.items() if v >= 2}),
        "met_avg": round(statistics.mean(today_min.values()), 2),
    }
    out["met"] = _bucket([(m * 60, v) for m, v in met.items()], 600, graph_from)
    out["wear_min"] = sum(1 for m in worn if today <= m * 60 <= now)

    # --- skin temperature against today's worn baseline ---
    worn_temps = [(t, c) for t, c in temps if c >= 32]  # past the few minutes it takes to warm up
    base = statistics.median(c for _, c in worn_temps) if len(worn_temps) >= 10 else None
    out["temp_dev"] = round(worn_temps[-1][1] - base, 2) if base else None
    out["temp_dev_series"] = _bucket([(t, c - base) for t, c in worn_temps], 600, graph_from) if base else []

    # --- VO2 max (proxy) ---
    out["vo2max"] = round(15.3 * HR_MAX / resting, 1) if resting else None

    out["scores"] = _scores(out, sleep, base)
    return out


def _longest(minutes):
    best = run = 0
    last = None
    for m in sorted(minutes):
        run = run + 1 if last is not None and m - last <= 2 else 1  # tolerate a missing minute
        best, last = max(best, run), m
    return best


def _sleep(met, hr_by_min, packets, worn, bedtime, ring_hrv):
    """The main sleep in the window. The ring's own bedtime when it logged one; otherwise (proxy) the
    longest still, low-MET stretch while worn. Worn minutes without a MET value count as still."""
    if bedtime:
        start, end = int(bedtime[0] // 60), int(bedtime[1] // 60)
    else:
        level = {m: met.get(m, 1.0) for m in set(met) | worn}
        calm = set()
        for m in level:
            near = [level[x] for x in range(m - 15, m + 16) if x in level]
            if len(near) >= 10 and statistics.mean(near) <= 1.15 and level[m] <= 1.3:
                calm.add(m)
        runs, run = [], []
        for m in sorted(calm):
            if run and m - run[-1] > 15:
                runs.append(run)
                run = []
            run.append(m)
        if run:
            runs.append(run)
        runs = [r for r in runs if r[-1] - r[0] >= 90]
        if not runs:
            return None
        night = max(runs, key=lambda r: r[-1] - r[0])
        start, end = night[0], night[-1]
    if end - start < 30:
        return None
    in_bed = end - start + 1
    restless = sum(1 for m in range(start, end + 1) if met.get(m, 1.0) > 1.3)
    asleep = sum(1 for m in range(start, end + 1) if m in worn) - restless

    # stages from heart rate, in 5-minute blocks: low HR is deep, high HR while still is REM
    hrs = {m: v for m, v in hr_by_min.items() if start <= m <= end}
    lo, hi = _pct(list(hrs.values()), 0.25), _pct(list(hrs.values()), 0.7)
    stages = []
    for b in range(start, end + 1, 5):
        block = range(b, min(b + 5, end + 1))
        if sum(met.get(m, 1.0) > 1.3 for m in block) >= 2:  # one fidget isn't waking up
            stage = "awake"
        else:
            vals = [hrs[m] for m in block if m in hrs]
            v = statistics.mean(vals) if vals else None
            stage = "light" if v is None or lo is None else "deep" if v <= lo else "rem" if v >= hi else "light"
        stages.append({"at": b * 60, "stage": stage})
    minutes = {s: 5 * sum(1 for x in stages if x["stage"] == s) for s in ("deep", "rem", "light", "awake")}
    night_diffs = [b - a for t, ibis in packets if start <= t // 60 <= end
                   for a, b in zip(ibis, ibis[1:]) if abs(b - a) <= 0.2 * a]
    night_ring_hrv = [v for t, v in ring_hrv if start <= t // 60 <= end]
    return {"start": start * 60, "end": end * 60, "start_min": start, "end_min": end,
            "asleep_min": asleep, "in_bed_min": in_bed,
            "efficiency": round(asleep / in_bed, 2) if in_bed else None,
            "restless_min": restless, "stages": stages, "stage_min": minutes,
            "lowest_hr": round(min(hrs.values())) if hrs else None,
            "avg_hrv": round(statistics.mean(night_ring_hrv)) if night_ring_hrv
            else round(r) if (r := _rmssd(night_diffs)) else None}


def _breathing(packets, met, asleep):
    """Breaths a minute from how beat intervals rise and fall with breathing (proxy).
    Needs runs of back-to-back packets while still; sleep runs are preferred."""
    runs, run = [], []
    for t, ibis in packets:
        if run and t - run[-1][0] > sum(ibis) / 1000 + 2:  # a gap: the next packet isn't back to back
            runs.append(run)
            run = []
        run.append((t, ibis))
    if run:
        runs.append(run)
    rates = []
    for r in runs:
        series = [i for _, ibis in r for i in ibis]
        seconds = sum(series) / 1000
        m = int(r[0][0] // 60)
        if seconds < 45 or any(met.get(x, 1.0) >= 1.8 for x in range(m, m + int(seconds // 60) + 1)):
            continue
        smooth = [statistics.mean(series[max(0, i - 1):i + 2]) for i in range(len(series))]
        peaks = [i for i in range(2, len(smooth) - 2)
                 if smooth[i] == max(smooth[i - 2:i + 3]) and smooth[i] - min(smooth[i - 2:i + 3]) >= 8]
        rate = len(peaks) / (seconds / 60)
        if 6 <= rate <= 30:
            rates.append((rate, m in asleep))
    if not rates:
        return None
    night = [v for v, s in rates if s]
    return round(statistics.median(night or [v for v, _ in rates]), 1)


def _scores(out, sleep, temp_base):
    """0-100 blends (proxies). Each skips the parts it has no data for."""
    def blend(parts):
        parts = [(w, v) for w, v in parts if v is not None]
        return round(sum(w * v for w, v in parts) / sum(w for w, _ in parts)) if parts else None

    sleep_score = None
    if sleep:
        hours = sleep["asleep_min"] / 60
        deep_rem = (sleep["stage_min"]["deep"] + sleep["stage_min"]["rem"]) / max(sleep["asleep_min"], 1)
        sleep_score = blend([(0.45, 100 * _clamp(hours / (SLEEP_GOAL_H - 1), 0, 1)),
                             (0.25, 100 * _clamp((sleep["efficiency"] - 0.65) / 0.3, 0, 1)),
                             (0.15, 100 * _clamp(deep_rem / 0.45, 0, 1)),
                             (0.15, 100 * _clamp(1 - sleep["restless_min"] / 60, 0, 1))])

    a = out["activity"]
    activity_score = None if not a else blend([
        (0.4, 100 * _clamp(a["steps"] / STEP_GOAL, 0, 1)),
        (0.35, 100 * _clamp(a["active_min"] / ACTIVE_GOAL_MIN, 0, 1)),
        (0.25, 100 * _clamp(1 - a["longest_still_min"] / 180, 0, 1))])

    hr = out["hr"]
    readiness = blend([
        (0.35, sleep_score),
        (0.25, None if out["hrv_avg"] is None else 100 * _clamp(out["hrv_avg"] / 60, 0, 1)),
        (0.2, None if not hr or not hr["resting"] else 100 * _clamp((85 - hr["resting"]) / 35, 0, 1)),
        (0.2, None if out["temp_dev"] is None else 100 * _clamp(1 - abs(out["temp_dev"]) / 1.5, 0, 1))])
    return {"readiness": readiness, "sleep": sleep_score, "activity": activity_score}
