"""Direct regression, independent evidence and three-source planning regressions."""

import asyncio
import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np

from load_forecast_guard import LoadForecastGuard, LoadForecastComparison
from load_regression import LoadRegressionPredictor, MODEL_VERSION
from load_regression_forecast import RegressionForecast
from load_ml_component import LoadMLComponent
from tests.test_load_forecast_guard import GuardBase, curve, paired_evidence
from tests.test_web_load_ml_accuracy import accuracy_web
from utils import is_debug_excluded_key


def history(days=16):
    """Repeated household profile with a known daily high-load event and no future load."""
    return {m: (0.25 if 60 < (-m % 1440) <= 180 else 0.04) for m in range(days * 1440 + 1) if m % 5 == 0}


class KeyStorage:
    """Storage fixture that keeps models and evidence independently, just like production."""

    def __init__(self):
        """Start empty and record exact writes."""
        self.values, self.saves, self.offline = {}, [], False

    async def load(self, module, key):
        """Copy persisted values, or fail without pretending storage was empty."""
        if self.offline:
            raise RuntimeError("offline")
        return copy.deepcopy(self.values.get(key))

    async def save(self, module, key, value, **kwargs):
        """Save a deep copy with its key for assertions."""
        self.values[key] = copy.deepcopy(value)
        self.saves.append(key)
        return True


class ThreeModelTests(unittest.TestCase):
    """Synthetic prospective tests use known future energy, never retrospectively edited forecasts."""

    def base(self):
        """Real Fetch methods with an independent regression candidate and no device writes."""
        base = GuardBase()
        base.args["load_ml_guard_mode"] = "auto"
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        return base

    def evidence(self, base, neural=21, regression=20, classic=25, actual=20):
        """Give both candidates identical independent issue times and measured consumption."""
        now = int(base.now_utc.timestamp() // 60)
        base.load_forecast_guard.records = paired_evidence(now, ml=neural, classic=classic, actual=actual)
        base.load_forecast_guard_regression.records = paired_evidence(now, ml=regression, classic=classic, actual=actual)

    def test_default_is_historical_with_all_three_available(self):
        """Being trained or publishing a forecast does not itself grant either candidate permission."""
        base = self.base()
        self.assertEqual(base.load_forecast_guard_selected, "classic")
        attrs = base.dashboard["sensor.predbat_load_ml_accuracy"]["attributes"]
        self.assertEqual(attrs["models"], ["classic", "ml", "regression"])
        self.assertEqual(attrs["scores"]["8h"]["samples"], 0)

    def test_best_qualified_candidate_is_selected_and_replans(self):
        """A clearly better regression earns the plan; a better neural forecast can also win."""
        for neural, regression, expected in ((21, 20, "regression"), (20, 21, "ml")):
            base = self.base()
            self.evidence(base, neural, regression)
            use, changed = base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
            self.assertTrue(use)
            self.assertTrue(changed)
            self.assertEqual(base.load_forecast_guard_selected, expected)

    def test_model_without_own_evidence_cannot_inherit_permission(self):
        """Existing neural qualification does not qualify a newly added regression model."""
        base = self.base()
        self.evidence(base, neural=20)
        base.load_forecast_guard_regression.records = []
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertEqual(base.load_forecast_guard_selected, "ml")
        self.assertTrue(base.load_forecast_guard_regression.fallback)

    def test_one_day_cannot_qualify_either_candidate(self):
        """Many excellent predictions from one day do not count as independent days."""
        base = self.base()
        self.evidence(base)
        for guard in (base.load_forecast_guard, base.load_forecast_guard_regression):
            guard.records = guard.records[:1] + [r for r in guard.records if r["duration"] == 1440][:1]
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertEqual(base.load_forecast_guard_selected, "classic")

    def test_minor_advantage_does_not_switch_incumbent(self):
        """Matched-window hysteresis avoids changing the plan for negligible improvements."""
        base = self.base()
        self.evidence(base, neural=21, regression=21.05)
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertEqual(base.load_forecast_guard_selected, "ml")
        self.evidence(base, neural=21.05, regression=21)
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertEqual(base.load_forecast_guard_selected, "ml")
        self.evidence(base, neural=21, regression=20)
        use, changed = base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertTrue(use and changed)
        self.assertEqual(base.load_forecast_guard_selected, "regression")

    def test_missing_regression_uses_qualified_neural_or_historical(self):
        """A failed regression never leaves stale results in the battery plan."""
        base = self.base()
        self.evidence(base)
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertEqual(base.load_forecast_guard_selected, "regression")
        base.update_load_forecast_guard(curve(0.01), curve(0.01), {})
        self.assertEqual(base.load_forecast_guard_selected, "ml")
        base.update_load_forecast_guard({}, curve(0.01), {})
        self.assertEqual(base.load_forecast_guard_selected, "classic")

    def test_both_models_deteriorating_return_to_historical(self):
        """Independent repeated losses revoke both grants and request a new historical plan."""
        base = self.base()
        self.evidence(base)
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.evidence(base, neural=35, regression=36, classic=20)
        use, changed = base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertFalse(use)
        self.assertTrue(changed)
        self.assertEqual(base.load_forecast_guard_selected, "classic")

    def test_monitor_and_source_opt_out_keep_existing_behaviour(self):
        """The saved guard mode and explicit source flag remain authoritative."""
        base = self.base()
        self.evidence(base)
        base.args["load_ml_guard_mode"] = "monitor"
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertEqual(base.load_forecast_guard_selected, "ml")
        base.args.update(load_ml_guard_mode="auto", load_ml_source=False)
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertEqual(base.load_forecast_guard_selected, "classic")

    def test_selection_replaces_only_neural_entry_preserving_extra_load(self):
        """Real source application changes the learned forecast once, keeping independent extra loads."""
        base = self.base()
        self.evidence(base)
        neural, reg, extra = curve(0.02), curve(0.01), curve(0.003)
        base.classic_load_forecast_for_guard = lambda: curve(0.015)
        base.load_forecast_array = [neural, extra]
        base.load_forecast = {m: neural[m] + extra[m] for m in neural}
        selected, _, changed = base.apply_load_forecast_guard(neural, neural, reg)
        self.assertIs(selected, reg)
        self.assertTrue(changed)
        self.assertEqual(base.load_forecast_array, [reg, extra])
        self.assertAlmostEqual(base.load_forecast[60], reg[60] + extra[60])
        self.assertTrue(base.load_forecast_only)

    def test_matched_scores_never_mix_different_periods_or_actuals(self):
        """A model cannot win a comparison by being scored only on easier hours."""
        base = self.base()
        self.evidence(base)
        comparison = LoadForecastComparison(base.load_forecast_guard, base.load_forecast_guard_regression)
        self.assertEqual(comparison.scores()["24h"]["samples"], 3)
        base.load_forecast_guard_regression.records[0]["actual"] += 1
        self.assertEqual(comparison.scores()["8h"]["samples"], 2)
        base.load_forecast_guard_regression.records[3]["start"] += 5
        self.assertEqual(comparison.scores()["24h"]["samples"], 2)

    def test_storage_restart_and_legacy_neural_evidence_are_preserved(self):
        """Separate keys preserve existing neural observations and the selected regression across restart."""
        storage = KeyStorage()
        base = self.base()
        self.evidence(base)
        base.components = SimpleNamespace(get_component=lambda name: storage)
        for guard in (base.load_forecast_guard, base.load_forecast_guard_regression):
            guard.loaded = True
            guard.dirty = True
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertIn("load_forecast_accuracy", storage.values)
        self.assertIn("load_forecast_regression_accuracy", storage.values)
        restarted = GuardBase()
        restarted.args["load_ml_guard_mode"] = "auto"
        restarted.components = base.components
        _, changed = restarted.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertEqual(restarted.load_forecast_guard_selected, "regression")
        self.assertTrue(changed)
        self.assertGreaterEqual(restarted.load_forecast_guard.scores()["24h"]["samples"], 3)
        del storage.values["load_forecast_regression_accuracy"]
        legacy = GuardBase()
        legacy.args["load_ml_guard_mode"] = "auto"
        legacy.components = base.components
        legacy.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertEqual(legacy.load_forecast_guard_selected, "ml")
        self.assertTrue(legacy.load_forecast_guard_regression.fallback)

    def test_storage_outage_does_not_overwrite_evidence(self):
        """A failed load is never treated as a new empty model store."""
        base = self.base()
        storage = KeyStorage()
        storage.offline = True
        base.components = SimpleNamespace(get_component=lambda name: storage)
        for minute in (0, 5, 10, 15):
            base.now_utc += timedelta(minutes=minute)
            base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertFalse(storage.saves)

    def test_regression_version_change_keeps_neural_evidence(self):
        """A changed regression algorithm starts fresh without destroying compatible neural scores."""
        base = self.base()
        self.evidence(base)
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        neural, regression = base.load_forecast_guard, base.load_forecast_guard_regression
        with patch("fetch.REGRESSION_MODEL_VERSION", MODEL_VERSION + 1):
            base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertIs(base.load_forecast_guard, neural)
        self.assertIsNot(base.load_forecast_guard_regression, regression)
        self.assertEqual(base.load_forecast_guard_selected, "ml")

    def test_chronological_three_model_switches_and_restart(self):
        """Two weeks of frozen hourly predictions exercise qualification, competition and recovery."""
        neural, reg = LoadForecastGuard("house"), LoadForecastGuard("house:regression")
        selected, history_by_hour = "classic", []
        origin = 100000
        for hour in range(14 * 24 + 1):
            now = origin + hour * 60
            for guard in (neural, reg):
                guard.advance(now, lambda start, duration: 20 * duration / 1440)
            neural.capture(now, 0, curve((20 if hour < 3 * 24 else 21) / 1440), curve(24 / 1440))
            reg_rate = 22 if hour < 3 * 24 else 40 if 6 * 24 <= hour < 9 * 24 else 20
            reg.capture(now, 0, curve(reg_rate / 1440), curve(24 / 1440))
            neural.judge(now)
            reg.judge(now)
            selected = LoadForecastComparison(neural, reg, selected).choose(now, {"ml": True, "regression": True})
            history_by_hour.append(selected)
            if hour == 10 * 24:
                first, second = neural.serialise(), reg.serialise()
                neural, reg = LoadForecastGuard("house"), LoadForecastGuard("house:regression")
                neural.restore(first, now)
                reg.restore(second, now)
        self.assertEqual(set(history_by_hour[: 3 * 24]), {"classic"})
        self.assertEqual(history_by_hour[3 * 24], "ml")
        self.assertEqual(history_by_hour[6 * 24], "regression")
        self.assertEqual(history_by_hour[9 * 24], "ml")
        self.assertEqual(history_by_hour[-1], "regression")
        self.assertLessEqual(len(reg.records), 510)

    def test_no_historical_data_never_creates_empty_planning_load(self):
        """An unavailable baseline keeps a valid candidate with a visible warning."""
        base = self.base()
        use, _ = base.update_load_forecast_guard({}, {}, curve(0.01))
        self.assertTrue(use)
        self.assertEqual(base.load_forecast_guard_selected, "regression")
        self.assertEqual(base.dashboard["sensor.predbat_load_ml_accuracy"]["attributes"]["reason"], "classic_unavailable")

    def test_changed_load_definition_resets_both_candidates_only(self):
        """New exclusions require new evidence while actual load and neural weights remain untouched."""
        base = self.base()
        self.evidence(base)
        old = base.load_forecast_guard
        data = base.load_minutes.copy()
        base.args["iboost_energy_today"] = "sensor.immersion_energy"
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        self.assertIsNot(base.load_forecast_guard, old)
        self.assertTrue(base.load_forecast_guard.fallback)
        self.assertTrue(base.load_forecast_guard_regression.fallback)
        self.assertEqual(base.load_minutes, data)

    def test_historical_curve_is_household_only_and_does_not_change_sources(self):
        """The third graph uses measured filtered load and cannot select or mutate a source."""
        base = self.base()
        original = base.load_minutes.copy()
        base.minutes_now = 60
        base.now_utc += timedelta(hours=1)
        base.publish_classic_load_forecast(curve(0.01))
        item = base.dashboard["sensor.predbat_load_historical_forecast"]
        self.assertEqual(item["state"], "active")
        self.assertTrue(item["attributes"]["results"])
        self.assertEqual(base.load_minutes, original)
        self.assertEqual(base.load_forecast_guard_selected, "classic")

    def test_regression_validation_is_independent_of_neural_sensor(self):
        """Unavailable, stale or incomplete regression entities are rejected on their own merits."""
        base = GuardBase()
        results = {(base.now_utc + timedelta(minutes=m)).isoformat(): m * 0.01 for m in range(0, 2881, 5)}
        statuses = {"sensor.predbat_load_ml_forecast": "active", "sensor.predbat_load_regression_forecast": "unavailable"}
        base.get_state_wrapper = lambda entity, attribute=None, **kwargs: results if attribute == "results" else statuses[entity]
        self.assertTrue(base.fetch_ml_load_forecast(base.now_utc, save=False))
        self.assertFalse(base.fetch_ml_load_forecast(base.now_utc, save=False, regression=True))
        statuses["sensor.predbat_load_regression_forecast"] = "active"
        self.assertTrue(base.fetch_ml_load_forecast(base.now_utc, save=False, regression=True))
        del results[(base.now_utc + timedelta(minutes=20)).isoformat()]
        self.assertFalse(base.fetch_ml_load_forecast(base.now_utc, save=False, regression=True))

    def test_three_column_display_names_regression_and_preserves_controls(self):
        """All three scores, matching periods and the selected regression are visible in the existing panel."""
        base = self.base()
        self.evidence(base)
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        attrs = base.dashboard["sensor.predbat_load_ml_accuracy"]["attributes"]
        web = accuracy_web(attrs, mode="auto")
        web.base.now_utc = base.now_utc
        html = web.get_load_ml_accuracy_html()
        self.assertIn("Regression error", html)
        self.assertIn("Regression earned selection", html)
        self.assertIn("3 completed matched windows", html)
        self.assertIn("Apply mode", html)
        self.assertTrue(is_debug_excluded_key("load_forecast_guard_regression"))


class RegressionTests(unittest.TestCase):
    """Forecast energy, true holdout boundaries, physical constraints and model persistence."""

    def model(self, data=None, temperature=None):
        """Fit a tiny direct model, without neural training or live HA."""
        model = LoadRegressionPredictor(log_func=lambda message: None)
        self.assertIsNotNone(model.train(history() if data is None else data, temperature))
        return model

    def test_deterministic_direct_forecast_preserves_daily_event_and_energy(self):
        """The same history gives identical weights and keeps a repeatable heating event eight hours ahead."""
        data = history()
        first, second = self.model(data), self.model(data)
        np.testing.assert_array_equal(first.weights, second.weights)
        forecast = first.predict(data)
        self.assertEqual(forecast[0], 0)
        self.assertEqual(max(forecast), 2880)
        self.assertEqual(len(forecast), 577)
        self.assertAlmostEqual(forecast[180] - forecast[60], 6, places=2)
        self.assertAlmostEqual(forecast[1440], 16.56, places=2)
        self.assertTrue(all(b >= a for a, b in zip(forecast.values(), list(forecast.values())[1:])))

    def test_insufficient_or_missing_history_never_becomes_zero_forecast(self):
        """A candidate with too little complete history remains unavailable."""
        model = LoadRegressionPredictor(log_func=lambda message: None)
        self.assertIsNone(model.train(history(8)))
        self.assertEqual(model.predict(history(8)), {})

    def test_held_out_cold_days_cannot_enable_the_cold_term(self):
        """Model-structure selection sees training history only, not held-out future temperature."""
        temperature = {m: (-10 if m < 2880 else 15) for m in history()}
        model = self.model(temperature=temperature)
        self.assertFalse(model.cold_fitted)
        self.assertEqual(model.weights[3], 0)

    def test_finite_model_round_trip_and_invalid_restore(self):
        """Only finite compatible coefficients and dated validation can restore a model."""
        model = self.model()
        saved = model.serialise()
        restored = LoadRegressionPredictor(log_func=lambda message: None)
        self.assertTrue(restored.restore(saved))
        self.assertEqual(model.predict(history()), restored.predict(history()))
        for bad in ({**saved, "version": -1}, {**saved, "weights": [float("nan")] * 6}, {**saved, "mae": -1}, {**saved, "trained": "not a date"}):
            self.assertFalse(restored.restore(bad))
        self.assertFalse(model.is_valid(model.training_timestamp + timedelta(hours=49)))

    def test_physical_cap_and_no_temperature_fallback(self):
        """Missing weather data cannot create NaNs or unbounded heating forecasts."""
        model = self.model()
        model.max_load_kw = 1
        forecast = model.predict(history())
        self.assertTrue(all(np.isfinite(v) and v >= 0 for v in forecast.values()))
        self.assertLessEqual(max(forecast.values()), 48 + 0.001)

    def test_runner_publishes_independent_sensor_and_correct_midnight_reset(self):
        """The regression has separate entities and 5-minute cumulative values across midnight."""
        published = {}
        now = datetime.now(timezone.utc).replace(hour=23, minute=55, second=0, microsecond=0)
        component = SimpleNamespace(log=lambda message: None, ml_max_load_kw=23, now_utc=now, midnight_utc=now.replace(hour=0, minute=0), minutes_now=1435, prefix="predbat", load_data_age_days=16)
        component._load_baseline_now = lambda: 20
        component.dashboard_item = lambda entity, state, attributes, **kwargs: published.update({entity: {"state": state, "attributes": attributes}})
        runner = RegressionForecast(component)
        runner.status = "active"
        runner.publish({m: m / 60 for m in range(0, 2881, 5)}, {})
        data = published["sensor.predbat_load_regression_forecast"]["attributes"]["results"]
        self.assertEqual(data[now.strftime("%Y-%m-%dT%H:%M:%S%z")], 20)
        self.assertEqual(data[(now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S%z")], 0)
        self.assertAlmostEqual(data[(now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S%z")], 1 / 12, places=4)
        self.assertIn("mae_kwh_per_30min", published["sensor.predbat_load_regression_stats"]["attributes"])
        self.assertNotIn("sensor.predbat_load_ml_forecast", published)

    def component(self, storage, days=16):
        """Prepared, already-filtered input channels shared with a neural component fixture."""
        now = datetime.now(timezone.utc)
        component = SimpleNamespace(
            log=lambda message: None,
            ml_max_load_kw=23,
            ml_max_model_age_hours=48,
            ml_retrain_interval_seconds=7200,
            now_utc=now,
            midnight_utc=now.replace(hour=0, minute=0, second=0, microsecond=0),
            minutes_now=now.hour * 60 + now.minute,
            prefix="predbat",
            load_data_age_days=days,
            load_data=history(days),
            temperature_data={},
            data_lock=asyncio.Lock(),
            base=SimpleNamespace(components=SimpleNamespace(get_component=lambda name: storage)),
            args={"iboost_energy_today": "sensor.immersion_energy", "iboost_energy_subtract": True},
        )
        component.get_arg = lambda name, default=None, **kwargs: component.args.get(name, default)
        component._load_baseline_now = lambda: 10
        component.published = {}
        component.dashboard_item = lambda entity, state, attributes, **kwargs: component.published.update({entity: {"state": state, "attributes": attributes}})
        return component

    def test_runner_trains_without_neural_changes_and_saves_after_empty_read_grace(self):
        """A new store waits ten minutes, then saves the already fitted model without retraining."""
        storage = KeyStorage()
        c = self.component(storage)
        neural = object()
        c.predictor = neural
        runner = RegressionForecast(c)
        asyncio.run(runner.update())
        self.assertEqual(runner.status, "active")
        self.assertFalse(storage.saves)
        trained = runner.predictor.training_timestamp
        for _ in range(2):
            c.now_utc += timedelta(minutes=5)
            c.minutes_now += 5
            asyncio.run(runner.update())
        self.assertIn("load_regression_model", storage.values)
        self.assertEqual(storage.values["load_regression_model"]["model"]["version"], MODEL_VERSION)
        self.assertEqual(trained, runner.predictor.training_timestamp)
        self.assertIs(c.predictor, neural)
        self.assertNotIn("sensor.predbat_load_ml_forecast", c.published)
        restarted = RegressionForecast(c)
        with patch.object(LoadRegressionPredictor, "train", side_effect=AssertionError("Saved model should not train again")):
            asyncio.run(restarted.update())
        self.assertEqual(restarted.status, "active")

    def test_runner_insufficient_history_and_unavailable_storage_are_safe(self):
        """Warm-up and a storage outage never publish a valid-looking zero forecast."""
        storage = KeyStorage()
        storage.offline = True
        c = self.component(storage, days=8)
        runner = RegressionForecast(c)
        asyncio.run(runner.update())
        self.assertEqual(runner.status, "insufficient_data")
        self.assertEqual(c.published["sensor.predbat_load_regression_forecast"]["attributes"]["results"], {})
        self.assertFalse(storage.saves)
        c.load_data, c.load_data_age_days = history(), 16
        asyncio.run(runner.update())
        self.assertEqual(runner.status, "active")
        self.assertFalse(storage.saves)

    def test_runner_data_definition_change_refits_only_regression(self):
        """A changed immersion exclusion cannot reuse incompatible regression coefficients."""
        c = self.component(KeyStorage())
        runner = RegressionForecast(c)
        asyncio.run(runner.update())
        first, signature = runner.predictor, runner.definition
        c.args["iboost_energy_subtract"] = False
        asyncio.run(runner.update())
        self.assertIsNot(runner.predictor, first)
        self.assertNotEqual(runner.definition, signature)
        self.assertEqual(runner.status, "active")

    def test_component_starts_regression_while_initial_neural_training_waits(self):
        """The real component's first pass publishes regression without starting neural training."""
        c = self.component(KeyStorage())
        c.ml_enable, c.initial_training_done = True, False
        c.last_train_time = None
        c.database_history_loaded, c.load_ml_database_days = True, 0
        c.data_ready, c.ml_min_days = True, 1
        c._do_fetch, c._do_training = AsyncMock(), AsyncMock()
        self.assertTrue(asyncio.run(LoadMLComponent.run(c, 0, True)))
        c._do_training.assert_not_awaited()
        self.assertEqual(c.published["sensor.predbat_load_regression_forecast"]["state"], "active")
        self.assertFalse(c.initial_training_done)

    def test_component_regular_cycle_keeps_neural_lifecycle(self):
        """An added regression forecast does not start extra neural training or suppress its publication."""
        c = self.component(KeyStorage())
        c.ml_enable, c.initial_training_done = True, True
        c.last_train_time = c.now_utc
        c.database_history_loaded, c.load_ml_database_days = True, 0
        c.data_ready, c.ml_min_days = True, 1
        c.base.prediction_started = False
        c._do_fetch, c._do_training = AsyncMock(), AsyncMock()
        events = []
        c._update_model_status = lambda: events.append("neural status")
        c._get_predictions = lambda *args: events.append("neural predict")
        c._publish_entity = lambda: events.append("neural publish")
        c.update_success_timestamp = lambda: events.append("healthy")
        self.assertTrue(asyncio.run(LoadMLComponent.run(c, 1800, False)))
        c._do_training.assert_not_awaited()
        self.assertEqual(events, ["neural status", "neural predict", "neural publish", "healthy"])
        self.assertFalse(c.load_ml_calculating)


def run_three_model_load_tests(my_predbat=None):
    """Run direct-model and production-selector tests through the normal test registry."""
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (ThreeModelTests, RegressionTests))
    return not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()
