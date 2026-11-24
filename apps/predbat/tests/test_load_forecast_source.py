"""Two-source preferences and upgrade compatibility through real config/fetch paths."""

import asyncio
import copy
import pathlib
import unittest
from datetime import timedelta
from types import SimpleNamespace

from aiohttp import web as aiohttp_web
from load_forecast_source import source_settings, choose_planning_source, SOURCE_LABELS, DEFAULT_SOURCE_OPTIONS
from tests.test_load_ml_guard_mode import GuardModeControlTests
from tests.test_load_forecast_guard_fetch import GuardFetchTests
from tests.test_load_forecast_guard import GuardBase, curve, paired_evidence, FakeStorage, Components
from tests.test_web_load_ml_accuracy import accuracy_web, sample_attributes


class SourcePolicyTests(unittest.TestCase):
    """Verify retired settings, candidate availability and preserved neural evidence."""

    def test_retired_choices_become_automatic_with_historical_default(self):
        """Removal cannot grant neural permission or leave an unsupported source selected."""
        self.assertEqual(tuple(SOURCE_LABELS), ("auto", "classic", "ml"))
        self.assertEqual(DEFAULT_SOURCE_OPTIONS, ("classic", "ml"))
        self.assertEqual(source_settings("auto", "regression", "regression", False), ("auto", "classic", False))
        self.assertEqual(source_settings("auto", [], {}, True), ("auto", "classic", True))
        for mode in ("off", "monitor"):
            self.assertFalse(source_settings(mode, "ml", "ml", False)[2])
        self.assertTrue(source_settings("auto", "ml", "classic", False)[2])
        self.assertTrue(source_settings("auto", "auto", "ml", False)[2])
        self.assertFalse(source_settings("auto", "classic", "ml", True)[2])

    def test_manual_selection_never_bypasses_availability(self):
        """A manual neural choice falls back to Historical when output is unavailable."""
        sources = {"ml": True, "classic": True}
        self.assertEqual(choose_planning_source("ml", "classic", "classic", "classic", sources, {}), ("ml", "manual"))
        sources["ml"] = False
        self.assertEqual(choose_planning_source("ml", "classic", "classic", "classic", sources, {}), ("classic", "manual_unavailable"))

    def test_default_cannot_override_established_poor_performance(self):
        """A learned default is provisional and yields to demonstrated Historical preference."""
        sources = {"ml": True, "classic": True}
        self.assertEqual(choose_planning_source("auto", "ml", "classic", "classic", sources, {"ml": "measuring"}), ("ml", "automatic_default"))
        for reason in ("historical_preferred", "ml_worse_than_classic", "model_stale"):
            self.assertEqual(choose_planning_source("auto", "ml", "classic", "classic", sources, {"ml": reason}), ("classic", "automatic"))
        self.assertEqual(choose_planning_source("auto", "classic", "ml", "ml", sources, {}), ("ml", "automatic"))

    def test_missing_default_preserves_external_only_feed(self):
        """Source preferences cannot leave the configured external-only plan empty."""
        self.assertEqual(choose_planning_source("auto", "ml", "external", "classic", {"classic": True, "external": True}, {}, True), ("external", "default_unavailable"))

    def test_source_changes_and_restart_preserve_existing_evidence(self):
        """Frozen predictions, completed actuals, fingerprints and pending windows survive removal."""
        base = GuardBase()
        base.args["load_ml_guard_mode"] = "auto"
        base.update_load_forecast_guard(curve(0.01), curve(0.01))
        guard = base.load_forecast_guard
        guard.records.extend(paired_evidence(int(base.now_utc.timestamp() // 60), ml=26, classic=20, actual=20))
        guard.loaded, guard.dirty = True, True
        storage = FakeStorage()
        base.components = Components(storage)
        original = copy.deepcopy(guard.serialise())
        for choice, default in (("ml", "classic"), ("classic", "ml"), ("auto", "ml"), ("regression", "regression")):
            base.args.update(load_forecast_source=choice, load_forecast_auto_default=default)
            base.update_load_forecast_guard(curve(0.01), curve(0.01))
            self.assertEqual(guard.fingerprint, original["fingerprint"])
            self.assertEqual(guard.records, original["records"])
        fresh = GuardBase()
        fresh.args.update(base.args)
        fresh.components = base.components
        fresh.update_load_forecast_guard(curve(0.01), curve(0.01))
        self.assertEqual(fresh.load_forecast_guard.fingerprint, original["fingerprint"])
        self.assertCountEqual(fresh.load_forecast_guard.records, original["records"])
        self.assertEqual(base.dashboard["sensor.predbat_load_ml_accuracy"]["attributes"]["models"], ["classic", "ml"])

    def test_chart_keeps_manual_controls_without_retired_options(self):
        """The form explains policy without presenting Regression as a usable candidate."""
        for kind, headline in (("manual", "Manual source selection"), ("automatic_default", "Using automatic default"), ("manual_unavailable", "Requested source unavailable")):
            attrs = sample_attributes("measuring", "ml")
            attrs["selection_kind"] = kind
            page = accuracy_web(attrs, mode="auto")
            page.base.args.update(load_forecast_source="ml", load_forecast_auto_default="ml")
            html = page.get_load_ml_accuracy_html()
            self.assertIn(headline, html)
            self.assertIn('<option value="ml" selected>LoadML</option>', html)
            self.assertNotIn("Regression", html)


class SourceControlTests(GuardModeControlTests):
    """Check real native-select persistence and migration from removed choices."""

    def setUp(self):
        """Extend the mode fixture with both planning source settings."""
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

    def test_source_form_persists_without_resetting_live_evidence(self):
        """Selecting a supported source replans and survives restart with the guard untouched."""
        sentinel = object()
        self.base.load_forecast_guard = sentinel
        self.base.plan_valid, self.base.update_pending = True, False
        self.assertEqual(self.submit({self.source_field: "ml", self.default_field: "ml", "_load_ml_guard_return_chart": "LoadML"}), "./charts?chart=LoadML")
        self.assertFalse(self.base.plan_valid)
        self.assertTrue(self.base.update_pending)
        for name in ("load_forecast_source", "load_forecast_auto_default"):
            item = self.base.config_index[name]
            item["value"] = None
            self.base.ha_interface.set_state(item["entity"], "unavailable")
        self.base.load_user_config(load_config=True)
        self.assertEqual(self.base.get_arg("load_forecast_source"), "ml")
        self.assertEqual(self.base.get_arg("load_forecast_auto_default"), "ml")
        self.assertIs(self.base.load_forecast_guard, sentinel)

    def test_retired_saved_values_migrate_before_options_are_exposed(self):
        """Old Regression values cannot be appended back into the remaining native selects."""
        for name in ("load_forecast_source", "load_forecast_auto_default"):
            self.base.config_index[name]["value"] = "regression"
        self.base.save_current_config()
        for name in ("load_forecast_source", "load_forecast_auto_default"):
            item = self.base.config_index[name]
            item["value"] = None
            self.base.ha_interface.set_state(item["entity"], "unavailable")
        self.base.load_user_config(load_config=True)
        self.assertEqual(self.base.get_arg("load_forecast_source"), "auto")
        self.assertEqual(self.base.get_arg("load_forecast_auto_default"), "classic")
        for name in ("load_forecast_source", "load_forecast_auto_default"):
            self.assertNotIn("regression", self.base.config_index[name]["options"])

    def test_stale_or_invalid_forms_are_rejected_atomically(self):
        """No partial setting change occurs when a submitted source has been retired."""
        saved = pathlib.Path(self.directory.name, "predbat_config.json").read_text()
        for fields in ({self.source_field: "regression", self.default_field: "classic"}, {self.source_field: "ml", self.default_field: "auto"}):

            async def post():
                """Supply each untrusted form through the real config handler."""
                return fields

            with self.assertRaises(aiohttp_web.HTTPBadRequest):
                asyncio.run(self.web.html_config_post(SimpleNamespace(post=post)))
        self.assertEqual(self.base.get_arg("load_forecast_source"), "auto")
        self.assertEqual(pathlib.Path(self.directory.name, "predbat_config.json").read_text(), saved)


class SourceFetchTests(GuardFetchTests):
    """Exercise both remaining sources through actual aggregate forecast generation."""

    def select(self, choice="auto", default="classic"):
        """Set native selectors through the same interface as the chart."""
        self.base.expose_config("load_forecast_source", choice)
        self.base.expose_config("load_forecast_auto_default", default)
        self.configure()

    def test_explicit_sources_supply_plan_and_non_saving_comparison(self):
        """Manual source selection remains usable when the legacy planning flag is off."""
        self.base.args["load_ml_source"] = False
        for choice, expected in (("ml", 1.2), ("classic", 0.6)):
            self.select(choice)
            self.base.fetch_sensor_data(save=True)
            self.assertEqual(self.base.load_forecast_guard_selected, choice)
            self.assertAlmostEqual(self.future_energy(), expected)
            self.assertEqual(len(self.base.load_forecast_array), 1)
            self.configure()
            self.base.fetch_sensor_data(save=False)
            self.assertAlmostEqual(self.future_energy(), expected)

    def test_neural_default_yields_to_poor_evidence(self):
        """A provisional default cannot override established worse performance."""
        self.select(default="ml")
        self.base.fetch_sensor_data(save=True)
        self.assertEqual(self.base.load_forecast_guard_selected, "ml")
        self.base.load_forecast_guard.records = paired_evidence(int(self.base.now_utc.timestamp() // 60), ml=26, classic=20, actual=20)
        self.configure()
        self.base.fetch_sensor_data(save=True)
        self.assertEqual(self.base.load_forecast_guard_selected, "classic")

    def test_missing_manual_source_leaves_non_empty_history(self):
        """Unavailable neural output cannot create an empty household load forecast."""
        self.select("ml")
        self.base.fetch_ml_load_forecast.return_value = {}
        self.base.fetch_sensor_data(save=True)
        self.assertEqual(self.base.load_forecast_guard_selected, "classic")
        self.assertAlmostEqual(self.future_energy(), 0.6)

    def test_manual_sources_preserve_additional_external_feed(self):
        """Independent configured load remains present exactly once after switching sources."""
        entity = "sensor.source_test_external_load"
        self.base.args["load_forecast"] = entity + "$results"
        data = {(self.base.midnight_utc + timedelta(minutes=m)).isoformat(): m * 0.003 for m in range(0, 5000, 5)}
        self.base.set_state_wrapper(entity, state="active", attributes={"results": data})
        for choice, expected in (("ml", 1.38), ("classic", 0.78)):
            self.select(choice)
            self.base.fetch_sensor_data(save=True)
            self.assertAlmostEqual(self.future_energy(), expected, places=3)
            self.assertEqual(len(self.base.load_forecast_array), 2)


def run_load_forecast_source_tests(my_predbat):
    """Run policy, controls and fetch integration tests with the standard fixture."""
    SourceControlTests.base_fixture = SourceFetchTests.base_fixture = my_predbat
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (SourcePolicyTests, SourceControlTests, SourceFetchTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return not result.wasSuccessful()
