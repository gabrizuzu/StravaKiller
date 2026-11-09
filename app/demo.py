"""Dati inventati per provare l'app senza chiave. Finiscono solo in demo.db."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from . import db, fit_files, sync


def _records(start: float, km: float, pace: float, hr: float, rng: random.Random) -> list[dict]:
    records, t, d, alt, current_hr = [], start, 0.0, 120.0, hr - 25
    while d < km * 1000:
        phase_pace = pace * (1 + rng.uniform(-.03, .03))
        step = 10 / phase_pace * 1000
        d += step
        t += 10
        alt += rng.uniform(-.8, .8)
        current_hr += (hr - current_hr) * .08 + rng.uniform(-.7, .7)
        records.append({"t": t, "d": d, "hr": round(current_hr), "alt": alt})
    return records


def seed_if_empty() -> None:
    with db.connect() as c:
        if c.execute("SELECT COUNT(*) n FROM activities").fetchone()["n"]:
            return
    rng = random.Random(42)
    today = date.today()
    weeks = 60
    db.set_setting("garmin_max_hr", 188)
    with db.connect() as c:
        for w in range(weeks, -1, -1):
            progress = 1 - w / weeks
            monday = today - timedelta(days=today.weekday()) - timedelta(weeks=w)
            easy_pace = 368 - 30 * progress + rng.uniform(-4, 4)
            sessions = [(1, "Corsa facile", 7 + 4 * progress, easy_pace, 136), (3, "Ripetute", 8 + 3 * progress, easy_pace * .79, 166),
                        (5, "Corsa facile", 6 + 4 * progress, easy_pace * 1.02, 134), (6, "Lungo", 13 + 11 * progress, easy_pace * 1.04, 141)]
            if w % 4 == 3:
                sessions = [(d, n, k * .7, p, h) for d, n, k, p, h in sessions]
            if w == 22:
                sessions = []                          # settimana saltata, come nella realtà
            for offset, name, km, pace, hr in sessions:
                day = monday + timedelta(days=offset)
                if day > today:
                    continue
                km *= rng.uniform(.93, 1.08)
                when = datetime.combine(day, datetime.min.time()) + timedelta(hours=7, minutes=rng.randint(0, 50))
                recs = _records(when.timestamp(), km, pace, hr + rng.uniform(-3, 3), rng)
                activity_id = f"demo-{when:%Y%m%d%H%M}"
                hrs = [r["hr"] for r in recs]
                duration = recs[-1]["t"] - when.timestamp()
                sync.upsert_activity(c, {
                    "id": activity_id, "source": "demo", "start_local": when.strftime("%Y-%m-%d %H:%M:%S"),
                    "type": "running", "name": name, "distance_m": recs[-1]["d"], "duration_s": duration, "moving_s": duration,
                    "avg_hr": round(sum(hrs) / len(hrs)), "max_hr": max(hrs) + 6, "cadence": 166 + 6 * progress + rng.uniform(-2, 2),
                    "elev_gain": rng.uniform(30, 180), "calories": km * 68, "vo2max": round(44 + 6 * progress + rng.uniform(-.4, .4)),
                })
                fit_files.store_derived(c, activity_id, recs)
            for offset in (2,):                        # un'attività non di corsa per i filtri
                day = monday + timedelta(days=offset)
                if day <= today and w % 2 == 0:
                    when = datetime.combine(day, datetime.min.time()) + timedelta(hours=18)
                    sync.upsert_activity(c, {"id": f"demo-c-{when:%Y%m%d}", "source": "demo", "start_local": when.strftime("%Y-%m-%d %H:%M:%S"),
                                             "type": "strength_training", "name": "Forza", "duration_s": 2400, "moving_s": 2400, "avg_hr": 112, "calories": 240})
        for back in range(420, -1, -1):
            day = today - timedelta(days=back)
            trend = 1 - back / 420
            db.upsert_daily(c, day.isoformat(), {
                "steps": round(rng.gauss(10500, 2600)), "resting_hr": round(55 - 5 * trend + rng.uniform(-2, 2)),
                "stress": round(rng.uniform(20, 42)), "sleep_s": rng.gauss(7.3, .6) * 3600, "sleep_score": round(rng.uniform(62, 91)),
                "hrv": round(52 + 11 * trend + rng.uniform(-6, 6)), "bb_high": round(rng.uniform(72, 100)), "bb_low": round(rng.uniform(8, 32)),
                "weight_kg": round(74.5 - 2.4 * trend + rng.uniform(-.3, .3), 1),
            })
