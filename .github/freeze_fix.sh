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
old = '''            # Some inverters continue to discharge the battery internally while Freeze Export
            # blocks normal external battery flow. Model that measured battery-side discharge
            # directly in SoC without creating fictitious house load or grid energy.
            freeze_export_discharge_step = 0.0
            if freeze_export_active and inverter_freeze_export_discharge_rate > 0:
                freeze_export_discharge_step = min(inverter_freeze_export_discharge_rate * step / 60000.0, max(soc - reserve_expected, 0))
                soc -= freeze_export_discharge_step

'''
replace_once(path, old, '')
replace_once(
    path,
    '            # Count battery cycles, including internal Freeze Export battery discharge\n            battery_cycle = battery_cycle + abs(battery_draw) + freeze_export_discharge_step\n',
    '            # Count battery cycles\n            battery_cycle = battery_cycle + abs(battery_draw)\n',
)

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
old = '''        // Internal, directional battery discharge while Freeze Export is active.
        // This mirrors prediction.py and deliberately does not alter battery_draw/grid energy.
        double freeze_export_discharge_step = 0.0;
        if (freeze_export_active && inverter_freeze_export_discharge_rate > 0) {
            freeze_export_discharge_step = std::min(inverter_freeze_export_discharge_rate * step / 60000.0, std::max(soc - reserve_expected, 0.0));
            soc -= freeze_export_discharge_step;
        }

'''
replace_once(path, old, '')
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

# Main already contains the feature but did not carry the dedicated model tests from the PR branch.
# Insert the regression block before the existing baseline scenarios.
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

# Restore normal CI and remove this temporary runner before the real main commit.
git checkout 8aff023194e0ff0f99937847cab0457c34941d0b -- .github/workflows/code-quality.yml
rm -f .github/freeze_fix.sh

python -m pip install --upgrade pip
pip install -r requirements.txt
bash apps/predbat/build_kernel.sh
(
  cd coverage
  PREDBAT_KERNEL_REQUIRED=1 python3 ../apps/predbat/unit_test.py --quick
  python3 ../apps/predbat/verify_kernel_binary.py ../apps/predbat/prediction_kernel_lib_x86_64.so
)
bash apps/predbat/build_kernel_cross.sh

# Save the three source changes common to main and the existing PR branch.
git diff --binary -- \
  apps/predbat/prediction.py \
  apps/predbat/prediction_kernel.cpp \
  apps/predbat/prediction_kernel.py > /tmp/freeze-source-fix.patch

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add -A
git commit -m "fix(prediction): account Freeze Export discharge as house supply"
MAIN_FIX_SHA="$(git rev-parse HEAD)"

git fetch origin feature/freeze-export-loss
git switch -c feature-fix origin/feature/freeze-export-loss
git apply --3way /tmp/freeze-source-fix.patch

# The PR branch already has the Freeze Export test block; update its wording/house-supply case.
python - <<'PY'
from pathlib import Path

def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected exactly one PR-branch test match in {path}, found {count}")
    p.write_text(text.replace(old, new, 1))

path = "apps/predbat/tests/test_model.py"
replace_once(
    path,
    '    # Freeze Export internal battery discharge regression coverage. 240 W for one hour\n    # is 0.24 kWh. It applies only in Freeze Export, respects reserve, and counts as cycling.\n',
    '    # Freeze Export battery discharge regression coverage. The configured battery-side flow\n    # must supply house load through normal inverter accounting and still respect reserve/cycling.\n',
)
old = '''    failed |= simple_scenario(
        "freeze_export_discharge_rate_240w_one_hour",
        my_predbat,
        0,
        0,
        assert_final_metric=0,
        assert_final_soc=9.76,
        battery_size=10.0,
        battery_soc=10.0,
        discharge=99,
        end_record=60,
        inverter_freeze_export_discharge_rate=240.0,
        assert_battery_cycle=0.24,
    )
'''
new = '''    failed |= simple_scenario(
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
'''
replace_once(path, old, new)
PY

# Rebuild and test on the actual PR branch before committing it.
bash apps/predbat/build_kernel.sh
(
  cd coverage
  PREDBAT_KERNEL_REQUIRED=1 python3 ../apps/predbat/unit_test.py --quick
  python3 ../apps/predbat/verify_kernel_binary.py ../apps/predbat/prediction_kernel_lib_x86_64.so
)
bash apps/predbat/build_kernel_cross.sh

git add -A
git commit -m "fix(prediction): account Freeze Export discharge as house supply"

# Nothing reaches either real branch unless every step above succeeded.
git push --atomic origin "${MAIN_FIX_SHA}:main" feature-fix:feature/freeze-export-loss
