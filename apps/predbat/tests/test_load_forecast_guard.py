"""Prospective scoring, persistence and source-selection regression tests."""

import copy
import unittest
from datetime import datetime, timedelta, timezone

from fetch import Fetch
from load_forecast_guard import LoadForecastGuard, forecast_energy
from utils import MinuteArray, is_debug_excluded_key


def curve(rate, minutes=3000, reset=False):
    """Dense cumulative energy for a fixed kWh/minute load, optionally resetting daily."""
    return {minute: (minute % 1440 if reset else minute) * rate for minute in range(minutes + 1)}


class GuardBase(Fetch):
    """Small real-Fetch harness with measured load and no inverter or HA writes."""

    def __init__(self):
        """Initialise a UTC day and 3 days of fine-grained household energy."""
        self.now_utc = datetime(2026, 11, 19, tzinfo=timezone.utc)
        self.midnight_utc = self.now_utc
        self.minutes_now = 0
        self.prefix = "predbat"
        self.forecast_days = 2
        self.forecast_minutes = 2880
        self.plan_interval_minutes = 30
        self.load_minutes_age = 3
        self.args = {"load_ml_enable": True, "load_ml_source": True, "load_ml_guard_mode": "monitor"}
        self.load_minutes = {minute: (5000 - minute) * 0.01 for minute in range(4321)}
        self.load_minutes_source = "load_today"
        self.car_charging_hold = False
        self.car_charging_energy = {}
        self.iboost_energy_subtract = False
        self.iboost_energy_today = {}
        self.base_load = 0
        self.components = None
        self.logs = []
        self.notifications = []
        self.dashboard = {}
        self.load_forecast_only = True
        self.load_forecast_history = True

    def get_arg(self, name, default=None, **kwargs):
        """Read test configuration without HA entity lookups."""
        return self.args.get(name, default)

    def log(self, message):
        """Keep warnings for assertions."""
        self.logs.append(message)

    def dashboard_item(self, entity_id, state, attributes, **kwargs):
        """Record the guard entity instead of publishing anything."""
        self.dashboard[entity_id] = {"state": state, "attributes": attributes}

    def call_notify(self, message):
        """Record optional notifications."""
        self.notifications.append(message)

    def get_from_incrementing(self, data, index, backwards=True):
        """Use the same non-negative cumulative increment as the production helper."""
        return max(data.get(index, 0) - data.get(index + 1, 0), 0)

    def get_state_wrapper(self, entity_id, attribute=None, **kwargs):
        """Serve the ML forecast sensor with an independently controllable state."""
        return self.results if attribute == "results" else self.model_state

    def log_replay_ml_forecast(self, forecast):
        """Skip replay output in the isolated harness."""


class FakeStorage:
    """Scripted Storage API, with copies so memory mutations cannot change saved evidence."""

    def __init__(self, value=None):
        """Start empty or restore the supplied evidence."""
        self.value = value
        self.saves = []

    async def load(self, *args):
        """Return a copy or simulate a storage outage."""
        if isinstance(self.value, Exception):
            raise self.value
        return copy.deepcopy(self.value)

    async def save(self, module, key, data, **kwargs):
        """Record the store independently of live state."""
        self.value = copy.deepcopy(data)
        self.saves.append(copy.deepcopy(data))
        return True


class Components:
    """Expose only the test storage component."""

    def __init__(self, storage):
        """Store the supplied backend."""
        self.storage = storage

    def get_component(self, name):
        """Return storage for its component name."""
        return self.storage if name == "storage" else None


def daily_evidence(now, count=3, ml=40, classic=20, actual=20):
    """Independent, completed daily windows ending at now, newest first."""
    return [{"start": now - (i + 1) * 1440, "duration": 1440, "ml": ml, "classic": classic, "actual": actual} for i in range(count)]


class LoadForecastGuardTests(unittest.TestCase):
    """Exercise meaningful guard decisions and production Fetch integration."""

    def test_capture_is_prospective_and_frozen(self):
        """Later forecasts cannot rewrite evidence and actuals cannot appear early."""
        guard = LoadForecastGuard("house")
        guard.capture(10000, 0, curve(0.02), curve(0.01))
        guard.capture(10005, 5, curve(1), curve(1))
        self.assertEqual(len(guard.records), 3)
        self.assertEqual(guard.records[0]["ml"], 1.2)
        reads = []
        guard.advance(10059, lambda *args: reads.append(args) or 99)
        self.assertFalse(reads)
        guard.advance(10060, lambda start, duration: duration * 0.01)
        self.assertEqual(guard.scores()["1h"]["ml_mae_kwh"], 0.6)
        self.assertIsNone(guard.records[1]["actual"])
        guard.advance(10060, lambda *args: 99)
        self.assertEqual(guard.records[0]["actual"], 0.6)

    def test_midnight_and_invalid_forecast(self):
        """Daily resets keep energy totals, whereas missing and non-finite values do not score."""
        self.assertAlmostEqual(forecast_energy(curve(0.01, reset=True), 1380, 480), 4.79)
        # At the reset boundary the published zero omits the preceding minute;
        # consumers see that same omission. Do not invent energy to repair it.
        for value in (None, float("nan"), float("inf"), -1, True):
            data = curve(0.01)
            data[30] = value
            self.assertIsNone(forecast_energy(data, 0, 60))
        data = curve(0.01)
        del data[45]
        self.assertIsNone(forecast_energy(data, 0, 60))
        self.assertEqual(forecast_energy({0: 20, 1: 15, 2: 10, 3: 5, 4: 0}, 0, 4), 0)

    def test_missing_actuals_do_not_become_zero(self):
        """Incomplete measured intervals remain pending, rather than poisoning the score."""
        guard = LoadForecastGuard("house")
        guard.capture(10000, 0, curve(0.02), curve(0.01))
        guard.advance(11000, lambda *args: None)
        self.assertEqual(guard.scores()["8h"]["samples"], 0)
        guard.advance(11000, lambda start, duration: duration * 0.01)
        self.assertEqual(guard.scores()["8h"]["ml_mae_kwh"], 4.8)

    def test_daily_hysteresis_and_recovery(self):
        """Repeated independent failures latch; one good day cannot clear the latch."""
        guard = LoadForecastGuard("house")
        now = 100000
        guard.records = daily_evidence(now, 2)
        self.assertEqual(guard.judge(now), "measuring")
        self.assertFalse(guard.fallback)
        guard.records = daily_evidence(now)
        self.assertEqual(guard.judge(now), "ml_worse_than_classic")
        guard.records[0]["ml"] = 20
        guard.judge(now)
        self.assertTrue(guard.fallback)
        guard.records = daily_evidence(now, ml=20)
        self.assertEqual(guard.judge(now), "within_limits")
        self.assertFalse(guard.fallback)

    def test_overlap_and_stale_evidence_do_not_authorise_switches(self):
        """Many overlapping samples are not independent days; old successes cannot restore ML."""
        guard = LoadForecastGuard("house")
        now = 100000
        guard.records = [{**daily_evidence(now, 1)[0], "start": now - 1440 - i * 60} for i in range(20)]
        guard.judge(now)
        self.assertFalse(guard.fallback)
        guard.fallback = True
        guard.records = daily_evidence(now - 1440, ml=20)
        guard.judge(now)
        self.assertTrue(guard.fallback)

    def test_relative_errors_and_classic_comparison(self):
        """A large error alone cannot veto ML when classic is even worse."""
        guard = LoadForecastGuard("house")
        guard.records = daily_evidence(100000, ml=40, classic=50)
        guard.judge(100000)
        self.assertFalse(guard.fallback)
        guard.records = daily_evidence(100000, ml=22, classic=20)
        guard.judge(100000)
        self.assertFalse(guard.fallback)

    def test_restore_retention_and_clock_changes(self):
        """Restart preserves the latch and observations; incompatible or future data is excluded."""
        guard = LoadForecastGuard("house")
        guard.records = daily_evidence(100000)
        guard.fallback = True
        saved = guard.serialise()
        saved["records"] += [{"start": 999999, "duration": 1440, "ml": 1, "classic": 1, "actual": 1}, {"start": 0, "duration": 60}]
        restored = LoadForecastGuard("house")
        restored.restore(saved, 100000)
        self.assertTrue(restored.fallback)
        self.assertEqual(len(restored.records), 3)
        self.assertEqual(LoadForecastGuard("other").restore(saved, 100000), None)
        restored.advance(120000, lambda *args: None)
        self.assertFalse(restored.records)
        restored.capture(120000, 0, curve(0.02), curve(0.01))
        restored.advance(119000, lambda *args: None)
        self.assertFalse(restored.records)

    def test_actual_load_and_ev_boundaries(self):
        """An EV outside the load measurement is not subtracted; measured in-load EV is subtracted once."""
        base = GuardBase()
        now = int(base.now_utc.timestamp() // 60)
        base.car_charging_energy = {minute: (5000 - minute) * 0.005 for minute in range(4321)}
        self.assertAlmostEqual(base.guard_actual_load(now - 60, 60), 0.6)
        base.car_charging_hold = True
        self.assertAlmostEqual(base.guard_actual_load(now - 60, 60), 0.3)
        base.iboost_energy_subtract = True
        base.iboost_energy_today = {minute: (5000 - minute) * 0.002 for minute in range(4321)}
        self.assertAlmostEqual(base.guard_actual_load(now - 60, 60), 0.18)
        base.car_charging_energy = {}
        self.assertIsNone(base.guard_actual_load(now - 60, 60))
        base.car_charging_hold = False
        base.load_minutes = MinuteArray({}, 20)
        self.assertIsNone(base.guard_actual_load(now - 60, 60))

    def test_monitor_and_auto_source_selection(self):
        """Monitoring reports poor forecasts without changing selection; auto mode vetoes ML."""
        base = GuardBase()
        ml, classic = curve(0.02), curve(0.01)
        use_ml, changed = base.update_load_forecast_guard(ml, classic)
        self.assertTrue(use_ml)
        self.assertFalse(changed)
        now = int(base.now_utc.timestamp() // 60)
        base.load_forecast_guard.records = daily_evidence(now)
        use_ml, _ = base.update_load_forecast_guard(ml, classic)
        self.assertTrue(use_ml)
        attrs = base.dashboard["sensor.predbat_load_ml_accuracy"]["attributes"]
        self.assertEqual(attrs["recommended_source"], "classic")
        base.args["load_ml_guard_mode"] = "auto"
        use_ml, changed = base.update_load_forecast_guard(ml, classic)
        self.assertFalse(use_ml)
        self.assertTrue(changed)
        self.assertFalse(base.notifications)
        base.args["load_ml_source"] = False
        base.update_load_forecast_guard(ml, classic)
        self.assertEqual(base.load_forecast_guard_selected, "classic")

    def test_storage_outage_restart_and_notification_deduplication(self):
        """A failed read cannot overwrite stored evidence; repeated warnings are not spammed."""
        base = GuardBase()
        storage = FakeStorage(RuntimeError("offline"))
        base.components = Components(storage)
        ml, classic = curve(0.02), curve(0.01)
        base.update_load_forecast_guard(ml, classic)
        self.assertFalse(storage.saves)
        saved = base.load_forecast_guard.serialise()
        now = int(base.now_utc.timestamp() // 60)
        saved["records"] = daily_evidence(now)
        saved["fallback"] = True
        storage.value = saved
        base.args.update(load_ml_guard_mode="auto", load_ml_guard_notify=True)
        use_ml, _ = base.update_load_forecast_guard(ml, classic)
        self.assertFalse(use_ml)
        self.assertTrue(storage.saves)
        self.assertEqual(len(base.notifications), 1)
        base.update_load_forecast_guard(ml, classic)
        self.assertEqual(len(base.notifications), 1)
        for delta in (5, 10):
            fresh = GuardBase()
            fresh.components = Components(FakeStorage())
            for advance in (0, 5, delta):
                fresh.now_utc = base.now_utc + timedelta(minutes=advance)
                fresh.update_load_forecast_guard(ml, classic)
            self.assertEqual(bool(fresh.components.storage.saves), delta >= 10)

    def test_veto_preserves_external_forecasts(self):
        """Fallback removes only ML, restores classic eligibility and forces a replan."""
        base = GuardBase()
        ml, extra = {0: 1.0, 60: 2.0}, {0: 0.2, 60: 0.5}
        base.load_forecast_array = [ml, extra]
        base.load_forecast = {0: 1.2, 60: 2.5}
        base.classic_load_forecast_for_guard = lambda: curve(0.01)
        base.update_load_forecast_guard = lambda *args: (False, True)
        selected, classic, changed = base.apply_load_forecast_guard(ml, ml)
        self.assertEqual(selected, {})
        self.assertEqual(base.load_forecast, extra)
        self.assertEqual(base.load_forecast_array, [extra])
        self.assertFalse(base.load_forecast_only)
        self.assertTrue(changed)
        self.assertTrue(classic)

    def test_invalid_or_stale_ml_never_serves_leftover_results(self):
        """An invalid sensor state, old timestamps, gaps or NaNs force the classic path."""
        base = GuardBase()
        base.results = {(base.now_utc + timedelta(minutes=i)).isoformat(): i * 0.01 for i in range(0, 2881, 5)}
        base.model_state = "fallback_stale"
        self.assertEqual(base.fetch_ml_load_forecast(base.now_utc, save=False), {})
        base.model_state = "active"
        self.assertTrue(base.fetch_ml_load_forecast(base.now_utc, save=False))
        self.assertEqual(base.fetch_ml_load_forecast(base.now_utc + timedelta(hours=2), save=False), {})
        first = next(iter(base.results))
        base.results[first] = float("nan")
        self.assertEqual(base.fetch_ml_load_forecast(base.now_utc, save=False), {})
        base.results[first] = 0
        del base.results[(base.now_utc + timedelta(minutes=15)).isoformat()]
        self.assertEqual(base.fetch_ml_load_forecast(base.now_utc, save=False), {})

    def test_manual_classic_forecast_does_not_mutate_settings(self):
        """Counterfactual manual-day forecasts cannot change the selected model's history or days."""
        base = GuardBase()
        base.load_forecast_history = False
        base.days_previous, base.days_previous_weight = [1, 2, 3], [1, 1, 1]
        original_days = base.days_previous
        original_data = base.load_minutes.copy()
        base.forecast_minutes, base.plan_interval_minutes = 1440, 30

        def modal(data):
            """Simulate the production filter mutating its data and self's day lists."""
            data[0] = 999
            base.days_previous.pop()
            base.days_previous_weight.pop()

        base.previous_days_modal_filter = modal
        forecast = base.classic_load_forecast_for_guard()
        self.assertTrue(forecast)
        self.assertIs(base.days_previous, original_days)
        self.assertEqual(base.days_previous, [1, 2, 3])
        self.assertEqual(base.load_minutes, original_data)

    def test_chronological_fallback_and_recovery(self):
        """Replay hourly snapshots over eight days, scoring only after each target passes."""
        guard = LoadForecastGuard("house")
        origin = 100000
        history = []
        for hour in range(8 * 24 + 1):
            now = origin + hour * 60
            guard.advance(now, lambda start, duration: duration * 20 / 1440)
            guard.capture(now, 0, curve((40 if hour < 3 * 24 else 20) / 1440), curve(20 / 1440))
            guard.judge(now)
            history.append(guard.fallback)
        self.assertFalse(any(history[: 2 * 24 + 8]))
        self.assertTrue(history[3 * 24])
        self.assertTrue(history[4 * 24])
        self.assertFalse(history[-1])
        self.assertLessEqual(len(guard.records), 510)

    def test_wrong_eight_hour_shape_with_correct_daily_total(self):
        """Correct daily energy cannot hide repeated eight-hour timing errors."""
        guard = LoadForecastGuard("house")
        now = 100000
        guard.records = daily_evidence(now, ml=20)
        guard.records += [{"start": now - 480 - day * 1440, "duration": 480, "ml": 15, "classic": 5, "actual": 5} for day in range(3)]
        guard.judge(now)
        self.assertTrue(guard.fallback)
        self.assertEqual(guard.trigger_horizon, 480)
        # Good daily totals alone must not restore a model rejected for its shape.
        guard.records = daily_evidence(now, ml=20)
        guard.judge(now)
        self.assertTrue(guard.fallback)
        guard.records += [{"start": now - 480 - day * 1440, "duration": 480, "ml": 5, "classic": 5, "actual": 5} for day in range(3)]
        guard.judge(now)
        self.assertFalse(guard.fallback)

    def test_no_empty_classic_fallback_and_bad_thresholds(self):
        """Bad configuration and missing classic data cannot turn a valid forecast into zero load."""
        base = GuardBase()
        ml, classic = curve(0.02), curve(0.01)
        base.update_load_forecast_guard(ml, classic)
        now = int(base.now_utc.timestamp() // 60)
        base.load_forecast_guard.records = daily_evidence(now)
        base.args.update(load_ml_guard_mode="auto", load_ml_guard_min_days="bad", load_ml_guard_error_kwh=float("nan"), load_ml_guard_error_percent=None)
        use_ml, _ = base.update_load_forecast_guard(ml, {})
        self.assertTrue(use_ml)
        self.assertEqual(base.dashboard["sensor.predbat_load_ml_accuracy"]["attributes"]["reason"], "classic_unavailable")

    def test_changed_load_definition_discards_only_guard_evidence(self):
        """Changing measured load resets scores, without changing measurements or model files."""
        base = GuardBase()
        ml, classic = curve(0.02), curve(0.01)
        base.update_load_forecast_guard(ml, classic)
        old_guard = base.load_forecast_guard
        base.args["load_today"] = "sensor.other_load"
        before = base.load_minutes.copy()
        base.update_load_forecast_guard(ml, classic)
        self.assertIsNot(old_guard, base.load_forecast_guard)
        self.assertEqual(before, base.load_minutes)
        self.assertEqual(base.load_forecast_guard.scores()["24h"]["samples"], 0)
        self.assertTrue(is_debug_excluded_key("load_forecast_guard"))

    def test_notification_failure_does_not_stop_planning(self):
        """An unavailable notification integration cannot prevent fallback."""
        base = GuardBase()
        ml, classic = curve(0.02), curve(0.01)
        base.update_load_forecast_guard(ml, classic)
        base.load_forecast_guard.records = daily_evidence(int(base.now_utc.timestamp() // 60))
        base.args.update(load_ml_guard_mode="auto", load_ml_guard_notify=True)

        def unavailable(message):
            """Simulate a failed notify_devices service."""
            raise RuntimeError("notify unavailable")

        base.call_notify = unavailable
        use_ml, _ = base.update_load_forecast_guard(ml, classic)
        self.assertFalse(use_ml)
        self.assertTrue(any("notification failed" in line for line in base.logs))

    def test_restored_fallback_forces_the_first_plan(self):
        """A persisted fallback must invalidate a retained ML plan on the first cycle."""
        original = GuardBase()
        ml, classic = curve(0.02), curve(0.01)
        original.update_load_forecast_guard(ml, classic)
        evidence = original.load_forecast_guard.serialise()
        evidence["records"] = daily_evidence(int(original.now_utc.timestamp() // 60))
        evidence["fallback"] = True
        restarted = GuardBase()
        restarted.args["load_ml_guard_mode"] = "auto"
        restarted.components = Components(FakeStorage(evidence))
        use_ml, changed = restarted.update_load_forecast_guard(ml, classic)
        self.assertFalse(use_ml)
        self.assertTrue(changed)
        self.assertTrue(restarted.load_forecast_guard_fallback)


def run_load_forecast_guard_tests(my_predbat=None):
    """Run the guard suite through Predbat's test registry."""
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LoadForecastGuardTests))
    return not result.wasSuccessful()
