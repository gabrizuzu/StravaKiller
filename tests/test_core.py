"""Test senza rete. Avvio: python -m unittest discover -s tests -t ."""
import os
import tempfile
import unittest
from types import SimpleNamespace as NS

os.environ["GARMIN_COACH_DATA"] = tempfile.mkdtemp()

from app import analytics as an  # noqa: E402
from app import coach, db, fit_files, sync, tools  # noqa: E402

db.init()


def steady(km, pace, step=5):
    n = int(km * 1000 / (step / pace * 1000)) + 2
    return [{"t": i * step, "d": i * step / pace * 1000, "hr": 150, "alt": 100.0} for i in range(n)]


class Efforts(unittest.TestCase):
    def efforts(self, recs):
        clean = fit_files._clean(recs)
        return fit_files.best_efforts(clean, fit_files._moving_time(clean))

    def test_steady_pace(self):
        result = self.efforts(steady(10.5, 300))
        self.assertAlmostEqual(result[5000], 1500, delta=2)
        self.assertAlmostEqual(result[10000], 3000, delta=2)
        self.assertNotIn(15000, result)

    def test_fast_block_is_found(self):
        recs, t, d = [], 0.0, 0.0
        for km, pace in [(2, 330), (5.2, 240), (2, 330)]:
            for _ in range(int(km * pace / 2)):
                t += 2
                d += 2 / pace * 1000
                recs.append({"t": t, "d": d, "hr": 150})
        self.assertAlmostEqual(self.efforts(recs)[5000], 1200, delta=6)

    def test_pause_is_not_counted(self):
        recs = steady(3, 300)
        half = len(recs) // 2
        step = recs[half]["d"] - recs[half - 1]["d"]
        for rec in recs[half:]:       # da fermo l'orologio non accumula distanza
            rec["t"] += 600
            rec["d"] -= step
        self.assertAlmostEqual(self.efforts(recs)[1000], 300, delta=2)

    def test_gps_spike_rejected(self):
        recs = steady(2, 300)
        for rec in recs[40:]:
            rec["d"] += 1000             # 1 km in 5 secondi
        self.assertGreater(self.efforts(recs)[1000], 133)

    def test_splits(self):
        clean = fit_files._clean(steady(3.2, 300))
        splits = fit_files.km_splits(clean, fit_files._moving_time(clean))
        self.assertEqual([s["km"] for s in splits], [1, 2, 3])
        self.assertAlmostEqual(splits[1]["time_s"], 300, delta=1)


class Vdot(unittest.TestCase):
    def test_reference_value(self):
        self.assertAlmostEqual(an.vdot(5000, 20 * 60), 49.8, delta=.3)   # 20:00 sui 5k

    def test_prediction_round_trip(self):
        value = an.vdot(10000, 2700)
        self.assertAlmostEqual(an.predict_seconds(value, 10000), 2700, delta=1)


class Extraction(unittest.TestCase):
    def test_iter_days_nested(self):
        payload = {"hrvSummaries": [{"calendarDate": "2026-11-01", "lastNightAvg": 60}], "userProfilePk": 1}
        self.assertEqual([d for d, _ in sync.iter_days(payload)], ["2026-11-01"])

    def test_deep_find(self):
        self.assertEqual(sync.deep_find({"values": {"overallStressLevel": 31}}, ["overallStressLevel"]), 31)

    def test_weight_grams(self):
        self.assertEqual(sync._weight_kg({"weight": 72500}), 72.5)

    def test_activity_row(self):
        row = sync.activity_row({"activityId": 7, "activityType": {"typeKey": "running"}, "startTimeLocal": "2026-11-01 07:00:00"})
        self.assertEqual((row["id"], row["type"]), ("7", "running"))


class Tools(unittest.TestCase):
    def test_validate(self):
        self.assertIsNone(tools.validate("get_health", {"days": 7}))
        self.assertIsNotNone(tools.validate("get_health", {"days": "x"}))
        self.assertIsNotNone(tools.validate("get_activity", {}))
        self.assertIsNotNone(tools.validate("rm", {}))


class _Stream:
    def __init__(self, message, texts):
        self.message, self.texts = message, texts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(NS(type="text", text=t) for t in self.texts)

    def get_final_message(self):
        return self.message


class ChatLoop(unittest.TestCase):
    def test_tool_round_then_answer(self):
        think = NS(type="thinking", thinking="", signature="sig")
        call = NS(type="tool_use", id="t1", name="get_health", input={"days": 7})
        turns = [_Stream(NS(stop_reason="tool_use", content=[think, call]), []),
                 _Stream(NS(stop_reason="end_turn", content=[NS(type="text", text="Ciao")]), ["Ciao"])]
        seen = []

        def stream(**kwargs):
            seen.append([dict(m) for m in kwargs["messages"]])
            return turns.pop(0)

        client = NS(beta=NS(messages=NS(stream=stream)))
        events = list(coach.reply("c1", "come sto?", client))
        self.assertEqual([e["type"] for e in events], ["tool", "delta", "done"])
        self.assertEqual(seen[1][1]["content"][0], {"type": "thinking", "thinking": "", "signature": "sig"})
        self.assertEqual(seen[1][2]["content"][0]["tool_use_id"], "t1")
        self.assertEqual(coach.visible_history("c1"), [{"role": "user", "text": "come sto?"}, {"role": "assistant", "text": "Ciao"}])
        # il turno successivo rimanda la cronologia completa, in sola aggiunta
        self.assertEqual(len(coach.load_history("c1")), 4)

    def test_refusal_not_saved(self):
        client = NS(beta=NS(messages=NS(stream=lambda **k: _Stream(NS(stop_reason="refusal", content=[]), []))))
        self.assertEqual(list(coach.reply("c2", "x", client))[-1]["type"], "error")
        self.assertEqual(coach.load_history("c2"), [])


if __name__ == "__main__":
    unittest.main()
