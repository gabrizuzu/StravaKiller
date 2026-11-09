"""Metriche di corsa. Tutte le funzioni leggono da SQLite e restituiscono dati serializzabili."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median

from . import db

RUN = "type LIKE '%run%'"
ZONES = [("Z1", 0, .60), ("Z2", .60, .70), ("Z3", .70, .80), ("Z4", .80, .90), ("Z5", .90, 9)]
DISTANCE_LABELS = {400: "400 m", 1000: "1 km", 1609: "1 miglio", 3000: "3 km", 5000: "5 km",
                   10000: "10 km", 15000: "15 km", 21097: "Mezza", 42195: "Maratona"}
PREDICTION_DISTANCES = [1609, 3000, 5000, 10000, 21097, 42195]


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _runs(where: str = "", params: tuple = ()) -> list[dict]:
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            f"SELECT id,start_local,type,name,distance_m,duration_s,moving_s,avg_hr,max_hr,cadence,elev_gain,"
            f"vo2max,hr_hist FROM activities WHERE {RUN} {where} ORDER BY start_local", params)]


def _seconds(run: dict) -> float:
    return run.get("moving_s") or run.get("duration_s") or 0


def run_date(run: dict) -> date:
    return date.fromisoformat(run["start_local"][:10])


def hr_limits() -> tuple[float, float]:
    """(FC massima, FC a riposo): impostazioni, poi Garmin, poi valori osservati."""
    with db.connect() as c:
        peaks = [r[0] for r in c.execute(
            f"SELECT max_hr FROM activities WHERE {RUN} AND max_hr BETWEEN 120 AND 225 ORDER BY max_hr DESC LIMIT 2")]
        since = (date.today() - timedelta(days=30)).isoformat()
        rest = [r[0] for r in c.execute("SELECT resting_hr FROM daily WHERE resting_hr IS NOT NULL AND date>=?", (since,))]
    max_hr = db.get_setting("max_hr") or db.get_setting("garmin_max_hr") or (peaks[-1] if peaks else 190)
    resting = db.get_setting("resting_hr") or (median(rest) if rest else 55)
    return float(max_hr), float(resting)


def trimp(seconds: float, avg_hr: float | None, max_hr: float, rest_hr: float) -> float:
    """Carico stile Banister. Senza FC si assume uno sforzo medio-facile."""
    minutes = seconds / 60
    reserve = 0.6 if not avg_hr else max(0.0, min(1.0, (avg_hr - rest_hr) / max(1.0, max_hr - rest_hr)))
    return minutes * reserve * 0.64 * math.exp(1.92 * reserve)


def weekly(weeks: int = 26) -> list[dict]:
    this = monday(date.today())
    first = this - timedelta(weeks=weeks - 1)
    buckets = {first + timedelta(weeks=i): {"km": 0.0, "hours": 0.0, "runs": 0, "elev": 0.0} for i in range(weeks)}
    for run in _runs("AND start_local>=?", (first.isoformat(),)):
        b = buckets.get(monday(run_date(run)))
        if b is not None:
            b["km"] += (run["distance_m"] or 0) / 1000
            b["hours"] += _seconds(run) / 3600
            b["runs"] += 1
            b["elev"] += run["elev_gain"] or 0
    return [{"week": k.isoformat(), "km": round(v["km"], 1), "hours": round(v["hours"], 2),
             "runs": v["runs"], "elev": round(v["elev"])} for k, v in buckets.items()]


def monthly(months: int = 24) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"km": 0.0, "runs": 0, "seconds": 0.0})
    for run in _runs():
        b = buckets[run["start_local"][:7]]
        b["km"] += (run["distance_m"] or 0) / 1000
        b["runs"] += 1
        b["seconds"] += _seconds(run)
    out = [{"month": k, "km": round(v["km"], 1), "runs": v["runs"],
            "avg_pace": round(v["seconds"] / v["km"], 1) if v["km"] else None}
           for k, v in sorted(buckets.items())]
    return out[-months:]


def load_series(days: int = 180) -> list[dict]:
    """CTL (fitness, 42 g), ATL (fatica, 7 g), TSB (forma = CTL - ATL del giorno prima)."""
    runs = _runs()
    if not runs:
        return []
    max_hr, rest_hr = hr_limits()
    per_day: dict[date, float] = defaultdict(float)
    for run in runs:
        per_day[run_date(run)] += trimp(_seconds(run), run["avg_hr"], max_hr, rest_hr)
    k_ctl, k_atl = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)
    ctl = atl = 0.0
    out = []
    current = min(per_day)
    today = date.today()
    while current <= today:
        tsb = ctl - atl
        load = per_day.get(current, 0.0)
        ctl += (load - ctl) * k_ctl
        atl += (load - atl) * k_atl
        out.append({"date": current.isoformat(), "load": round(load, 1), "ctl": round(ctl, 1),
                    "atl": round(atl, 1), "tsb": round(tsb, 1)})
        current += timedelta(days=1)
    return out[-days:]


def efficiency(weeks: int = 52) -> list[dict]:
    """Metri al minuto per battito nelle corse in zona aerobica: se sale, a pari FC vai più forte."""
    max_hr, _ = hr_limits()
    since = (date.today() - timedelta(weeks=weeks)).isoformat()
    buckets: dict[date, list[float]] = defaultdict(list)
    for run in _runs("AND start_local>=? AND avg_hr IS NOT NULL AND distance_m>=3000", (since,)):
        if run["avg_hr"] <= max_hr * .82 and _seconds(run):
            buckets[monday(run_date(run))].append((run["distance_m"] / (_seconds(run) / 60)) / run["avg_hr"])
    return [{"week": k.isoformat(), "ef": round(mean(v), 3)} for k, v in sorted(buckets.items())]


def _zone_index(hr: float, max_hr: float) -> int:
    ratio = hr / max_hr
    return next(i for i, (_, low, high) in enumerate(ZONES) if low <= ratio < high)


def _zone_seconds(run: dict, max_hr: float) -> list[float]:
    seconds = [0.0] * len(ZONES)
    hist = json.loads(run["hr_hist"]) if run.get("hr_hist") else None
    if hist:
        for bpm, secs in hist.items():
            seconds[_zone_index(int(bpm) + 2.5, max_hr)] += secs
    elif run.get("avg_hr"):
        seconds[_zone_index(run["avg_hr"], max_hr)] += _seconds(run)
    return seconds


def zones(weeks: int = 26) -> dict:
    max_hr, _ = hr_limits()
    first = monday(date.today()) - timedelta(weeks=weeks - 1)
    buckets = {first + timedelta(weeks=i): [0.0] * len(ZONES) for i in range(weeks)}
    for run in _runs("AND start_local>=?", (first.isoformat(),)):
        target = buckets.get(monday(run_date(run)))
        if target is not None:
            for i, secs in enumerate(_zone_seconds(run, max_hr)):
                target[i] += secs
    return {"zones": [z[0] for z in ZONES], "max_hr": max_hr,
            "weeks": [{"week": k.isoformat(), "minutes": [round(s / 60, 1) for s in v]} for k, v in buckets.items()]}


# ---- VDOT (formule di Daniels-Gilbert) ----------------------------------------------------------

def vdot(distance_m: float, seconds: float) -> float:
    minutes = seconds / 60
    velocity = distance_m / minutes
    vo2 = -4.6 + 0.182258 * velocity + 0.000104 * velocity ** 2
    fraction = 0.8 + 0.1894393 * math.exp(-0.012778 * minutes) + 0.2989558 * math.exp(-0.1932605 * minutes)
    return vo2 / fraction


def predict_seconds(value: float, distance_m: float) -> float:
    low, high = distance_m / 500 * 60, distance_m / 40 * 60      # tra 500 e 40 m/min
    for _ in range(70):
        mid = (low + high) / 2
        if vdot(distance_m, mid) > value:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _pace_at(value: float, pct: float) -> float:
    target = value * pct
    velocity = (-0.182258 + math.sqrt(0.182258 ** 2 + 4 * 0.000104 * (target + 4.6))) / (2 * 0.000104)
    return 60000 / velocity


def predictions(window_days: int = 120) -> dict | None:
    since = (date.today() - timedelta(days=window_days)).isoformat()
    with db.connect() as c:
        rows = c.execute(
            f"SELECT e.distance_m, e.time_s, a.start_local FROM best_efforts e JOIN activities a ON a.id=e.activity_id "
            f"WHERE e.distance_m>=5000 AND a.start_local>=? AND a.{RUN}", (since,)).fetchall()
    if not rows:
        return None
    best = max(rows, key=lambda r: vdot(r["distance_m"], r["time_s"]))
    value = vdot(best["distance_m"], best["time_s"])
    return {
        "vdot": round(value, 1),
        "based_on": {"distance": DISTANCE_LABELS.get(best["distance_m"], str(best["distance_m"])),
                     "time_s": best["time_s"], "date": best["start_local"][:10]},
        "races": [{"distance_m": d, "label": DISTANCE_LABELS[d], "time_s": round(predict_seconds(value, d))}
                  for d in PREDICTION_DISTANCES],
        "paces": [
            {"name": "Facile", "from_s": round(_pace_at(value, .65)), "to_s": round(_pace_at(value, .78))},
            {"name": "Maratona", "from_s": round(_pace_at(value, .84)), "to_s": round(_pace_at(value, .80))},
            {"name": "Soglia", "from_s": round(_pace_at(value, .90)), "to_s": round(_pace_at(value, .86))},
            {"name": "Intervalli", "from_s": round(_pace_at(value, 1.0)), "to_s": round(_pace_at(value, .95))},
            {"name": "Ripetute brevi", "from_s": round(_pace_at(value, 1.10)), "to_s": round(_pace_at(value, 1.05))},
        ],
    }


def fitness_history(months: int = 24) -> list[dict]:
    with db.connect() as c:
        efforts = c.execute(
            f"SELECT e.distance_m, e.time_s, a.start_local FROM best_efforts e JOIN activities a ON a.id=e.activity_id "
            f"WHERE e.distance_m>=5000 AND a.{RUN}").fetchall()
    best: dict[str, float] = {}
    for e in efforts:
        month = e["start_local"][:7]
        best[month] = max(best.get(month, 0), vdot(e["distance_m"], e["time_s"]))
    garmin: dict[str, list[float]] = defaultdict(list)
    for run in _runs("AND vo2max IS NOT NULL"):
        garmin[run["start_local"][:7]].append(run["vo2max"])
    months_all = sorted(set(best) | set(garmin))[-months:]
    return [{"month": m, "vdot": round(best[m], 1) if m in best else None,
             "garmin_vo2": round(mean(garmin[m]), 1) if garmin.get(m) else None} for m in months_all]


# ---- classifica --------------------------------------------------------------------------------

def leaderboard(distance_m: int = 5000, limit: int = 20, since: str | None = None) -> list[dict]:
    clause, params = ("AND a.start_local>=?", (since,)) if since else ("", ())
    with db.connect() as c:
        rows = c.execute(
            f"SELECT e.time_s, e.origin, a.id, a.name, a.start_local, a.avg_hr FROM best_efforts e "
            f"JOIN activities a ON a.id=e.activity_id WHERE e.distance_m=? AND a.{RUN} {clause} "
            f"ORDER BY e.time_s LIMIT ?", (distance_m, *params, limit)).fetchall()
    return [{"rank": i, "time_s": r["time_s"], "pace_s": round(r["time_s"] / distance_m * 1000, 1),
             "activity_id": r["id"], "name": r["name"], "date": r["start_local"][:10], "origin": r["origin"]}
            for i, r in enumerate(rows, 1)]


def pr_progression(distance_m: int) -> list[dict]:
    with db.connect() as c:
        rows = c.execute(
            f"SELECT e.time_s, a.start_local, a.id FROM best_efforts e JOIN activities a ON a.id=e.activity_id "
            f"WHERE e.distance_m=? AND a.{RUN} ORDER BY a.start_local", (distance_m,)).fetchall()
    best, out = None, []
    for r in rows:
        if best is None or r["time_s"] < best:
            best = r["time_s"]
            out.append({"date": r["start_local"][:10], "time_s": r["time_s"], "activity_id": r["id"]})
    return out


def records() -> dict:
    personal = []
    recent_since = (date.today() - timedelta(days=90)).isoformat()
    for distance, label in DISTANCE_LABELS.items():
        top = leaderboard(distance, 1)
        if top:
            recent = leaderboard(distance, 1, recent_since)
            personal.append({"distance_m": distance, "label": label, **top[0],
                             "recent_time_s": recent[0]["time_s"] if recent else None})
    runs = _runs()
    weeks = defaultdict(float)
    for run in runs:
        weeks[monday(run_date(run))] += (run["distance_m"] or 0) / 1000
    longest = max(runs, key=lambda r: r["distance_m"] or 0, default=None)
    biggest = max(weeks.items(), key=lambda kv: kv[1], default=None)
    return {"personal": personal, "total_runs": len(runs),
            "longest": {"km": round(longest["distance_m"] / 1000, 2), "date": longest["start_local"][:10]} if longest else None,
            "biggest_week": {"km": round(biggest[1], 1), "week": biggest[0].isoformat()} if biggest else None}


# ---- salute e riepiloghi -----------------------------------------------------------------------

def health(days: int = 90) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    with db.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM daily WHERE date>=? ORDER BY date", (since,))]
    for row in rows:
        row["sleep_h"] = round(row.pop("sleep_s") / 3600, 2) if row.get("sleep_s") else None
    return rows


def summary() -> dict:
    today = date.today()
    week = weekly(9)
    previous = [w["km"] for w in week[:-1]][-4:]
    year_prefix = str(today.year)
    runs = _runs()
    year_runs = [r for r in runs if r["start_local"].startswith(year_prefix)]
    load = load_series(2)
    pred = predictions()
    return {
        "today": today.isoformat(),
        "week_km": week[-1]["km"] if week else 0,
        "week_runs": week[-1]["runs"] if week else 0,
        "avg_4w_km": round(mean(previous), 1) if previous else 0,
        "year_km": round(sum((r["distance_m"] or 0) for r in year_runs) / 1000, 1),
        "year_runs": len(year_runs),
        "total_runs": len(runs),
        "form": load[-1] if load else None,
        "vdot": pred["vdot"] if pred else None,
        "last_activity": runs[-1]["start_local"][:10] if runs else None,
    }


def recommendations() -> list[dict]:
    """Regole trasparenti: ogni consiglio mostra il numero che lo ha generato."""
    runs = _runs()
    if not runs:
        return []
    out: list[dict] = []
    add = lambda level, title, detail, action: out.append(
        {"level": level, "title": title, "detail": detail, "action": action})
    today = date.today()
    week = weekly(6)
    done_weeks = [w["km"] for w in week[:-1]]
    chronic = mean(done_weeks[-4:]) if done_weeks else 0
    last_full = done_weeks[-1] if done_weeks else 0
    load = load_series(1)

    if chronic >= 5 and last_full > chronic * 1.3:
        add("warn", "Volume in crescita rapida",
            f"La scorsa settimana hai corso {last_full:.0f} km contro una media di {chronic:.0f} km.",
            "Questa settimana stabilizza il volume o riducilo del 10-20%, poi riprendi con aumenti graduali.")
    if load:
        ctl, atl = load[-1]["ctl"], load[-1]["atl"]
        ratio = atl / ctl if ctl else 0
        if ctl > 10 and ratio > 1.4:
            add("warn", "Fatica alta rispetto alla forma",
                f"Rapporto carico acuto/cronico {ratio:.2f} (fatica {atl:.0f}, fitness {ctl:.0f}).",
                "Inserisci 1-2 giorni leggeri o di riposo prima della prossima seduta intensa.")
        elif ctl > 10 and ratio < 0.7:
            add("info", "Carico in calo",
                f"Rapporto carico acuto/cronico {ratio:.2f}: stai perdendo stimolo, salvo tapering voluto.",
                "Se non stai scaricando per una gara, riprendi con corse facili e aumenta gradualmente.")

    max_hr, _ = hr_limits()
    recent = [r for r in runs if (today - run_date(r)).days <= 28]
    totals = [0.0] * len(ZONES)
    for run in recent:
        for i, secs in enumerate(_zone_seconds(run, max_hr)):
            totals[i] += secs
    all_secs = sum(totals)
    if all_secs > 3600:
        easy = sum(totals[:3]) / all_secs
        if easy < .75:
            add("warn", "Troppa intensità nelle corse",
                f"Solo il {easy:.0%} del tempo delle ultime 4 settimane è sotto l'80% della FC massima.",
                "Porta le corse facili a un ritmo in cui riesci a parlare: l'obiettivo è circa l'80% del tempo.")
        else:
            add("good", "Buon equilibrio di intensità",
                f"Il {easy:.0%} del tempo è stato sotto l'80% della FC massima.",
                "Mantieni questa distribuzione e concentra la qualità in 1-2 sedute a settimana.")
        last_14 = [r for r in runs if (today - run_date(r)).days <= 14]
        hard = sum(sum(_zone_seconds(r, max_hr)[3:]) for r in last_14)
        if len(last_14) >= 3 and hard < 600:
            add("info", "Manca uno stimolo di qualità",
                f"Nelle ultime 2 settimane solo {hard / 60:.0f} minuti sopra l'80% della FC massima.",
                "Prova una seduta a settimana, ad esempio 4-5 x 4 min al ritmo intervalli con 3 min di recupero.")

    if len(recent) and len(recent) / 4 < 3:
        add("info", "Frequenza bassa",
            f"Media di {len(recent) / 4:.1f} corse a settimana nelle ultime 4 settimane.",
            "Una corsa breve e facile in più spesso rende più di un'uscita lunga aggiuntiva.")
    gap = (today - run_date(runs[-1])).days
    if gap >= 10:
        add("info", "Pausa dalla corsa", f"Ultima corsa {gap} giorni fa.",
            "Riparti con 2-3 uscite facili e accorciate prima di tornare ai volumi di prima.")

    last_2w = [r for r in runs if (today - run_date(r)).days <= 14]
    if last_2w and chronic >= 15:
        longest = max((r["distance_m"] or 0) / 1000 for r in last_2w)
        if longest / chronic > .45:
            add("warn", "Lungo sproporzionato",
                f"La corsa più lunga ({longest:.1f} km) vale il {longest / chronic:.0%} del volume settimanale medio.",
                "Tieni il lungo intorno al 30-35% del volume, oppure distribuisci più km sugli altri giorni.")
        elif longest / chronic < .2:
            add("info", "Manca il lungo",
                f"Nelle ultime 2 settimane la corsa più lunga è stata di {longest:.1f} km.",
                f"Inserisci un lungo facile da circa {chronic * .3:.0f} km una volta a settimana.")

    cadences = [r["cadence"] for r in recent if r.get("cadence")]
    if cadences and mean(cadences) < 160:
        add("info", "Cadenza bassa",
            f"Cadenza media {mean(cadences):.0f} passi/min.",
            "Prova ad aumentarla del 3-5% nelle corse facili, senza forzare e senza allungare il passo.")

    trend = efficiency(12)
    if len(trend) >= 8:
        early, late = mean(t["ef"] for t in trend[:4]), mean(t["ef"] for t in trend[-4:])
        change = late / early - 1
        if change > .015:
            add("good", "Efficienza aerobica in crescita",
                f"A parità di battiti vai il {change:.1%} più veloce rispetto a 2-3 mesi fa.",
                "Il lavoro aerobico sta pagando: non cambiare ciò che funziona.")
        elif change < -.02:
            add("info", "Efficienza aerobica in calo",
                f"A parità di battiti vai il {abs(change):.1%} più piano rispetto a 2-3 mesi fa.",
                "Possibili cause: stanchezza, caldo o poco volume facile. Controlla sonno e recupero.")

    days = health(45)
    hrv = [d["hrv"] for d in days if d.get("hrv")]
    if len(hrv) >= 20:
        week_hrv, base = mean(hrv[-7:]), mean(hrv[:-7])
        if week_hrv < base * .9:
            add("warn", "HRV sotto la tua media",
                f"Media 7 giorni {week_hrv:.0f} ms contro {base:.0f} ms di base.",
                "Sposta le sedute intense finché l'HRV non rientra e cura il sonno.")
    resting = [d["resting_hr"] for d in days if d.get("resting_hr")]
    if len(resting) >= 20 and mean(resting[-5:]) > mean(resting[:-5]) + 4:
        add("warn", "FC a riposo in aumento",
            f"Ultimi 5 giorni {mean(resting[-5:]):.0f} bpm contro {mean(resting[:-5]):.0f} bpm di base.",
            "Può indicare fatica o un malanno in arrivo: alleggerisci e, se persiste, parlane con un medico.")
    sleep = [d["sleep_h"] for d in days[-14:] if d.get("sleep_h")]
    if len(sleep) >= 7 and mean(sleep) < 6.75:
        add("info", "Sonno sotto le 7 ore",
            f"Media {mean(sleep):.1f} ore nelle ultime 2 settimane.",
            "Il sonno è il primo strumento di recupero: anticipa di 30 minuti, soprattutto dopo le sedute dure.")

    goal = db.get_setting("goal")
    pred = predictions()
    if goal and pred:
        try:
            race_day = date.fromisoformat(goal["date"])
            weeks_left = (race_day - today).days / 7
            predicted = predict_seconds(pred["vdot"], goal["distance_m"])
            gap_s = predicted - goal["time_s"]
        except (KeyError, TypeError, ValueError):
            weeks_left = -1
        if weeks_left >= 0:
            verdict = f"sei a {gap_s / 60:.1f} min dall'obiettivo" if gap_s > 0 else f"sei avanti di {-gap_s / 60:.1f} min sull'obiettivo"
            add("good" if gap_s <= 0 else "info", f"Obiettivo gara tra {weeks_left:.0f} settimane",
                f"In base ai dati recenti {verdict}.",
                "Nelle ultime 2 settimane riduci il volume e mantieni un po' di ritmo gara."
                if weeks_left <= 2 else "Inserisci ogni settimana una seduta specifica al ritmo gara obiettivo.")

    priority = {"warn": 0, "info": 1, "good": 2}
    return sorted(out, key=lambda r: priority[r["level"]])


def list_activities(kind: str | None = None, query: str | None = None, days: int | None = None,
                    offset: int = 0, limit: int = 50) -> dict:
    clauses, params = ["1=1"], []
    if kind == "run":
        clauses.append(RUN)
    elif kind:
        clauses.append("type=?")
        params.append(kind)
    if query:
        clauses.append("(name LIKE ? OR type LIKE ?)")
        params += [f"%{query}%", f"%{query}%"]
    if days:
        clauses.append("start_local>=?")
        params.append((date.today() - timedelta(days=days)).isoformat())
    where = " AND ".join(clauses)
    with db.connect() as c:
        total = c.execute(f"SELECT COUNT(*) AS n FROM activities WHERE {where}", params).fetchone()["n"]
        rows = c.execute(
            f"SELECT id,start_local,type,name,distance_m,duration_s,moving_s,avg_hr,max_hr,cadence,elev_gain,calories,"
            f"vo2max,garmin_load,fit_done FROM activities WHERE {where} ORDER BY start_local DESC LIMIT ? OFFSET ?",
            (*params, limit, offset)).fetchall()
        types = [r["type"] for r in c.execute("SELECT DISTINCT type FROM activities ORDER BY type")]
    return {"total": total, "types": types, "items": [dict(r) for r in rows]}


def activity_detail(activity_id: str) -> dict | None:
    with db.connect() as c:
        row = c.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
        if not row:
            return None
        splits = c.execute("SELECT km,time_s,avg_hr,elev_gain FROM splits WHERE activity_id=? ORDER BY km", (activity_id,)).fetchall()
        efforts = c.execute("SELECT distance_m,time_s FROM best_efforts WHERE activity_id=? ORDER BY distance_m", (activity_id,)).fetchall()
    out = dict(row)
    out.pop("raw", None)
    hist = out.pop("hr_hist", None)
    out["stream"] = json.loads(out["stream"]) if out.get("stream") else []
    out["splits"] = [dict(s) for s in splits]
    out["efforts"] = [{**dict(e), "label": DISTANCE_LABELS.get(e["distance_m"], str(e["distance_m"]))} for e in efforts]
    if hist:
        max_hr, _ = hr_limits()
        minutes = _zone_seconds({"hr_hist": hist}, max_hr)
        out["zones"] = [{"zone": z[0], "minutes": round(m / 60, 1)} for z, m in zip(ZONES, minutes)]
    return out
