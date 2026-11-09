"""Sincronizzazione completa con client Garmin simulato e un vero file FIT binario."""
import io
import os
import struct
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta
from unittest import mock

os.environ.setdefault("GARMIN_COACH_DATA", tempfile.mkdtemp())

from app import analytics as an  # noqa: E402
from app import config, db, fit_files, sync  # noqa: E402

db.init()
FIT_EPOCH = 631065600


def build_fit(start_unix: int, km: float, pace: float) -> bytes:
    """FIT minimale: record ogni 5 s, sessione e attività con ora locale (UTC+1)."""
    start = start_unix - FIT_EPOCH
    body = bytearray()

    def define(local, global_msg, fields):
        body.extend(struct.pack("<BBBHB", 0x40 | local, 0, 0, global_msg, len(fields)))
        for number, size, base in fields:
            body.extend(bytes((number, size, base)))

    define(0, 20, [(253, 4, 0x86), (5, 4, 0x86), (3, 1, 0x02), (2, 2, 0x84)])
    steps = int(km * 1000 / (5 / pace * 1000))
    for i in range(1, steps + 1):
        distance = round(i * 5 / pace * 1000 * 100)
        body.extend(struct.pack("<BIIBH", 0, start + i * 5, distance, 150, 3000))
    define(1, 18, [(253, 4, 0x86), (2, 4, 0x86), (9, 4, 0x86), (7, 4, 0x86), (8, 4, 0x86),
                   (16, 1, 0x02), (17, 1, 0x02), (5, 1, 0x00), (6, 1, 0x00)])
    total = steps * 5
    body.extend(struct.pack("<BIIIIIBBBB", 1, start + total, start, round(km * 100000), total * 1000, total * 1000, 150, 170, 1, 0))
    define(2, 34, [(253, 4, 0x86), (5, 4, 0x86)])
    body.extend(struct.pack("<BII", 2, start + total, start + total + 3600))
    header = struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0)
    return header + bytes(body) + b"\x00\x00"


def zipped(data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("1_ACTIVITY.fit", data)
    return buffer.getvalue()


DAY = (date.today() - timedelta(days=2)).isoformat()


def activity(i):
    return {"activityId": i, "activityName": f"Corsa {i}", "startTimeLocal": f"{DAY} 07:00:00", "activityType": {"typeKey": "running"},
            "distance": 6000.0, "duration": 1800.0, "movingDuration": 1800.0, "averageHR": 150.0, "maxHR": 175.0, "vO2MaxValue": 50.0}


class FakeClient:
    def __init__(self, token): self.pages = [[activity(i) for i in range(1, 46)], [activity(46)], []]
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_user_profile_settings(self): return {"userData": {"maxHeartRateUsed": 186}}
    def search_activities(self, start=0, limit=20): return self.pages[start // 45]
    def download_activity(self, activity_id): return zipped(build_fit(1793000000, 6.0, 300))
    def get_daily_steps(self, a, b): return [{"calendarDate": DAY, "totalSteps": 9000}]
    def get_daily_stress(self, a, b): return [{"calendarDate": DAY, "values": {"overallStressLevel": 31}}]
    def get_daily_summaries(self, a, b): return [{"calendarDate": DAY, "restingHeartRate": 48}]
    def get_daily_sleep_summaries(self, a, b): return [{"calendarDate": DAY, "sleepTimeSeconds": 25200, "sleepScores": {"overall": {"value": 80}}}]
    def get_daily_hrv_summaries(self, a, b): return {"hrvSummaries": [{"calendarDate": DAY, "lastNightAvg": 61}]}
    def get_daily_body_battery(self, a, b): return [{"date": DAY, "bodyBatteryValuesArray": [[1, 20], [2, 90]]}]
    def get_weight_measurements(self, a, b): raise sync.HttpError(status_code=500, method="GET", url="u", response_text="")


class FakeAuth:
    def __init__(self, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def obtain_token(self, key): return mock.Mock(access_token="t")


class FitParsing(unittest.TestCase):
    def test_real_binary(self):
        session, records = fit_files.parse_fit(build_fit(1793000000, 6.0, 300))
        self.assertEqual(session["sport"], "running")
        self.assertAlmostEqual(session["distance_m"], 6000, delta=1)
        self.assertEqual(session["start_local"][11:], "08:33:20")     # UTC 07:33:20 + 1 h
        clean = fit_files._clean(records)
        self.assertAlmostEqual(fit_files.best_efforts(clean, fit_files._moving_time(clean))[5000], 1500, delta=3)


class FullSync(unittest.TestCase):
    def test_sync(self):
        config.save_secret("garmin_api_key", "k")
        sync.PAUSE_S = 0
        with mock.patch.object(sync, "GarminPersonalApiClient", FakeClient), mock.patch.object(sync, "PersonalApiAuthenticator", FakeAuth):
            message = sync._sync()
        self.assertIn("46 attività", message)
        self.assertIn("46 corse", message)
        self.assertTrue(any("weight" in w for w in sync.status["warnings"]))    # una serie rotta non blocca il resto
        day = next(d for d in an.health(10) if d["date"] == DAY)
        self.assertEqual((day["steps"], day["stress"], day["resting_hr"], day["sleep_score"], day["hrv"], day["bb_high"], day["bb_low"], day["sleep_h"]),
                         (9000, 31, 48, 80, 61, 90, 20, 7.0))
        self.assertAlmostEqual(an.leaderboard(5000)[0]["time_s"], 1500, delta=3)
        self.assertEqual(db.get_setting("garmin_max_hr"), 186)

        # seconda importazione dello stesso file: riconosciuta come doppione
        same_day = int(datetime.fromisoformat(f"{DAY}T07:00:00+00:00").timestamp())
        result = sync._import([("x.zip", zipped(build_fit(same_day, 6.0, 300)))])
        self.assertIn("0 attività aggiunte", result)
        self.assertIn("1 file ignorati", result)


if __name__ == "__main__":
    unittest.main()
