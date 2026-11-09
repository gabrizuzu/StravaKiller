"""Sincronizzazione dalla Garmin Personal API e import di file FIT, in un thread separato."""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta

from garmin_personal_api import GarminPersonalApiClient, PersonalApiAuthenticator
from garmin_personal_api.errors import GarminPersonalApiError, HttpError

from . import config, db, fit_files

PAGE_SIZE = 45
WINDOW_DAYS = 28            # finestra accettata da tutti gli endpoint giornalieri
HEALTH_BACKFILL_DAYS = 730
MAX_FIT_PER_SYNC = 400      # il resto arriva alla sincronizzazione successiva
PAUSE_S = 0.3
_FASTEST_FIELDS = {1000: "fastestSplit_1000", 1609: "fastestSplit_1609", 5000: "fastestSplit_5000",
                   10000: "fastestSplit_10000", 21097: "fastestSplit_21098", 42195: "fastestSplit_42195"}

_lock = threading.Lock()
status: dict = {"running": False, "kind": None, "message": "", "done": 0, "total": 0,
                "warnings": [], "finished_at": None, "ok": None}


class RateLimited(Exception):
    pass


def _update(**values) -> None:
    status.update(values)


def _run_job(kind: str, target, *args) -> bool:
    if not _lock.acquire(blocking=False):
        return False
    _update(running=True, kind=kind, message="Avvio…", done=0, total=0, warnings=[], ok=None, finished_at=None)

    def worker():
        try:
            message = target(*args)
            _update(ok=True, message=message)
        except RateLimited:
            _update(ok=False, message="Garmin ha limitato le richieste (429). I dati scaricati sono salvati: riprova più tardi.")
        except HttpError as exc:
            hint = "Chiave non valida o scaduta: creane una nuova su Garmin Connect." if exc.status_code in (401, 403) else ""
            _update(ok=False, message=f"Errore Garmin HTTP {exc.status_code}. {hint}".strip())
        except GarminPersonalApiError as exc:
            _update(ok=False, message=f"Errore Garmin: {type(exc).__name__}. Controlla la chiave nelle impostazioni.")
        except Exception as exc:  # il thread non deve morire in silenzio
            _update(ok=False, message=f"Errore imprevisto: {type(exc).__name__}: {exc}")
        finally:
            _update(running=False, finished_at=datetime.now().isoformat(timespec="seconds"))
            _lock.release()

    threading.Thread(target=worker, daemon=True).start()
    return True


def start_sync() -> bool:
    return _run_job("sync", _sync)


def start_import(blobs: list[tuple[str, bytes]]) -> bool:
    return _run_job("import", _import, blobs)


# ---- normalizzazione --------------------------------------------------------------------------

def _is_date(value) -> bool:
    return isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-"


def iter_days(payload):
    """Trova gli oggetti giornalieri in una risposta di forma non documentata."""
    if isinstance(payload, list):
        for item in payload:
            yield from iter_days(item)
    elif isinstance(payload, dict):
        for key in ("calendarDate", "summaryDate", "date", "calendarDay"):
            if _is_date(payload.get(key)):
                yield payload[key][:10], payload
                return
        for value in payload.values():
            if isinstance(value, (list, dict)):
                yield from iter_days(value)


def deep_find(obj, names):
    """Primo valore numerico con una delle chiavi date, in ampiezza e senza badare al maiuscolo."""
    wanted = {n.lower() for n in names}
    queue = deque([obj])
    while queue:
        node = queue.popleft()
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in wanted and isinstance(value, (int, float)) and not isinstance(value, bool):
                    return value
            queue.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            queue.extend(v for v in node if isinstance(v, (dict, list)))
    return None


def _weight_kg(item):
    value = deep_find(item, ["weight", "latestWeight"])
    if value is None:
        return None
    return round(value / 1000, 2) if value > 1000 else value


def _sleep_score(item):
    direct = deep_find(item, ["overallSleepScore", "sleepScore"])
    return direct if direct is not None else deep_find(item.get("sleepScores"), ["value"])


def _body_battery(item):
    high, low = deep_find(item, ["bodyBatteryHighestValue"]), deep_find(item, ["bodyBatteryLowestValue"])
    values = item.get("bodyBatteryValuesArray")
    if high is None and isinstance(values, list):
        levels = [v[1] for v in values if isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float))]
        if levels:
            high, low = max(levels), min(levels)
    return {"bb_high": high, "bb_low": low}


HEALTH_ENDPOINTS = {
    "get_daily_steps": ("steps", lambda d: {"steps": deep_find(d, ["totalSteps", "steps"])}),
    "get_daily_stress": ("stress", lambda d: {"stress": deep_find(d, ["overallStressLevel", "averageStressLevel", "avgStress"])}),
    "get_daily_summaries": ("summary", lambda d: {
        "resting_hr": deep_find(d, ["restingHeartRate", "restingHR"]),
        "calories": deep_find(d, ["totalKilocalories", "totalCalories"]),
        "spo2": deep_find(d, ["averageSpo2", "avgSpo2"]),
        "bb_high": deep_find(d, ["bodyBatteryHighestValue"]),
        "bb_low": deep_find(d, ["bodyBatteryLowestValue"]),
    }),
    "get_daily_sleep_summaries": ("sleep", lambda d: {
        "sleep_s": deep_find(d, ["sleepTimeSeconds", "totalSleepSeconds", "sleepTimeInSeconds"]),
        "sleep_score": _sleep_score(d),
    }),
    "get_daily_hrv_summaries": ("hrv", lambda d: {"hrv": deep_find(d, ["lastNightAvg", "avgOvernightHrv", "hrvValue"])}),
    "get_daily_body_battery": ("body_battery", _body_battery),
    "get_weight_measurements": ("weight", lambda d: {"weight_kg": _weight_kg(d)}),
}


def activity_row(a: dict) -> dict:
    kind = a.get("activityType")
    return {
        "id": str(a["activityId"]), "source": "garmin", "start_local": a.get("startTimeLocal") or "",
        "type": (kind or {}).get("typeKey", "other") if isinstance(kind, dict) else "other",
        "name": a.get("activityName"), "distance_m": a.get("distance"), "duration_s": a.get("duration"),
        "moving_s": a.get("movingDuration"), "avg_hr": a.get("averageHR"), "max_hr": a.get("maxHR"),
        "cadence": a.get("averageRunningCadenceInStepsPerMinute"), "elev_gain": a.get("elevationGain"),
        "calories": a.get("calories"), "vo2max": a.get("vO2MaxValue"), "garmin_load": a.get("activityTrainingLoad"),
        "te_aerobic": a.get("aerobicTrainingEffect"), "raw": json.dumps(a),
    }


def upsert_activity(conn, row: dict) -> None:
    cols = list(row)
    conn.execute(
        f"INSERT INTO activities({','.join(cols)}) VALUES({','.join('?' for _ in cols)}) "
        f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{c}=excluded.{c}' for c in cols if c != 'id')}",
        tuple(row.values()))


def _store_fastest_splits(conn, activity_id: str, raw: dict) -> None:
    """Usa i migliori split di Garmin, se presenti, finché non arriva il file FIT."""
    for distance, field in _FASTEST_FIELDS.items():
        seconds = raw.get(field)
        if isinstance(seconds, (int, float)) and seconds > 0:
            conn.execute("INSERT INTO best_efforts(activity_id,distance_m,time_s,origin) VALUES(?,?,?,'garmin') "
                         "ON CONFLICT(activity_id,distance_m) DO NOTHING", (activity_id, distance, seconds))


# ---- job ----------------------------------------------------------------------------------------

def _call(func, *args, **kwargs):
    try:
        result = func(*args, **kwargs)
    except HttpError as exc:
        if exc.status_code == 429:
            raise RateLimited from exc
        raise
    time.sleep(PAUSE_S)
    return result


def _sync_activities(client) -> int:
    start, seen = 0, 0
    with db.connect() as c:
        known = {r["id"] for r in c.execute("SELECT id FROM activities")}
    while True:
        page = _call(client.search_activities, start=start, limit=PAGE_SIZE)
        if not page:
            break
        ids = {str(a.get("activityId")) for a in page}
        with db.connect() as c:
            for item in page:
                if item.get("activityId") is None:
                    continue
                row = activity_row(item)
                upsert_activity(c, row)
                _store_fastest_splits(c, row["id"], item)
        seen += len(page)
        _update(message=f"Attività: {seen} lette…", done=seen, total=0)
        if len(page) < PAGE_SIZE or ids <= known:   # pagina già nota: lo storico è completo
            break
        start += len(page)
    return seen


def _sync_health(client) -> None:
    today = date.today()
    with db.connect() as c:
        last = c.execute("SELECT MAX(date) AS d FROM daily").fetchone()["d"]
    first = date.fromisoformat(last) - timedelta(days=3) if last else today - timedelta(days=HEALTH_BACKFILL_DAYS)
    windows = []
    cursor = first
    while cursor <= today:
        end = min(cursor + timedelta(days=WINDOW_DAYS - 1), today)
        windows.append((cursor, end))
        cursor = end + timedelta(days=1)
    total = len(windows) * len(HEALTH_ENDPOINTS)
    done = 0
    failed: set[str] = set()
    for method, (kind, extract) in HEALTH_ENDPOINTS.items():
        for begin, end in windows:
            done += 1
            _update(message=f"Salute: {kind} {begin.isoformat()}", done=done, total=total)
            if method in failed:
                continue
            try:
                payload = _call(getattr(client, method), begin.isoformat(), end.isoformat())
            except HttpError as exc:
                failed.add(method)
                status["warnings"].append(f"{kind}: HTTP {exc.status_code}, serie saltata")
                continue
            with db.connect() as c:
                for day, item in iter_days(payload):
                    c.execute("INSERT INTO daily_raw(date,kind,json) VALUES(?,?,?) "
                              "ON CONFLICT(date,kind) DO UPDATE SET json=excluded.json",
                              (day, kind, json.dumps(item)))
                    db.upsert_daily(c, day, extract(item))


def _sync_fit(client) -> tuple[int, int]:
    with db.connect() as c:
        pending = [r["id"] for r in c.execute(
            "SELECT id FROM activities WHERE fit_done=0 AND source='garmin' AND type LIKE '%run%' "
            "ORDER BY start_local DESC")]
    batch = pending[:MAX_FIT_PER_SYNC]
    for index, activity_id in enumerate(batch, 1):
        _update(message=f"Analisi corse (file FIT) {index}/{len(batch)}", done=index, total=len(batch))
        try:
            data = _call(client.download_activity, activity_id)
            records: list[dict] = []
            for blob in fit_files.fit_blobs(data, f"{activity_id}.zip"):
                _, records = fit_files.parse_fit(blob)
                break
        except HttpError as exc:
            status["warnings"].append(f"FIT {activity_id}: HTTP {exc.status_code}")
            continue
        except (fitdecode_errors()) as exc:
            status["warnings"].append(f"FIT {activity_id}: {type(exc).__name__}")
            continue
        with db.connect() as c:
            fit_files.store_derived(c, activity_id, records)
    return len(batch), max(0, len(pending) - len(batch))


def fitdecode_errors():
    from fitdecode.exceptions import FitError
    return (FitError, EOFError, ValueError)


def _sync() -> str:
    key = config.get_secret("garmin_api_key")
    if not key:
        raise GarminPersonalApiError("missing key")
    with PersonalApiAuthenticator(bearer_token_cache_file=config.GARMIN_TOKEN_CACHE) as authenticator:
        token = authenticator.obtain_token(key)
    with GarminPersonalApiClient(token.access_token) as client:
        try:
            profile = _call(client.get_user_profile_settings)
            max_hr = deep_find(profile, ["maxHeartRateUsed", "maxHeartRate"])
            if max_hr:
                db.set_setting("garmin_max_hr", max_hr)
        except HttpError as exc:
            status["warnings"].append(f"profilo: HTTP {exc.status_code}")
        activities = _sync_activities(client)
        _sync_health(client)
        analysed, remaining = _sync_fit(client)
    db.set_setting("last_sync", datetime.now().isoformat(timespec="seconds"))
    tail = f" Restano {remaining} corse da analizzare: risincronizza." if remaining else ""
    return f"Sincronizzazione completata: {activities} attività lette, {analysed} corse analizzate.{tail}"


def _import(blobs: list[tuple[str, bytes]]) -> str:
    fits = [b for name, data in blobs for b in fit_files.fit_blobs(data, name)]
    added = skipped = 0
    for index, blob in enumerate(fits, 1):
        _update(message=f"Import FIT {index}/{len(fits)}", done=index, total=len(fits))
        try:
            session, records = fit_files.parse_fit(blob)
        except fitdecode_errors():
            skipped += 1
            continue
        if not session.get("start_local") or not session.get("distance_m"):
            skipped += 1      # file di monitoraggio, impostazioni, ecc.
            continue
        kind = session["sport"] if not session["sub_sport"] or session["sub_sport"] == "generic" \
            else f"{session['sub_sport']}_{session['sport']}"
        start = session["start_local"]
        distance = float(session["distance_m"])
        with db.connect() as c:
            duplicate = c.execute(
                "SELECT id, fit_done FROM activities WHERE substr(start_local,1,10)=? AND ABS(distance_m-?)<=?",
                (start[:10], distance, max(50.0, distance * 0.01))).fetchone()
            if duplicate and duplicate["fit_done"]:
                skipped += 1
                continue
            activity_id = duplicate["id"] if duplicate else f"fit-{start.replace(' ', 'T')}"
            if not duplicate:
                cadence = session.get("cadence")
                upsert_activity(c, {
                    "id": activity_id, "source": "fit", "start_local": start, "type": kind, "name": None,
                    "distance_m": distance, "duration_s": session.get("duration_s"), "moving_s": session.get("moving_s"),
                    "avg_hr": session.get("avg_hr"), "max_hr": session.get("max_hr"),
                    "cadence": cadence * 2 if cadence and "run" in kind else cadence,
                    "elev_gain": session.get("elev_gain"), "calories": session.get("calories"),
                })
            fit_files.store_derived(c, activity_id, records)
        added += 1
    return f"Import completato: {added} attività aggiunte o completate, {skipped} file ignorati."
