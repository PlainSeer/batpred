# LoadML stability changes in this fork

This branch starts from the official PredBat v9.8.0 release and adds focused
LoadML changes. It uses the official interface and does not carry the fork's
modern-interface additions or unreleased backend changes from another fork.

## What changes

- Published tariff results no longer overwrite older recorded prices with a
  padded copy of their oldest price.
- The old cached price channels are rebuilt from available recorded history.
  Saved household usage, solar generation and temperature history are retained.
- Recent validation targets are excluded from training and fitted statistics.
- Training selects weights using a full feedback forecast, including its bias,
  rather than selecting solely on one-step accuracy. These checks run after the
  first epoch, every fifth epoch and the final epoch; they use past data only.
- A separately trained candidate replaces the current model only when its
  validation is finite, its rollout error plus bias beats the historical pattern, and its
  neither rollout accuracy nor error plus bias worsens the previous model on the same data.
  Rejected attempts wait for the normal retraining interval before retrying.
- Normalised inputs are bounded consistently during training and prediction.
  If the final hidden layer closes completely, prediction uses the observed
  daily pattern instead of repeating the network's output bias.

## First run and fallback

Model version 12 deliberately retrains rather than reusing version 10 or the
previous experimental version 11 weights. Training can take longer because it
checks the actual forecast horizon. Maximum configured epoch budgets and the
per-epoch health update are retained.

If no initial candidate passes validation, the existing standard PredBat load
forecast remains the planning fallback. A rejected update retains the previous
model. The usual model-age limit still applies. These checks reduce a known
failure mode; they do not guarantee the timing of unpredictable household use.

Successful startup data collection records component health before the first
training run. A rejected initial candidate reports `fallback_validation`, with
its reason and scores in the `last_training_result` attribute of the ML stats
sensor. The same summary appears in the add-on console. Rejection keeps the
standard forecast available and waits for the configured retry interval;
waiting cycles no longer claim a training run has started.

These health/reporting corrections do not improve the model's forecast accuracy
or change candidate acceptance. An accuracy experiment that improves validation
but worsens a separate later forecast must not be promoted on that evidence.

Installing the branch does not enable LoadML as a planning source or change any
AlphaESS, EV, iBoost, tariff or inverter-control settings. Observe the forecast
with the existing source setting before deciding whether to use it for planning.

## Validation and reversal

Regression tests cover preserved rate boundaries, the clock change, cache
migration, separate validation targets, bounded feature paths, collapsed-network
fallback, long-range checkpoint selection, rejected/failed training isolation,
candidate acceptance and retry timing. Existing LoadML and interface tests also
exercise compatibility with the official release.

The release integration and LoadML fix are separate commits. Returning to the
official release or reverting the focused fix restores the previous code; an
incompatible model is retrained automatically. No installation or Home Assistant
restart is performed by preparing this branch.
