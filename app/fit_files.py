"""Lettura dei file FIT e calcolo di migliori sforzi, split, zone e tracciato."""
from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import fitdecode

EFFORT_DISTANCES = [400, 1000, 1609, 3000, 5000, 10000, 15000, 21097, 42195]
MAX_SPEED_MPS = 7.5        # oltre: quasi certamente uno spike GPS
PAUSE_GAP_S = 15           # buco tra campioni oltre il quale, se fermo, è una pausa
MAX_MEMBER_BYTES = 80_000_000


def fit_blobs(data: bytes, name: str = "", depth: int = 0):
    """Restituisce i FIT contenuti in `data`: file singolo, ZIP o ZIP annidati (export Garmin)."""
    if zipfile.is_zipfile(io.BytesIO(data)):
        if depth > 2:
            return
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.file_size > MAX_MEMBER_BYTES:
                    continue
                lowered = info.filename.lower()
                if lowered.endswith((".fit", ".zip")):
                    yield from fit_blobs(archive.read(info), info.filename, depth + 1)
    elif name.lower().endswith(".fit") or data[8:12] == b".FIT":
        yield data


def _value(frame, *names):
    for name in names:
        if frame.has_field(name):
            value = frame.get_value(name)
            if value is not None:
                return value
    return None


def parse_fit(data: bytes) -> tuple[dict, list[dict]]:
    """Ritorna (sessione, record). Sessione vuota se il file non contiene un'attività."""
    session: dict = {}
    records: list[dict] = []
    local_offset: timedelta | None = None
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as reader:
        for frame in reader:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "record":
                ts = _value(frame, "timestamp")
                if ts is None:
                    continue
                records.append({
                    "t": ts.timestamp(),
                    "d": _value(frame, "distance"),
                    "hr": _value(frame, "heart_rate"),
                    "alt": _value(frame, "enhanced_altitude", "altitude"),
                })
            elif frame.name == "session" and not session:
                session = {
                    "start": _value(frame, "start_time"),
                    "sport": str(_value(frame, "sport") or "other"),
                    "sub_sport": str(_value(frame, "sub_sport") or ""),
                    "distance_m": _value(frame, "total_distance"),
                    "duration_s": _value(frame, "total_elapsed_time"),
                    "moving_s": _value(frame, "total_timer_time"),
                    "avg_hr": _value(frame, "avg_heart_rate"),
                    "max_hr": _value(frame, "max_heart_rate"),
                    "elev_gain": _value(frame, "total_ascent"),
                    "calories": _value(frame, "total_calories"),
                    "cadence": _value(frame, "avg_running_cadence", "avg_cadence"),
                }
            elif frame.name == "activity" and local_offset is None:
                utc, local = _value(frame, "timestamp"), _value(frame, "local_timestamp")
                if isinstance(utc, datetime) and isinstance(local, datetime):
                    local_offset = local.replace(tzinfo=timezone.utc) - utc.astimezone(timezone.utc)
    if isinstance(session.get("start"), datetime):
        start = session["start"].astimezone(timezone.utc) + (local_offset or timedelta())
        session["start_local"] = start.strftime("%Y-%m-%d %H:%M:%S")
    return session, records


def _clean(records: list[dict]) -> list[dict]:
    """Tiene i campioni con distanza, forzando la distanza a non decrescere."""
    out: list[dict] = []
    last = 0.0
    for rec in sorted(records, key=lambda r: r["t"]):
        if rec.get("d") is None:
            continue
        last = max(last, float(rec["d"]))
        out.append({**rec, "d": last})
    return out


def _moving_time(recs: list[dict]) -> list[float]:
    cum = [0.0]
    for prev, cur in zip(recs, recs[1:]):
        dt = cur["t"] - prev["t"]
        paused = dt > PAUSE_GAP_S and (cur["d"] - prev["d"]) < dt * 0.5
        cum.append(cum[-1] + (0.0 if paused else dt))
    return cum


def best_efforts(recs: list[dict], moving: list[float]) -> dict[int, float]:
    """Tempo minimo su ogni distanza, con finestra scorrevole e inizio interpolato."""
    result: dict[int, float] = {}
    if len(recs) < 2:
        return result
    total = recs[-1]["d"] - recs[0]["d"]
    for distance in EFFORT_DISTANCES:
        if total < distance:
            continue
        best = None
        i = 0
        for j in range(1, len(recs)):
            while i + 1 < j and recs[j]["d"] - recs[i + 1]["d"] >= distance:
                i += 1
            span = recs[j]["d"] - recs[i]["d"]
            if span < distance:
                continue
            step = recs[i + 1]["d"] - recs[i]["d"]
            frac = (span - distance) / step if step > 0 else 0.0
            start = moving[i] + frac * (moving[i + 1] - moving[i])
            elapsed = moving[j] - start
            if elapsed > 0 and distance / elapsed <= MAX_SPEED_MPS and (best is None or elapsed < best):
                best = elapsed
        if best is not None:
            result[distance] = round(best, 1)
    return result


def km_splits(recs: list[dict], moving: list[float]) -> list[dict]:
    splits: list[dict] = []
    if len(recs) < 2:
        return splits
    km, start_t, hrs, gain = 1, 0.0, [], 0.0
    for idx in range(1, len(recs)):
        prev, cur = recs[idx - 1], recs[idx]
        if cur.get("hr"):
            hrs.append(cur["hr"])
        if prev.get("alt") is not None and cur.get("alt") is not None and cur["alt"] > prev["alt"]:
            gain += cur["alt"] - prev["alt"]
        while cur["d"] >= km * 1000:
            step = cur["d"] - prev["d"]
            frac = (km * 1000 - prev["d"]) / step if step > 0 else 1.0
            at = moving[idx - 1] + frac * (moving[idx] - moving[idx - 1])
            splits.append({"km": km, "time_s": round(at - start_t, 1),
                           "avg_hr": round(sum(hrs) / len(hrs)) if hrs else None,
                           "elev_gain": round(gain, 1)})
            km, start_t, hrs, gain = km + 1, at, [], 0.0
    return splits


def hr_histogram(records: list[dict]) -> dict[str, float]:
    """Secondi per fascia di 5 bpm: le zone si ricalcolano se cambia la FC massima."""
    hist: dict[str, float] = {}
    ordered = sorted(records, key=lambda r: r["t"])
    for prev, cur in zip(ordered, ordered[1:]):
        dt = cur["t"] - prev["t"]
        if cur.get("hr") and 0 < dt <= PAUSE_GAP_S:
            key = str(int(cur["hr"]) // 5 * 5)
            hist[key] = round(hist.get(key, 0.0) + dt, 1)
    return hist


def stream_points(recs: list[dict], moving: list[float], limit: int = 240) -> list[dict]:
    if len(recs) < 2:
        return []
    step = max(1, len(recs) // limit)
    points: list[dict] = []
    for start in range(0, len(recs) - step, step):
        end = start + step
        distance = recs[end]["d"] - recs[start]["d"]
        duration = moving[end] - moving[start]
        chunk = [r["hr"] for r in recs[start:end] if r.get("hr")]
        points.append({
            "km": round(recs[end]["d"] / 1000, 3),
            "pace": round(duration / distance * 1000, 1) if distance > 2 else None,
            "hr": round(sum(chunk) / len(chunk)) if chunk else None,
            "alt": round(recs[end]["alt"], 1) if recs[end].get("alt") is not None else None,
        })
    return points


def store_derived(conn: sqlite3.Connection, activity_id: str, records: list[dict]) -> None:
    """Salva quanto ricavato dai campioni. Sovrascrive i dati stimati dal riepilogo."""
    recs = _clean(records)
    moving = _moving_time(recs)
    conn.execute("UPDATE activities SET hr_hist=?, stream=?, fit_done=1 WHERE id=?",
                 (json.dumps(hr_histogram(records)), json.dumps(stream_points(recs, moving)), activity_id))
    conn.execute("DELETE FROM splits WHERE activity_id=?", (activity_id,))
    conn.executemany("INSERT INTO splits(activity_id,km,time_s,avg_hr,elev_gain) VALUES(?,?,?,?,?)",
                     [(activity_id, s["km"], s["time_s"], s["avg_hr"], s["elev_gain"])
                      for s in km_splits(recs, moving)])
    for distance, seconds in best_efforts(recs, moving).items():
        conn.execute("INSERT INTO best_efforts(activity_id,distance_m,time_s,origin) VALUES(?,?,?,'fit') "
                     "ON CONFLICT(activity_id,distance_m) DO UPDATE SET time_s=excluded.time_s, origin='fit'",
                     (activity_id, distance, seconds))
