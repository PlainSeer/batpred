#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected exactly one match in {path}, found {count}")
    p.write_text(text.replace(old, new, 1))

# Main integration for the configurable Freeze Export battery-side discharge.
path = "apps/predbat/predbat.py"
replace_once(
    path,
    '''        self.battery_loss = 1.0
        self.battery_loss_discharge = 1.0
        self.inverter_loss = 1.0
        self.inverter_hybrid = True
''',
    '''        self.battery_loss = 1.0
        self.battery_loss_discharge = 1.0
        self.inverter_loss = 1.0
        # Battery-side discharge rate (W) observed while Freeze Export is active.
        # Prediction-only: inverter control is unchanged and energy follows normal house/grid accounting.
        self.inverter_freeze_export_discharge_rate = max(float(self.args.get("inverter_freeze_export_discharge_rate", 0)), 0.0)
        self.log("Freeze Export discharge rate configured: {:.0f} W".format(self.inverter_freeze_export_discharge_rate))
        self.inverter_hybrid = True
''',
)

path = "apps/predbat/tests/test_infra.py"
replace_once(
    path,
    '''    charge_limit_best=None,
    inverter_loss=1.0,
    battery_rate_max_charge=1.0,
''',
    '''    charge_limit_best=None,
    inverter_loss=1.0,
    inverter_freeze_export_discharge_rate=0.0,
    battery_rate_max_charge=1.0,
''',
)
replace_once(
    path,
    '''    keep=0.0,
    keep_weight=0.5,
    assert_keep=0.0,
    save="best",
''',
    '''    keep=0.0,
    keep_weight=0.5,
    assert_keep=0.0,
    assert_battery_cycle=None,
    save="best",
''',
)
replace_once(
    path,
    '''    my_predbat.reserve = reserve
    my_predbat.inverter_loss = inverter_loss
    my_predbat.battery_rate_max_charge = battery_rate_max_charge / 60.0
''',
    '''    my_predbat.reserve = reserve
    my_predbat.inverter_loss = inverter_loss
    my_predbat.inverter_freeze_export_discharge_rate = inverter_freeze_export_discharge_rate
    my_predbat.battery_rate_max_charge = battery_rate_max_charge / 60.0
''',
)
replace_once(
    path,
    '''    if abs(final_soc - assert_final_soc) >= 0.1:
        if not ignore_failed:
            print("ERROR: Final SOC {} should be {}".format(final_soc, assert_final_soc))
        failed = True
    if abs(final_iboost - assert_final_iboost) >= 0.1:
''',
    '''    if abs(final_soc - assert_final_soc) >= 0.1:
        if not ignore_failed:
            print("ERROR: Final SOC {} should be {}".format(final_soc, assert_final_soc))
        failed = True
    if assert_battery_cycle is not None and abs(battery_cycle - assert_battery_cycle) >= 0.001:
        if not ignore_failed:
            print("ERROR: Battery cycle {} should be {}".format(battery_cycle, assert_battery_cycle))
        failed = True
    if abs(final_iboost - assert_final_iboost) >= 0.1:
''',
)

path = "apps/predbat/tests/test_kernel_parity.py"
replace_once(
    path,
    '''    "inverter_hybrid",
    "inverter_loss",
    "inverter_limit",
''',
    '''    "inverter_hybrid",
    "inverter_loss",
    "inverter_freeze_export_discharge_rate",
    "inverter_limit",
''',
)
replace_once(
    path,
    '''    my_predbat.set_export_freeze = rng.choice([True, False])
    my_predbat.set_export_freeze_only = rng.choice([True, False, False, False])
    my_predbat.set_charge_window = rng.choice([True, True, False])
''',
    '''    my_predbat.set_export_freeze = rng.choice([True, False])
    my_predbat.set_export_freeze_only = rng.choice([True, False, False, False])
    # Exercise a non-zero value without consuming another RNG draw, preserving seeded scenarios.
    my_predbat.inverter_freeze_export_discharge_rate = 240.0 if my_predbat.set_export_freeze else 0.0
    my_predbat.set_charge_window = rng.choice([True, True, False])
''',
)

# Python predictor: feed the measured battery-side flow into normal house/grid energy accounting.
path = "apps/predbat/prediction.py"
old = '''            # Export limit, clip PV output
            diff = get_diff(battery_draw, pv_dc, pv_ac, load_yesterday, inverter_loss, inverter_loss_recp)
'''
new = '''            # Some inverters continue to supply house load from the battery during Freeze Export.
            # Feed the configured battery-side discharge through the normal battery/inverter
            # energy path so the energy is accounted for rather than disappearing from SoC.
            freeze_export_discharge_step = 0.0
            if freeze_export_active and inverter_freeze_export_discharge_rate > 0:
                freeze_export_discharge_step = min(
                    inverter_freeze_export_discharge_rate * step / 60000.0,
                    max((soc - reserve_expected) * battery_loss_discharge, 0),
                )
                battery_draw += freeze_export_discharge_step

            # Export limit, clip PV output
            diff = get_diff(battery_draw, pv_dc, pv_ac, load_yesterday, inverter_loss, inverter_loss_recp)
'''
replace_once(path, old, new)
replace_once(
    path,
    '''            # Some inverters continue to discharge the battery internally while Freeze Export
            # blocks normal external battery flow. Model that measured battery-side discharge
            # directly in SoC without creating fictitious house load or grid energy.
            freeze_export_discharge_step = 0.0
            if freeze_export_active and inverter_freeze_export_discharge_rate > 0:
                freeze_export_discharge_step = min(inverter_freeze_export_discharge_rate * step / 60000.0, max(soc - reserve_expected, 0))
                soc -= freeze_export_discharge_step

''',
    '',
)
replace_once(
    path,
    '            # Count battery cycles, including internal Freeze Export battery discharge\n            battery_cycle = battery_cycle + abs(battery_draw) + freeze_export_discharge_step\n',
    '            # Count battery cycles\n            battery_cycle = battery_cycle + abs(battery_draw)\n',
)

# C++ kernel mirror.
path = "apps/predbat/prediction_kernel.cpp"
replace_once(path, '#define PK_PARITY_REVISION 8\n', '#define PK_PARITY_REVISION 9\n')
replace_once(
    path,
    '    double inverter_freeze_export_discharge_rate; // W, internal battery discharge while Freeze Export is active\n',
    '    double inverter_freeze_export_discharge_rate; // W, battery-side discharge while Freeze Export is active\n',
)
old = '''        // Export limit, clip PV output - prediction.py:1051-1058
        double diff = get_diff(battery_draw, pv_dc, pv_ac, load_yesterday, inverter_loss, inverter_loss_recp);
'''
new = '''        // Some inverters continue to supply house load from the battery during Freeze Export.
        // Feed the configured battery-side discharge through the normal energy path.
        double freeze_export_discharge_step = 0.0;
        if (freeze_export_active && inverter_freeze_export_discharge_rate > 0) {
            freeze_export_discharge_step = std::min(
                inverter_freeze_export_discharge_rate * step / 60000.0,
                std::max((soc - reserve_expected) * battery_loss_discharge, 0.0));
            battery_draw += freeze_export_discharge_step;
        }

        // Export limit, clip PV output - prediction.py:1051-1058
        double diff = get_diff(battery_draw, pv_dc, pv_ac, load_yesterday, inverter_loss, inverter_loss_recp);
'''
replace_once(path, old, new)
replace_once(
    path,
    '''        // Internal, directional battery discharge while Freeze Export is active.
        // This mirrors prediction.py and deliberately does not alter battery_draw/grid energy.
        double freeze_export_discharge_step = 0.0;
        if (freeze_export_active && inverter_freeze_export_discharge_rate > 0) {
            freeze_export_discharge_step = std::min(inverter_freeze_export_discharge_rate * step / 60000.0, std::max(soc - reserve_expected, 0.0));
            soc -= freeze_export_discharge_step;
        }

''',
    '',
)
replace_once(
    path,
    '        battery_cycle = battery_cycle + std::fabs(battery_draw) + freeze_export_discharge_step;\n',
    '        battery_cycle = battery_cycle + std::fabs(battery_draw);\n',
)
replace_once(
    "apps/predbat/prediction_kernel.py",
    'KERNEL_PARITY_REVISION = 8\n',
    'KERNEL_PARITY_REVISION = 9\n',
)

# Regression coverage: no-op default, house supply, outside-freeze, reserve floor and cycle accounting.
path = "apps/predbat/tests/test_model.py"
anchor = '''    failed = False
    failed |= simple_scenario("zero", my_predbat, 0, 0, 0, 0, with_battery=False)
'''
replacement = '''    failed = False
    # Freeze Export battery discharge regression coverage. The configured battery-side flow
    # must supply house load through normal inverter accounting and still respect reserve/cycling.
    failed |= simple_scenario(
        "freeze_export_discharge_rate_default_zero",
        my_predbat,
        0,
        0,
        assert_final_metric=0,
        assert_final_soc=10.0,
        battery_size=10.0,
        battery_soc=10.0,
        discharge=99,
        end_record=60,
        inverter_freeze_export_discharge_rate=0.0,
        assert_battery_cycle=0.0,
    )
    failed |= simple_scenario(
        "freeze_export_discharge_rate_240w_supplies_house",
        my_predbat,
        1.0,
        0,
        # 240 W battery-side becomes 192 W AC at 80% inverter efficiency,
        # leaving 0.808 kWh imported during this one-hour test.
        assert_final_metric=8.08,
        assert_final_soc=9.76,
        battery_size=10.0,
        battery_soc=10.0,
        inverter_loss=0.8,
        discharge=99,
        end_record=60,
        inverter_freeze_export_discharge_rate=240.0,
        assert_battery_cycle=0.24,
    )
    failed |= simple_scenario(
        "freeze_export_discharge_rate_not_outside_freeze",
        my_predbat,
        0,
        0,
        assert_final_metric=0,
        assert_final_soc=10.0,
        battery_size=10.0,
        battery_soc=10.0,
        discharge=100,
        end_record=60,
        inverter_freeze_export_discharge_rate=240.0,
        assert_battery_cycle=0.0,
    )
    failed |= simple_scenario(
        "freeze_export_discharge_rate_reserve_floor",
        my_predbat,
        0,
        0,
        assert_final_metric=0,
        assert_final_soc=4.0,
        battery_size=10.0,
        battery_soc=4.1,
        reserve=4.0,
        discharge=99,
        end_record=60,
        inverter_freeze_export_discharge_rate=240.0,
        assert_battery_cycle=0.1,
    )
    if failed:
        return failed

    failed |= simple_scenario("zero", my_predbat, 0, 0, 0, 0, with_battery=False)
'''
replace_once(path, anchor, replacement)
PY

python -m pip install --upgrade pip
pip install -r requirements.txt
bash apps/predbat/build_kernel.sh
(
  cd coverage
  PREDBAT_KERNEL_REQUIRED=1 python3 ../apps/predbat/unit_test.py --quick
  python3 ../apps/predbat/verify_kernel_binary.py ../apps/predbat/prediction_kernel_lib_x86_64.so
)
bash apps/predbat/build_kernel_cross.sh
