"""Focused regressions for rate history, model collapse and candidate acceptance."""

import asyncio
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from load_ml_component import LoadMLComponent, RATE_HISTORY_VERSION
from load_predictor import CHUNK_MINUTES, LOOKBACK_STEPS, MODEL_VERSION, LoadPredictor
from tests.test_load_ml_rollout import _collapsed_predictor, _spike_history


class LockedLogger:
    """Stand in for a logger attached to live connection state that owns a lock."""

    def __init__(self):
        """Keep a lock that would make an accidental deep copy fail."""
        self.lock = threading.Lock()
        self.messages = []

    def __call__(self, message):
        """Capture the component's diagnostic messages."""
        self.messages.append(message)


class LoadMLStabilityTests(unittest.TestCase):
    """Exercise production paths without running resource-heavy model training."""

    def setUp(self):
        """Create a minimal component around a fixed UTC clock."""
        self.now = datetime(2026, 11, 15, 13, 20, tzinfo=timezone.utc)
        self.component = LoadMLComponent.__new__(LoadMLComponent)
        self.component.base = SimpleNamespace(now_utc=self.now)
        self.component.log = lambda *args, **kwargs: None

    def test_published_rates_do_not_pad_old_history(self):
        """Yesterday's first published price must not overwrite older recorder data."""
        rates = {"2026-11-14T00:00:00+00:00": 6.6, "2026-11-15T05:30:00+00:00": 35.61, "2026-11-15T23:30:00+00:00": 6.6}
        data = self.component._rate_attribute_data(rates, 21)
        self.assertEqual(max(data), 2240)
        self.assertAlmostEqual(data[0], 35.61)
        self.assertAlmostEqual(data[-610], 6.6)
        merged = self.component._merge_channel({3000: 35.61, 4000: 54.23}, data)
        self.assertEqual(merged[3000], 35.61)
        self.assertEqual(merged[4000], 54.23)

    def test_zero_negative_and_invalid_prices(self):
        """Legitimate zero/negative rates remain usable; bad samples create no padded history."""
        data = self.component._rate_attribute_data({"2026-11-15T12:00:00+00:00": -2, "2026-11-15T13:00:00+00:00": 0, "2026-10-01T00:00:00+00:00": "unknown", "bad": 3}, 21)
        self.assertEqual(max(data), 80)
        self.assertEqual(data[0], 0)
        self.assertEqual(data[80], -2)
        self.assertEqual(self.component._rate_attribute_data({"bad": "unknown"}, 21), {})

    def test_rate_boundaries_use_utc_across_clock_change(self):
        """The repeated autumn hour must retain both prices in chronological order."""
        self.component.base.now_utc = datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
        data = self.component._rate_attribute_data({"2026-10-25T01:00:00+00:00": 20, "2026-10-25T01:00:00+01:00": 10}, 2)
        self.assertEqual(max(data), 120)
        self.assertEqual(data[120], 10)
        self.assertEqual(data[60], 20)

    def test_legacy_rate_migration_retains_other_channels(self):
        """Discard the legacy rate channels without losing accumulated load/PV/weather."""
        for rate_version in [None, RATE_HISTORY_VERSION]:
            with self.subTest(rate_version=rate_version), tempfile.TemporaryDirectory() as folder:
                metadata = {"version": 1, "step_minutes": 5, "saved_utc": self.now.isoformat(), "age_days": 3, "nan_sentinel": True, "iboost_subtracted": False}
                if rate_version is not None:
                    metadata["rate_history_version"] = rate_version
                path = os.path.join(folder, "history.npz")
                np.savez_compressed(path, metadata_json=np.array(json.dumps(metadata)), load=np.array([0.1, 0.2]), pv=np.array([0.0, 0.3]), temp=np.array([9.0, 8.0]), import_rate=np.array([6.6, 35.61]), export_rate=np.array([19.0, 20.0]))
                self.component.database_filepath = path
                self.component.iboost_subtract_active = lambda: False
                asyncio.run(self.component.load_database_history())
                self.assertEqual(self.component.load_data, {0: 0.1, 5: 0.2})
                self.assertEqual(self.component.temperature_data, {0: 9.0, 5: 8.0})
                self.assertEqual(bool(self.component.import_rates_data), rate_version is not None)

    def test_holdout_is_not_a_training_target(self):
        """Recent validation targets are disjoint from training and normalisation rows."""
        predictor = LoadPredictor(log_func=lambda *args: None)
        history = {minute: 0.05 for minute in range(0, 7 * 1440, 5)}
        dataset, _, _, validation, _ = predictor._create_dataset(history, self.now, validation_holdout_hours=48)
        self.assertGreater(len(dataset), 0)
        self.assertGreaterEqual(int(dataset.targets.min()), 48 * 60 // CHUNK_MINUTES)
        self.assertEqual(len(validation), 48 * 60 // CHUNK_MINUTES)
        self.assertGreater(len(dataset.channels[0]), LOOKBACK_STEPS)

    def test_normalisation_bounds_match_training_and_inference(self):
        """Materialised, gathered and inference features obey identical bounds."""
        predictor = LoadPredictor(log_func=lambda *args: None)
        history = {minute: 10.0 for minute in range(0, 7 * 1440, 5)}
        dataset, _, _, _, _ = predictor._create_dataset(history, self.now)
        predictor.feature_mean = np.zeros(dataset.shape[1], dtype=np.float32)
        predictor.feature_std = np.ones(dataset.shape[1], dtype=np.float32)
        raw = dataset[0].copy()
        dataset.mean = predictor.feature_mean
        dataset.std = predictor.feature_std
        expected = predictor._normalize_features(raw.reshape(1, -1))[0]
        np.testing.assert_allclose(dataset[0], expected)
        np.testing.assert_allclose(np.asarray(dataset)[0], expected)
        inplace = raw.reshape(1, -1).copy()
        np.testing.assert_allclose(predictor._normalize_features(inplace, in_place=True)[0], expected)
        self.assertLessEqual(float(np.max(np.abs(expected))), 5.0)

    def test_dead_hidden_layer_uses_observed_daily_pattern(self):
        """A bias-only network must not erase a repeated daily demand peak."""
        predictor = _collapsed_predictor(0.01)
        history = _spike_history(self.now)
        results = predictor.predict(history, self.now, self.now.replace(hour=0, minute=0))
        self.assertEqual(len(results), 576)
        deltas = np.diff([0.0] + list(results.values()))
        self.assertGreater(float(np.max(deltas)), 0.5)
        self.assertTrue(np.all(np.isfinite(deltas)))
        self.assertGreaterEqual(float(np.min(deltas)), 0.0)

    def test_candidate_requires_finite_rollout_and_improvement(self):
        """One-step accuracy cannot hide a failed or regressed autoregressive rollout."""
        model = SimpleNamespace(validation_mae=0.01, rollout_mae=0.02, pattern_mae=0.03, rollout_bias=0.0, pattern_bias=0.0)
        self.assertTrue(self.component._accept_candidate(model, 2.0, 0.025)[0])
        self.assertFalse(self.component._accept_candidate(model, 2.0, 0.015)[0])
        self.assertFalse(self.component._accept_candidate(model, 2.0, 0.025, previous_mae=0.019)[0])
        model.rollout_bias = 0.02
        self.assertFalse(self.component._accept_candidate(model, 2.0, 0.025)[0])
        model.rollout_bias = 0.0
        for value in [0.04, None, np.nan, np.inf]:
            model.rollout_mae = value
            self.assertFalse(self.component._accept_candidate(model, 2.0)[0])
        self.assertGreater(MODEL_VERSION, 11)

    def test_pattern_comparison_penalises_accumulated_bias(self):
        """Compare both forecasts using error plus bias, the training selection metric."""
        model = SimpleNamespace(validation_mae=0.01, rollout_mae=0.032, pattern_mae=0.03, rollout_bias=0.0, pattern_bias=0.01)
        self.assertTrue(self.component._accept_candidate(model, 2.0)[0])
        model.rollout_mae = 0.02
        model.rollout_bias = 0.03
        self.assertFalse(self.component._accept_candidate(model, 2.0)[0])
        model.pattern_bias = np.nan
        self.assertFalse(self.component._accept_candidate(model, 2.0)[0])

    def test_bias_metrics_survive_model_reload(self):
        """Restarting must preserve the bias evidence used for candidate promotion."""
        predictor = _collapsed_predictor(0.01)
        predictor.rollout_bias = 0.003
        predictor.pattern_bias = -0.005
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "model.npz")
            predictor.save(path)
            restored = LoadPredictor(log_func=lambda *args: None)
            self.assertTrue(restored.load(path))
        self.assertEqual(restored.rollout_bias, predictor.rollout_bias)
        self.assertEqual(restored.pattern_bias, predictor.pattern_bias)

    def test_checkpoint_selection_uses_feedback_forecast(self):
        """Improving one-step errors must not replace a better long-range checkpoint."""
        np.random.seed(17)
        predictor = LoadPredictor(log_func=lambda *args: None)
        history = {minute: 0.05 for minute in range(0, 5 * 1440, 5)}
        forward = predictor._forward
        validation_errors = []
        snapshots = []

        def validation(inputs, training=False):
            """Make teacher-forced validation improve on every epoch."""
            result = forward(inputs, training=training)
            if not training:
                error = 0.03 / (len(validation_errors) + 1)
                validation_errors.append(error)
                result = (np.full_like(result[0], error / predictor.target_std), *result[1:])
            return result

        def rollout(*args, **kwargs):
            """Make the later feedback checkpoint worse, despite its one-step gain."""
            snapshots.append([weight.copy() for weight in predictor.weights])
            return (0.01 if len(snapshots) == 1 else 0.2), 0.0, 0.3

        with patch.object(predictor, "_forward", validation), patch.object(predictor, "_ar_rollout_diagnostic", rollout):
            predictor.train(history, self.now, epochs=5, patience=10, validation_holdout_hours=24)
        self.assertGreaterEqual(len(snapshots), 2)
        self.assertLess(validation_errors[-1], validation_errors[0])
        for actual, expected in zip(predictor.weights, snapshots[0]):
            np.testing.assert_array_equal(actual, expected)
        self.assertAlmostEqual(float(predictor.validation_mae), validation_errors[0], places=5)

    def _training_component(self, initialized=True):
        """Build the real training wrapper with tiny model state and a logger that owns a lock."""
        component = self.component
        # A bound production logger owns connections/locks: copying it must be avoided.
        component.log = LockedLogger()
        component.predictor = LoadPredictor(log_func=component.log)
        component.predictor.model_initialized = initialized
        component.predictor.pattern_bias = 0.0
        component.predictor.weights = [np.array([[1.0]])]
        component.predictor.feature_mean = np.array([2.0])
        component.predictor.adam_m_weights = [np.array([[3.0]])]
        component.data_lock = asyncio.Lock()
        component.load_data = {0: 0.1, 5: 0.2}
        component.load_data_age_days = 21
        component.pv_data = component.temperature_data = None
        component.import_rates_data = component.export_rates_data = None
        for name, value in {
            "ml_epochs_initial": 1,
            "ml_epochs_update": 1,
            "ml_time_decay_days": 7,
            "ml_validation_holdout_hours": 48,
            "ml_patience_initial": 1,
            "ml_patience_update": 1,
            "ml_curriculum_max_passes": 4,
            "ml_curriculum_window_days": 7,
            "ml_curriculum_step_days": 1,
            "ml_validation_threshold": 2.0,
            "ml_retrain_interval_seconds": 7200,
        }.items():
            setattr(component, name, value)
        component.model_filepath = "not-written.npz"
        component.model_valid = initialized
        component.model_status = "active" if initialized else "not_initialized"
        component.last_train_time = None
        component.last_success_timestamp = None
        component.initial_training_done = initialized
        return component

    def test_rejected_or_failed_training_preserves_live_state(self):
        """Neither a regression nor a training exception may mutate or save the live model."""
        for raises in (False, True):
            with self.subTest(raises=raises):
                component = self._training_component()
                previous = component.predictor

                def train(candidate, *args, **kwargs):
                    """Mutate all candidate state, then fail or report a worse rollout."""
                    candidate.weights[0][0, 0] = 99
                    candidate.feature_mean[0] = 99
                    candidate.adam_m_weights[0][0, 0] = 99
                    candidate.validation_mae = 0.01
                    if raises:
                        raise RuntimeError("simulated training error")
                    return 0.01

                def rollout(candidate, *args, **kwargs):
                    """Score both candidates against the same snapshot."""
                    self.assertEqual(kwargs["validation_holdout_hours"], 48)
                    return (0.04 if candidate.weights[0][0, 0] == 99 else 0.02), 0.0, 0.03

                with patch.object(LoadPredictor, "train_curriculum", train), patch.object(LoadPredictor, "_ar_rollout_diagnostic", rollout), patch.object(LoadPredictor, "save") as save:
                    asyncio.run(component._do_training(is_initial=False))
                    save.assert_not_called()
                    self.assertIs(component.predictor, previous)
                    self.assertEqual(previous.weights[0][0, 0], 1)
                    self.assertEqual(previous.feature_mean[0], 2)
                    self.assertEqual(previous.adam_m_weights[0][0, 0], 3)
                    self.assertTrue(component.model_valid)

    def test_accepted_model_is_published_and_retry_is_throttled(self):
        """An improved isolated candidate can replace the model only once per interval."""
        component = self._training_component()
        previous = component.predictor
        calls = []

        def train(candidate, *args, **kwargs):
            """Return a better trained candidate and exercise its liveness callback."""
            calls.append(candidate)
            candidate.weights[0][0, 0] = 2
            candidate.validation_mae = 0.01
            kwargs["progress_callback"]()
            return 0.01

        def rollout(candidate, *args, **kwargs):
            """Give the candidate a lower error on the identical holdout."""
            return (0.01 if candidate.weights[0][0, 0] == 2 else 0.02), 0.0, 0.03

        with patch.object(LoadPredictor, "train_curriculum", train), patch.object(LoadPredictor, "_ar_rollout_diagnostic", rollout), patch.object(LoadPredictor, "save") as save:
            asyncio.run(component._do_training(is_initial=False))
            asyncio.run(component._do_training(is_initial=False))
            save.assert_called_once()
        self.assertEqual(len(calls), 1)
        self.assertIsNot(component.predictor, previous)
        self.assertIs(component.predictor.log, previous.log)
        self.assertEqual(previous.weights[0][0, 0], 1)
        self.assertTrue(component.model_valid)
        self.assertEqual(component.model_status, "active")
        self.assertIsNotNone(component.last_success_timestamp)

    def test_initial_bad_candidate_leaves_standard_fallback_available(self):
        """A cold start with failed validation must not publish untrusted weights."""
        component = self._training_component(initialized=False)
        previous = component.predictor

        def train(candidate, *args, **kwargs):
            """Produce finite one-step metrics but a failed long-range rollout."""
            candidate.model_initialized = True
            candidate.validation_mae = 0.01
            return 0.01

        with patch.object(LoadPredictor, "train_curriculum", train), patch.object(LoadPredictor, "_ar_rollout_diagnostic", return_value=(0.1, 0.0, 0.03)), patch.object(LoadPredictor, "save") as save:
            asyncio.run(component._do_training(is_initial=True))
            asyncio.run(component._do_training(is_initial=True))
            save.assert_not_called()
        self.assertIs(component.predictor, previous)
        self.assertFalse(component.model_valid)
        self.assertFalse(component.initial_training_done)
        self.assertEqual(component.model_status, "fallback_validation")


def run_load_ml_stability_tests(my_predbat=None):
    """Run the lightweight stability regressions through PredBat's normal test registry."""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LoadMLStabilityTests)
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=2).run(suite)
    for test, failure in result.failures + result.errors:
        print("ERROR:", test, failure)
    print("LoadML stability: {} tests, {} failures, {} errors".format(result.testsRun, len(result.failures), len(result.errors)))
    return not result.wasSuccessful()
