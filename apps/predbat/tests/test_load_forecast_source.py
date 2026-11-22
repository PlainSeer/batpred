# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
"""Source preferences, real planning cycles and evidence preservation across settings and restart."""

import asyncio
import copy
import pathlib
import unittest
from types import SimpleNamespace

from aiohttp import web as aiohttp_web
from load_forecast_source import source_settings, choose_planning_source
from tests.test_load_ml_guard_mode import GuardModeControlTests
from tests.test_load_forecast_guard_fetch import ThreeModelGuardFetchTests
from tests.test_load_forecast_guard import GuardBase, curve, paired_evidence
from tests.test_three_model_load import KeyStorage
from tests.test_web_load_ml_accuracy import accuracy_web, sample_attributes


class SourcePolicyTests(unittest.TestCase):
    """Check eligibility rules separately from training, availability validation and saved evidence."""

    def test_settings_preserve_legacy_defaults_and_ignore_overrides_outside_auto(self):
        """Only an explicit learned choice in automatic mode changes legacy planning permission."""
        self.assertEqual(source_settings("auto", "auto", "classic", False), ("auto", "classic", False))
        for mode in ("off", "monitor"):
            self.assertFalse(source_settings(mode, "regression", "ml", False)[2])
            self.assertTrue(source_settings(mode, "classic", "classic", True)[2])
        self.assertTrue(source_settings("auto", "regression", "classic", False)[2])
        self.assertTrue(source_settings("auto", "auto", "ml", False)[2])
        self.assertFalse(source_settings("auto", "classic", "ml", True)[2])
        self.assertEqual(source_settings("auto", [], {}, True), ("auto", "classic", True))

    def test_manual_selection_bypasses_accuracy_but_not_availability(self):
        """Manual selection permits a valid candidate; its disappearance falls back to historical."""
        available = {"ml": True, "regression": True, "classic": True}
        self.assertEqual(choose_planning_source("regression", "classic", "ml", "ml", available, {}), ("regression", "manual"))
        available["regression"] = False
        self.assertEqual(choose_planning_source("regression", "classic", "ml", "ml", available, {}), ("classic", "manual_unavailable"))

    def test_default_is_provisional_and_cannot_override_bad_evidence_or_earned_winner(self):
        """A preferred default fills an evidence gap without defeating automatic rejection or selection."""
        available = {"ml": True, "regression": True, "classic": True}
        self.assertEqual(choose_planning_source("auto", "regression", "classic", "classic", available, {"regression": "measuring"}), ("regression", "automatic_default"))
        for reason in ("historical_preferred", "ml_worse_than_classic", "model_stale"):
            self.assertEqual(choose_planning_source("auto", "regression", "classic", "classic", available, {"regression": reason}), ("classic", "automatic"))
        self.assertEqual(choose_planning_source("auto", "regression", "ml", "ml", available, {"regression": "measuring"}), ("ml", "automatic"))

    def test_missing_default_preserves_external_only_or_valid_last_resort(self):
        """Source preferences cannot turn an existing external-only configuration into empty load."""
        available = {"classic": True, "external": True}
        self.assertEqual(choose_planning_source("auto", "ml", "external", "classic", available, {}, True), ("external", "default_unavailable"))
        self.assertEqual(choose_planning_source("ml", "classic", "classic", "classic", {"regression": True}, {}), ("regression", "manual_unavailable"))

    def test_source_changes_and_restart_preserve_completed_and_pending_evidence(self):
        """Both stores retain their fingerprints, frozen windows and completed records through every choice."""
        base = GuardBase()
        base.args["load_ml_guard_mode"] = "auto"
        base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        now = int(base.now_utc.timestamp() // 60)
        guards = (base.load_forecast_guard, base.load_forecast_guard_regression)
        for guard in guards:
            guard.records.extend(paired_evidence(now, ml=26, classic=20, actual=20))
            guard.loaded, guard.dirty = True, True
        storage = KeyStorage()
        base.components = SimpleNamespace(get_component=lambda name: storage)
        originals = [copy.deepcopy(guard.serialise()) for guard in guards]
        for choice, default in (("ml", "classic"), ("regression", "classic"), ("classic", "ml"), ("auto", "regression")):
            base.args.update(load_forecast_source=choice, load_forecast_auto_default=default)
            base.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
            for guard, original in zip(guards, originals):
                self.assertEqual(guard.fingerprint, original["fingerprint"])
                self.assertEqual(guard.records, original["records"])
                self.assertIn(guard, (base.load_forecast_guard, base.load_forecast_guard_regression))
        restarted = GuardBase()
        restarted.args.update(base.args)
        restarted.components = base.components
        restarted.update_load_forecast_guard(curve(0.01), curve(0.01), curve(0.01))
        for guard, original in zip((restarted.load_forecast_guard, restarted.load_forecast_guard_regression), originals):
            self.assertEqual(guard.fingerprint, original["fingerprint"])
            self.assertCountEqual(guard.records, original["records"])
        self.assertEqual(set(storage.values), {"load_forecast_accuracy", "load_forecast_regression_accuracy"})

    def test_chart_distinguishes_manual_and_provisional_source_from_earned_permission(self):
        """Readable status reports actual policy without claiming that a chosen default earned selection."""
        for kind, headline in (("manual", "Manual source selection"), ("automatic_default", "Using automatic default"), ("manual_unavailable", "Requested source unavailable")):
            attrs = sample_attributes("measuring", "regression")
            attrs["selection_kind"] = kind
            page = accuracy_web(attrs, mode="auto")
            page.base.args.update(load_forecast_source="regression", load_forecast_auto_default="ml")
            html = page.get_load_ml_accuracy_html()
            self.assertIn(headline, html)
            self.assertIn('name="select__predbat_load_forecast_source"', html)
            self.assertIn('name="select__predbat_load_forecast_auto_default"', html)
            self.assertIn('<option value="regression" selected>Regression</option>', html)
            self.assertIn("Collecting evidence", html)
        stale = accuracy_web(attrs, mode="auto", age_minutes=20).get_load_ml_accuracy_html()
        self.assertIn("Report out of date", stale)
        self.assertNotIn("Requested source unavailable</div>", stale)


class SourceControlTests(GuardModeControlTests):
    """Exercise native select persistence with all three controls and real config callbacks."""

    def setUp(self):
        """Extend the isolated mode fixture with both source selects."""
        super().setUp()
        names = ("load_ml_guard_mode", "load_forecast_source", "load_forecast_auto_default")
        self.base.CONFIG_ITEMS = [copy.deepcopy(self.saved_state["config_index"][name]) for name in names]
        for item in self.base.CONFIG_ITEMS:
            item.pop("value", None)
            item.pop("entity", None)
            self.base.ha_interface.dummy_items.pop("select." + self.base.prefix + "_" + item["name"], None)
        self.base.load_user_config()
        self.source_field = self.base.config_index["load_forecast_source"]["entity"].replace(".", "__")
        self.default_field = self.base.config_index["load_forecast_auto_default"]["entity"].replace(".", "__")

    def test_source_form_replans_persists_and_does_not_reset_live_evidence(self):
        """Selecting a source schedules a plan while leaving evidence objects untouched across config reload."""
        sentinel = object()
        self.base.load_forecast_guard = sentinel
        self.base.plan_valid, self.base.update_pending = True, False
        reset = self.base.inverter_needs_reset
        self.assertEqual(self.submit({self.source_field: "regression", self.default_field: "ml", "_load_ml_guard_return_chart": "LoadML"}), "./charts?chart=LoadML")
        self.assertEqual(self.base.get_arg("load_forecast_source"), "regression")
        self.assertFalse(self.base.plan_valid)
        self.assertTrue(self.base.update_pending)
        self.assertIs(self.base.load_forecast_guard, sentinel)
        self.assertEqual(self.base.inverter_needs_reset, reset)
        for name in ("load_forecast_source", "load_forecast_auto_default"):
            item = self.base.config_index[name]
            item["value"] = None
            self.base.ha_interface.set_state(item["entity"], "unavailable")
        self.base.load_user_config(load_config=True)
        self.assertEqual(self.base.get_arg("load_forecast_source"), "regression")
        self.assertEqual(self.base.get_arg("load_forecast_auto_default"), "ml")
        self.assertIs(self.base.load_forecast_guard, sentinel)

    def test_invalid_source_rejects_entire_form_before_changes(self):
        """A malformed second select cannot partially change the first or its saved value."""
        saved = pathlib.Path(self.directory.name, "predbat_config.json").read_text()

        async def post():
            """Supply a good source followed by an invalid automatic default."""
            return {self.source_field: "regression", self.default_field: "auto"}

        with self.assertRaises(aiohttp_web.HTTPBadRequest):
            asyncio.run(self.web.html_config_post(SimpleNamespace(post=post)))
        self.assertEqual(self.base.get_arg("load_forecast_source"), "auto")
        self.assertEqual(pathlib.Path(self.directory.name, "predbat_config.json").read_text(), saved)


class SourceFetchTests(ThreeModelGuardFetchTests):
    """Check actual aggregate planning load, separate feeds and non-saving comparison cycles."""

    def select(self, choice="auto", default="classic"):
        """Use the same native values as the chart and Config controls."""
        self.base.expose_config("load_forecast_source", choice)
        self.base.expose_config("load_forecast_auto_default", default)
        self.configure()

    def test_each_explicit_source_supplies_real_plan_and_non_save_comparison(self):
        """Manual learned choices work with legacy planning off, without double-adding household load."""
        self.base.args["load_ml_source"] = False
        for choice, expected in (("ml", 1.2), ("regression", 0.48), ("classic", 0.6)):
            self.select(choice)
            self.base.fetch_sensor_data(save=True)
            self.assertEqual(self.base.load_forecast_guard_selected, choice)
            self.assertAlmostEqual(self.future_energy(), expected)
            self.assertEqual(len(self.base.load_forecast_array), 1)
            self.configure()
            self.base.fetch_sensor_data(save=False)
            self.assertAlmostEqual(self.future_energy(), expected)

    def test_auto_default_yields_to_earned_winner_and_bad_evidence(self):
        """An initial regression preference is provisional and reverts when the accuracy checks reject it."""
        self.select(default="regression")
        self.base.fetch_sensor_data(save=True)
        self.assertEqual(self.base.load_forecast_guard_selected, "regression")
        self.assertAlmostEqual(self.future_energy(), 0.48)
        self.qualify(neural=20, regression=26)
        self.configure()
        self.base.fetch_sensor_data(save=True)
        self.assertEqual(self.base.load_forecast_guard_selected, "ml")
        self.assertAlmostEqual(self.future_energy(), 1.2)
        self.qualify(neural=26, regression=26)
        self.configure()
        self.base.fetch_sensor_data(save=True)
        self.assertEqual(self.base.load_forecast_guard_selected, "classic")
        self.assertAlmostEqual(self.future_energy(), 0.6)

    def test_missing_manual_candidate_uses_non_empty_history(self):
        """A selected regression with no valid output cannot leave an empty planning series."""
        self.select("regression")
        self.regression = {}
        self.base.fetch_sensor_data(save=True)
        self.assertEqual(self.base.load_forecast_guard_selected, "classic")
        self.assertAlmostEqual(self.future_energy(), 0.6)

    def test_manual_sources_keep_external_feed_exactly_once(self):
        """Changing household models preserves an independently configured additional load."""
        from datetime import timedelta

        entity = "sensor.source_test_external_load"
        self.base.args["load_forecast"] = entity + "$results"
        data = {(self.base.midnight_utc + timedelta(minutes=m)).isoformat(): m * 0.003 for m in range(0, 5000, 5)}
        self.base.set_state_wrapper(entity, state="active", attributes={"results": data})
        for choice, expected in (("ml", 1.38), ("regression", 0.66), ("classic", 0.78)):
            self.select(choice)
            self.base.fetch_sensor_data(save=True)
            self.assertAlmostEqual(self.future_energy(), expected, places=3)
            self.assertEqual(len(self.base.load_forecast_array), 2)


def run_load_forecast_source_tests(my_predbat):
    """Run policy and production-integration checks with the standard isolated fixture."""
    SourceControlTests.base_fixture = SourceFetchTests.base_fixture = my_predbat
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case) for case in (SourcePolicyTests, SourceControlTests, SourceFetchTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    SourceControlTests.base_fixture = SourceFetchTests.base_fixture = None
    return not result.wasSuccessful()
