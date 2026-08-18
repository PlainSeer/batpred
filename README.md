# Predbat

![image](https://github.com/springfall2008/batpred/actions/workflows/code-quality.yml/badge.svg)
![image](https://github.com/springfall2008/batpred/actions/workflows/publish-docs.yml/badge.svg)
![image](https://github.com/springfall2008/batpred/actions/workflows/pages/pages-build-deployment/badge.svg)

## Introduction

Home battery prediction and automatic charging for Home Assistant supporting multiple inverters, including GivEnergy, Solis, Huawei, SolarEdge, SigEnergy, FoxESS, Sofar, Tesla Powerwall and many more.

Also known by some as Batpred or Batman!

![icon](https://github.com/springfall2008/batpred/assets/48591903/7c207423-1423-4f88-beb2-d1da5cfbfeeb) ![image](https://github.com/springfall2008/batpred/assets/48591903/e98a0720-d2cf-4b71-94ab-97fe09b3cee1)

## PlainSeer fork changes

This fork currently carries a small prediction-only patch for inverters that continue to consume battery energy while PredBat is using **Freeze Export**.

### Why this patch exists

On some inverter/battery systems, including the AlphaESS setup this fork is being tested against, Freeze Export prevents normal battery discharge to the house/grid but does not make the battery completely idle. A small amount of internal battery energy can still be consumed while the freeze is active.

Without modelling that loss, PredBat can hold the predicted battery SoC artificially flat during a long Freeze Export period, or otherwise overestimate the SoC available later in the plan.

### What changed

A new optional `apps.yaml` setting has been added:

```yaml
inverter_freeze_export_loss: 260
```

The value is in **watts** and must be supplied as a scalar value, not a YAML list.

When Freeze Export is active, PredBat now subtracts the configured internal loss directly from predicted battery SoC. The loss is deliberately not treated as house load or grid export, because it represents internal battery/inverter energy consumption rather than energy delivered elsewhere.

The same behaviour is implemented in both prediction engines:

- Python `prediction.py`
- C++ `prediction_kernel.cpp`

The prediction-kernel ABI/parity versions were bumped and the platform binaries rebuilt so Python and C++ predictions remain consistent.

The internal Freeze Export loss is also included in PredBat battery-cycle accounting.

### Safety / scope

This patch changes **prediction modelling only**. It does not alter inverter commands, charging behaviour, export control, Home Assistant automations, or AlphaESS Modbus control logic.

The default is `0`, so installations that do not configure `inverter_freeze_export_loss` retain the original PredBat behaviour.

Negative configured values are clamped to `0`.

### Startup diagnostic

For easier verification, PredBat logs the parsed value once during startup/reset. For example, with the configuration above the log will contain:

```text
Freeze Export loss configured: 260 W
```

This confirms that PredBat has read the scalar setting successfully before predictions begin.

### Current test value

The current AlphaESS test configuration uses:

```yaml
inverter_freeze_export_loss: 260
```

This value is intended to be calibrated against observed SoC decline during real Freeze Export periods and may be adjusted as more data is collected.

If you want to buy me a beer, then please use [Paypal](https://paypal.me/predbat?country.x=GB&locale.x=en_GB) or [GitHub sponsor](https://github.com/springfall2008)
![image](https://github.com/springfall2008/batpred/assets/48591903/b3a533ef-0862-4e0b-b272-30e254f58467)

* Use my referral code for Octopus Energy: <https://share.octopus.energy/jolly-eel-176>
* Use my referral code for Axle Energy (UK): <https://vpp.axle.energy/landing/grid?ref=R-VWIICRSA>

If you find Home Assistant and Predbat too difficult to set up yourself, there is now [PredBat Cloud](https://predbat.com/), a paid version of Predbat hosted in the cloud. Please note that while I have given permission for PredBat Cloud to operate under license, PredBat will remain open source for personal use.

## Predbat documentation

You can find the latest Predbat documentation at [https://springfall2008.github.io/batpred/](https://springfall2008.github.io/batpred/) and
how-to videos on my [YouTube channel](https://www.youtube.com/@springfall2008).

The documentation covers how Predbat works and how to get it installed
and configured, video tutorials and FAQs to help you get going.
It also explains how you can contribute to the project.

## Support

For support, please raise a GitHub ticket or use the Facebook Group: [Predbat](https://www.facebook.com/groups/1477599886299106)

Some inverters have their own groups also, e.g.:

* [GivTCP](https://www.facebook.com/groups/615579009972782)
* [Solis](https://www.facebook.com/groups/288045168816481)

## License

Please see [License](https://github.com/springfall2008/batpred/blob/main/License.md)

```text
Copyright (c) Trefor Southwell 2025-2026 - All rights reserved
This software may be used at no cost for personal use only.
No warranty is given, either expressed or implied.
```
