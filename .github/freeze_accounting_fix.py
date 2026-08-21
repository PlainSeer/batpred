from pathlib import Path


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    p.write_text(text.replace(old, new, 1))
    print(f"patched {label}")


# Ensure the setting always exists on PredBat instances (main was missing the PR's reset default).
replace_once(
    "apps/predbat/predbat.py",
    "        self.battery_loss_discharge = 1.0\n        self.inverter_loss = 1.0\n        self.inverter_hybrid = True\n",
    "        self.battery_loss_discharge = 1.0\n        self.inverter_loss = 1.0\n        # Maximum battery-side discharge (W) that may continue to supply house load during Freeze Export.\n        self.inverter_freeze_export_discharge_rate = max(float(self.args.get(\"inverter_freeze_export_discharge_rate\", 0)), 0.0)\n        self.log(\"Freeze Export discharge rate configured: {:.0f} W\".format(self.inverter_freeze_export_discharge_rate))\n        self.inverter_hybrid = True\n",
    "PredBat reset default",
)

# Python predictor: remove the old 'lost energy' path and route the configured battery-side
# discharge through normal battery_draw -> inverter loss -> house/grid accounting.
replace_once(
    "apps/predbat/prediction.py",
    "            freeze_export_active = set_export_freeze and export_window_active and export_limit_now < 100.0 and (export_limit_now == 99.0 or set_export_freeze_only)\n",
    "",
    "Python obsolete freeze flag",
)

replace_once(
    "apps/predbat/prediction.py",
    "                    if battery_draw < 0:\n                        pv_dc = min(abs(battery_draw), pv_now)\n                        pv_ac = (pv_now - pv_dc) * inverter_loss_ac\n\n                battery_state = \"fz+\" if battery_draw < 0 else \"fz~\"\n",
    "                    if battery_draw < 0:\n                        pv_dc = min(abs(battery_draw), pv_now)\n                        pv_ac = (pv_now - pv_dc) * inverter_loss_ac\n\n                # Some inverters (observed on AlphaESS) still discharge the battery to supply\n                # house load during Freeze Export. The configured value is battery-side power,\n                # so convert it through the normal discharge-loss path and only use it where\n                # there is remaining house demand. This keeps the energy balance conserved and\n                # cannot create additional grid export.\n                if inverter_freeze_export_discharge_rate > 0 and battery_draw >= 0:\n                    freeze_house_demand = get_diff(battery_draw, pv_dc, pv_ac, load_yesterday, inverter_loss, inverter_loss_recp)\n                    if freeze_house_demand > 0:\n                        freeze_soc_available = min(inverter_freeze_export_discharge_rate * step / 60000.0, max(soc - reserve_expected, 0))\n                        freeze_draw_limit = freeze_soc_available * battery_loss_discharge\n                        freeze_draw_for_house = freeze_house_demand * inverter_loss_recp\n                        battery_draw = min(freeze_draw_limit, freeze_draw_for_house)\n\n                if battery_draw < 0:\n                    battery_state = \"fz+\"\n                elif battery_draw > 0:\n                    battery_state = \"fz-\"\n                else:\n                    battery_state = \"fz~\"\n",
    "Python Freeze Export house supply",
)

replace_once(
    "apps/predbat/prediction.py",
    "            # Some inverters continue to discharge the battery internally while Freeze Export\n            # blocks normal external battery flow. Model that measured battery-side discharge\n            # directly in SoC without creating fictitious house load or grid energy.\n            freeze_export_discharge_step = 0.0\n            if freeze_export_active and inverter_freeze_export_discharge_rate > 0:\n                freeze_export_discharge_step = min(inverter_freeze_export_discharge_rate * step / 60000.0, max(soc - reserve_expected, 0))\n                soc -= freeze_export_discharge_step\n\n",
    "",
    "Python lost-energy SoC deduction",
)

replace_once(
    "apps/predbat/prediction.py",
    "            # Count battery cycles, including internal Freeze Export battery discharge\n            battery_cycle = battery_cycle + abs(battery_draw) + freeze_export_discharge_step\n",
    "            # Count battery cycles; Freeze Export house supply is now part of battery_draw.\n            battery_cycle = battery_cycle + abs(battery_draw)\n",
    "Python cycle accounting",
)

# C++ kernel: exact behavioural mirror of the Python change.
replace_once(
    "apps/predbat/prediction_kernel.cpp",
    "#define PK_PARITY_REVISION 8\n",
    "#define PK_PARITY_REVISION 9\n",
    "C++ parity revision",
)

replace_once(
    "apps/predbat/prediction_kernel.cpp",
    "        const bool freeze_export_active = c->set_export_freeze && export_window_active && export_limit_now < 100.0 && (export_limit_now == 99.0 || c->set_export_freeze_only);\n",
    "",
    "C++ obsolete freeze flag",
)

replace_once(
    "apps/predbat/prediction_kernel.cpp",
    "                if (battery_draw < 0) {\n                    pv_dc = std::min(std::fabs(battery_draw), pv_now);\n                    pv_ac = (pv_now - pv_dc) * inverter_loss_ac;\n                }\n            }\n        } else {\n            // ECO Mode - prediction.py:951-997\n",
    "                if (battery_draw < 0) {\n                    pv_dc = std::min(std::fabs(battery_draw), pv_now);\n                    pv_ac = (pv_now - pv_dc) * inverter_loss_ac;\n                }\n            }\n\n            // Some inverters (observed on AlphaESS) still discharge the battery to supply\n            // house load during Freeze Export. The configured value is battery-side power;\n            // route it through normal discharge and inverter losses, capped by remaining load.\n            if (inverter_freeze_export_discharge_rate > 0 && battery_draw >= 0) {\n                const double freeze_house_demand = get_diff(battery_draw, pv_dc, pv_ac, load_yesterday, inverter_loss, inverter_loss_recp);\n                if (freeze_house_demand > 0) {\n                    const double freeze_soc_available = std::min(inverter_freeze_export_discharge_rate * step / 60000.0, std::max(soc - reserve_expected, 0.0));\n                    const double freeze_draw_limit = freeze_soc_available * battery_loss_discharge;\n                    const double freeze_draw_for_house = freeze_house_demand * inverter_loss_recp;\n                    battery_draw = std::min(freeze_draw_limit, freeze_draw_for_house);\n                }\n            }\n        } else {\n            // ECO Mode - prediction.py:951-997\n",
    "C++ Freeze Export house supply",
)

replace_once(
    "apps/predbat/prediction_kernel.cpp",
    "        // Internal, directional battery discharge while Freeze Export is active.\n        // This mirrors prediction.py and deliberately does not alter battery_draw/grid energy.\n        double freeze_export_discharge_step = 0.0;\n        if (freeze_export_active && inverter_freeze_export_discharge_rate > 0) {\n            freeze_export_discharge_step = std::min(inverter_freeze_export_discharge_rate * step / 60000.0, std::max(soc - reserve_expected, 0.0));\n            soc -= freeze_export_discharge_step;\n        }\n\n",
    "",
    "C++ lost-energy SoC deduction",
)

replace_once(
    "apps/predbat/prediction_kernel.cpp",
    "        // Count battery cycles - prediction.py:1094-1095\n        battery_cycle = battery_cycle + std::fabs(battery_draw) + freeze_export_discharge_step;\n",
    "        // Count battery cycles; Freeze Export house supply is now part of battery_draw.\n        battery_cycle = battery_cycle + std::fabs(battery_draw);\n",
    "C++ cycle accounting",
)

replace_once(
    "apps/predbat/prediction_kernel.py",
    "KERNEL_PARITY_REVISION = 8\n",
    "KERNEL_PARITY_REVISION = 9\n",
    "Python kernel parity revision",
)

# Temporary regression plumbing. These two test files are restored before the final clean commit;
# they exist only so the work branch validates the new semantics in both Python and C++ engines.
replace_once(
    "apps/predbat/tests/test_infra.py",
    "    inverter_loss=1.0,\n    battery_rate_max_charge=1.0,\n",
    "    inverter_loss=1.0,\n    inverter_freeze_export_discharge_rate=0.0,\n    battery_rate_max_charge=1.0,\n",
    "temporary test setting argument",
)
replace_once(
    "apps/predbat/tests/test_infra.py",
    "    assert_keep=0.0,\n    save=\"best\",\n",
    "    assert_keep=0.0,\n    assert_battery_cycle=None,\n    save=\"best\",\n",
    "temporary cycle assertion argument",
)
replace_once(
    "apps/predbat/tests/test_infra.py",
    "    my_predbat.inverter_loss = inverter_loss\n    my_predbat.battery_rate_max_charge = battery_rate_max_charge / 60.0\n",
    "    my_predbat.inverter_loss = inverter_loss\n    my_predbat.inverter_freeze_export_discharge_rate = inverter_freeze_export_discharge_rate\n    my_predbat.battery_rate_max_charge = battery_rate_max_charge / 60.0\n",
    "temporary test setting assignment",
)
replace_once(
    "apps/predbat/tests/test_infra.py",
    "    if abs(final_soc - assert_final_soc) >= 0.1:\n        if not ignore_failed:\n            print(\"ERROR: Final SOC {} should be {}\".format(final_soc, assert_final_soc))\n        failed = True\n    if abs(final_iboost - assert_final_iboost) >= 0.1:\n",
    "    if abs(final_soc - assert_final_soc) >= 0.1:\n        if not ignore_failed:\n            print(\"ERROR: Final SOC {} should be {}\".format(final_soc, assert_final_soc))\n        failed = True\n    if assert_battery_cycle is not None and abs(battery_cycle - assert_battery_cycle) >= 0.001:\n        if not ignore_failed:\n            print(\"ERROR: Battery cycle {} should be {}\".format(battery_cycle, assert_battery_cycle))\n        failed = True\n    if abs(final_iboost - assert_final_iboost) >= 0.1:\n",
    "temporary cycle assertion",
)

regression = '''    # Freeze Export discharge must supply real house load rather than disappear from SoC.\n    # Normal battery discharge is disabled in these cases so only the configured Freeze Export\n    # path is under test. 240 W for one hour is 0.24 kWh battery-side.\n    failed |= simple_scenario(\n        "freeze_export_house_supply_default_zero",\n        my_predbat,\n        1.0,\n        0,\n        assert_final_metric=10.0,\n        assert_final_soc=10.0,\n        battery_size=10.0,\n        battery_soc=10.0,\n        discharge=99,\n        end_record=60,\n        inverter_freeze_export_discharge_rate=0.0,\n        battery_rate_max_charge=0.0,\n        assert_battery_cycle=0.0,\n    )\n    failed |= simple_scenario(\n        "freeze_export_house_supply_240w_one_hour",\n        my_predbat,\n        1.0,\n        0,\n        assert_final_metric=7.6,\n        assert_final_soc=9.76,\n        battery_size=10.0,\n        battery_soc=10.0,\n        discharge=99,\n        end_record=60,\n        inverter_freeze_export_discharge_rate=240.0,\n        battery_rate_max_charge=0.0,\n        assert_battery_cycle=0.24,\n    )\n    failed |= simple_scenario(\n        "freeze_export_house_supply_respects_inverter_loss",\n        my_predbat,\n        1.0,\n        0,\n        assert_final_metric=8.08,\n        assert_final_soc=9.76,\n        battery_size=10.0,\n        battery_soc=10.0,\n        discharge=99,\n        end_record=60,\n        inverter_loss=0.8,\n        inverter_freeze_export_discharge_rate=240.0,\n        battery_rate_max_charge=0.0,\n        assert_battery_cycle=0.24,\n    )\n    failed |= simple_scenario(\n        "freeze_export_house_supply_no_load",\n        my_predbat,\n        0,\n        0,\n        assert_final_metric=0,\n        assert_final_soc=10.0,\n        battery_size=10.0,\n        battery_soc=10.0,\n        discharge=99,\n        end_record=60,\n        inverter_freeze_export_discharge_rate=240.0,\n        battery_rate_max_charge=0.0,\n        assert_battery_cycle=0.0,\n    )\n    failed |= simple_scenario(\n        "freeze_export_house_supply_not_outside_freeze",\n        my_predbat,\n        1.0,\n        0,\n        assert_final_metric=10.0,\n        assert_final_soc=10.0,\n        battery_size=10.0,\n        battery_soc=10.0,\n        discharge=100,\n        end_record=60,\n        inverter_freeze_export_discharge_rate=240.0,\n        battery_rate_max_charge=0.0,\n        assert_battery_cycle=0.0,\n    )\n    failed |= simple_scenario(\n        "freeze_export_house_supply_reserve_floor",\n        my_predbat,\n        1.0,\n        0,\n        assert_final_metric=9.0,\n        assert_final_soc=4.0,\n        battery_size=10.0,\n        battery_soc=4.1,\n        reserve=4.0,\n        discharge=99,\n        end_record=60,\n        inverter_freeze_export_discharge_rate=240.0,\n        battery_rate_max_charge=0.0,\n        assert_battery_cycle=0.1,\n    )\n    if failed:\n        return failed\n\n'''
replace_once(
    "apps/predbat/tests/test_model.py",
    "    failed = False\n    failed |= simple_scenario(\"zero\", my_predbat, 0, 0, 0, 0, with_battery=False)\n",
    "    failed = False\n" + regression + "    failed |= simple_scenario(\"zero\", my_predbat, 0, 0, 0, 0, with_battery=False)\n",
    "temporary Freeze Export regression cases",
)

print("all source and temporary regression patches applied")
