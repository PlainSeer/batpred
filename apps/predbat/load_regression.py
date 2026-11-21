# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# LoadReg - direct household load regression
# Based on Shedimus/batpred loadml-model-select at 49c405090e0097dabfb327a522bd13b07357dc40.
# Adapted for independent three-model scoring, leakage-free validation and Storage.
# cspell:ignore Shedimus nansum fmax linalg lstsq rcond
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init


"""LoadReg: linear regression for household load forecasting, an alternative to the LoadML MLP in load_predictor.py.

One ordinary least-squares model forecasts the next 48 hours in 30-minute slots.
The load in a future slot is a base amount plus five weighted inputs:

    profile      typical load at the same time of day over the 7 days before the forecast: the mean without the
                 highest day, scaled so that a day of it still adds up to the plain 7-day mean
    temperature  heating degrees (below 12 C) in the slot, minus their 7-day mean for that time of day
    cold         degrees below 5 C in the slot, minus their 7-day mean for that time of day: the extra a heat pump
                 uses per degree in cold weather. Only fitted when there is enough cold weather to learn it from
    last_2h      how far the last 2 hours ran above the profile, fading out over the next few hours
    last_24h     mean load over the last 24 hours minus the mean over the last 7 days

so every forecast value can be read back as weight x input for each term (see explain()).
"""

import numpy as np
from datetime import datetime, timezone
from load_forecast_guard import REGRESSION_MODEL_VERSION as MODEL_VERSION

# Saved models of another version are refitted.
STEP_MINUTES = 5  # Resolution of the input data and of the forecast returned
SLOT_MINUTES = 30  # Resolution the model works at
SLOT_STEPS = SLOT_MINUTES // STEP_MINUTES
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES
PROFILE_DAYS = 7  # Days the time-of-day profile is built from
PREDICT_HORIZON = 2 * SLOTS_PER_DAY  # 48 hours of 30-minute slots
RECENT_SLOTS = 4  # The "last 2 hours"
RECENT_FADE_SLOTS = 4  # The last 2 hours term decays by 1/e every 2 hours ahead
HEATING_BASE_TEMP = 12.0  # No heating load is assumed above this outside temperature (C)
COLD_TEMP = 5.0  # Below this the cold term adds a second, usually steeper, slope (C)
COLD_MIN_DAYS = 3  # Days' worth of half hours below COLD_TEMP in the history before the cold term is fitted
MIN_HISTORY_DAYS = PROFILE_DAYS + 3  # 7 days of profile, 2 days held out for validation and 1 day to fit on
TERMS = ["base", "profile", "temperature", "cold", "last_2h", "last_24h"]


def nanmean(values, axis):
    """Mean ignoring NaN, giving NaN (without numpy's warning) where there is nothing to average."""
    count = np.sum(~np.isnan(values), axis=axis)
    return np.where(count > 0, np.nansum(values, axis=axis) / np.maximum(count, 1), np.nan)


def rolling_nanmean(values, window):
    """Mean of values[i : i + window] for every i that has a full window."""
    return nanmean(np.lib.stride_tricks.sliding_window_view(values, window), axis=1)


class LoadRegressionPredictor:
    """
    Linear regression load predictor.

    All data is keyed by minutes relative to now at 5-minute steps: key m is the 5 minutes that
    ended m minutes ago, negative keys are in the future (-5 is the next 5 minutes).
    """

    def __init__(self, log_func=None, max_load_kw=23.0):
        """
        Initialise the load predictor.

        Args:
            log_func: Logging function (defaults to print)
            max_load_kw: Maximum load in kW for clipping predictions
        """
        self.log = log_func if log_func else print
        self.max_load_kw = max_load_kw

        self.weights = None  # One per entry of TERMS, set by train()
        self.cold_fitted = False  # Whether the last train() fitted the cold term (its weight is zero when not)
        self.training_timestamp = None
        self.validation_mae = None  # kWh per 30 minutes over the 48 hours held out
        self.validation_bias = None  # Mean (predicted - actual) over the same 48 hours; + = over-predicting
        self.model_initialized = False

    def _slots(self, data, first_slot, last_slot, total):
        """
        Turn {minute: value} at 5-minute steps into an array of 30-minute slots.

        Slot s covers the 30 minutes that ended 30 * s minutes ago, so slot 0 is the newest
        history and slot -1 the first future slot.

        Args:
            data: Dict of {minute: value}
            first_slot: First slot to return
            last_slot: One past the last slot to return
            total: True to sum the steps (energy, NaN unless all six are present), False to average those present

        Returns:
            Array of last_slot - first_slot values, NaN where there is no data
        """
        steps = np.full((last_slot - first_slot) * SLOT_STEPS, np.nan)
        for index in range(len(steps)):
            value = data.get(first_slot * SLOT_MINUTES + index * STEP_MINUTES)
            try:
                steps[index] = float(value)
            except (TypeError, ValueError):
                pass
        steps[~np.isfinite(steps)] = np.nan
        steps = steps.reshape(-1, SLOT_STEPS)
        return np.where(np.isnan(steps).any(axis=1), np.nan, np.nansum(steps, axis=1)) if total else nanmean(steps, axis=1)

    def _inputs(self, load_minutes, temp_minutes, origins):
        """
        Build the model inputs for forecasts made at the given origins.

        An origin is how many slots ago the forecast is made, 0 being now. Each origin gives
        PREDICT_HORIZON rows, one per 30-minute slot ahead of it.

        Args:
            load_minutes: Dict of {minute: kwh_per_5min}, history only
            temp_minutes: Dict of {minute: temperature_celsius}, history and forecast
            origins: Array of origins, each needing PROFILE_DAYS of load history behind it

        Returns:
            Tuple of (inputs, actual): inputs has shape (origins, PREDICT_HORIZON, terms) with NaN where history is
            missing, and actual is the load that happened in each slot (NaN when not known yet)
        """
        slots = max(load_minutes) // SLOT_MINUTES + 1
        load = self._slots(load_minutes, 0, slots, total=True)
        temperature = self._slots(temp_minutes or {}, -PREDICT_HORIZON, slots, total=False)

        def same_time(values):
            """Values at each slot stacked with those at the same slot on each of the PROFILE_DAYS - 1 days before it."""
            span = len(values) - (PROFILE_DAYS - 1) * SLOTS_PER_DAY
            return np.stack([values[day * SLOTS_PER_DAY : day * SLOTS_PER_DAY + span] for day in range(PROFILE_DAYS)])

        # shortcut: "same time of day" is whole days back on the clock that does not change, so the profile sits an hour
        # off local habits for the week after a clock change; key the slots by local time if that week matters
        days = same_time(load)
        count = np.sum(~np.isnan(days), axis=0)
        mean = nanmean(days, axis=0)
        # The highest day at each time of day is left out so a one-off spike does not become part of the shape, then each
        # origin's profile is scaled so that its day still adds up to the plain mean and the spikes' energy is not lost
        trimmed = np.where(count >= 3, (np.nansum(days, axis=0) - np.fmax.reduce(days, axis=0)) / np.maximum(count - 1, 1), mean)
        trimmed_day = rolling_nanmean(trimmed, SLOTS_PER_DAY)
        scale = np.where(trimmed_day > 0, rolling_nanmean(mean, SLOTS_PER_DAY) / np.where(trimmed_day > 0, trimmed_day, 1), 1.0)[origins]
        week = PROFILE_DAYS * SLOTS_PER_DAY
        last_24h = (rolling_nanmean(load, SLOTS_PER_DAY)[: slots - week + 1] - rolling_nanmean(load, week))[origins]
        last_2h = rolling_nanmean(load, RECENT_SLOTS)[origins] - scale * rolling_nanmean(trimmed, RECENT_SLOTS)[origins]

        ahead = np.arange(PREDICT_HORIZON)
        target = origins[:, None] - 1 - ahead[None, :]  # Slot being forecast, negative when it has not happened yet
        newest = origins[:, None] + SLOTS_PER_DAY - 1 - ahead[None, :] % SLOTS_PER_DAY  # Newest history slot at the target's time of day

        def degrees_below(base):
            """Degrees below base in the target slot, minus their mean at that time of day over the last week.
            No temperature known for the slot or for its time of day in the last week means no adjustment."""
            degrees = np.maximum(base - temperature, 0)
            return np.nan_to_num(degrees[target + PREDICT_HORIZON] - nanmean(same_time(degrees[PREDICT_HORIZON:]), axis=0)[newest])

        inputs = np.empty(target.shape + (len(TERMS),))
        inputs[..., TERMS.index("base")] = 1.0
        inputs[..., TERMS.index("profile")] = trimmed[newest] * scale[:, None]
        inputs[..., TERMS.index("temperature")] = degrees_below(HEATING_BASE_TEMP)
        inputs[..., TERMS.index("cold")] = degrees_below(COLD_TEMP)
        inputs[..., TERMS.index("last_2h")] = last_2h[:, None] * np.exp(-ahead / RECENT_FADE_SLOTS)[None, :]
        inputs[..., TERMS.index("last_24h")] = last_24h[:, None]
        actual = np.where(target >= 0, load[np.maximum(target, 0)], np.nan)
        return inputs, actual

    def train(self, load_minutes, temp_minutes=None):
        """
        Fit the weights by least squares on every forecast the history allows.

        The newest 48 hours are first held out: the model is fitted without them, forecasts them
        from 48 hours back and is scored on the result, then refitted on everything.

        Args:
            load_minutes: Dict of {minute: kwh_per_5min}, history only
            temp_minutes: Dict of {minute: temperature_celsius}, history (forecast keys are ignored)

        Returns:
            Validation MAE in kWh per 30 minutes, or None if there is not enough history
        """
        slots = max(load_minutes) // SLOT_MINUTES + 1 if load_minutes else 0
        if slots < MIN_HISTORY_DAYS * SLOTS_PER_DAY:
            self.log("Warn: LoadReg Predictor: {:.1f} days of load history, need {} to train".format(slots / SLOTS_PER_DAY, MIN_HISTORY_DAYS))
            return None

        origins = np.arange(1, slots - PROFILE_DAYS * SLOTS_PER_DAY + 1)
        inputs, actual = self._inputs(load_minutes, temp_minutes, origins)
        usable = ~np.isnan(actual) & ~np.isnan(inputs).any(axis=-1)
        target = origins[:, None] - 1 - np.arange(PREDICT_HORIZON)[None, :]

        seen = usable & (target >= PREDICT_HORIZON)
        held_out = usable[PREDICT_HORIZON - 1]  # The forecast made 48 hours ago, origins start at 1
        if seen.sum() < 10 * len(TERMS) or not held_out.any():
            self.log("Warn: LoadReg Predictor: Not enough complete load history to train")
            return None

        def fit_and_score(rows_inputs):
            """Weights fitted on everything, plus the error on the held-out 48 hours of a fit made without them."""
            fit = lambda rows: np.linalg.lstsq(rows_inputs[rows], actual[rows], rcond=None)[0]
            error = self._clip(rows_inputs[PREDICT_HORIZON - 1][held_out] @ fit(seen)) - actual[PREDICT_HORIZON - 1][held_out]
            return fit(usable), error

        # The cold term is only fitted with enough cold weather in the history to learn its slope from, and only kept
        # when heating comes out as using more energy the colder it gets. Otherwise its input is zeroed and it gets
        # no weight: a slope learned from a handful of cold days, or from a house that does not heat electrically, is noise
        temperature = self._slots(temp_minutes or {}, 0, slots, total=False)
        past_temperature = temperature[PREDICT_HORIZON:]
        cold_slots = int(np.sum(past_temperature[np.isfinite(past_temperature)] < COLD_TEMP))
        self.cold_fitted = cold_slots >= COLD_MIN_DAYS * SLOTS_PER_DAY
        no_cold = inputs.copy()
        no_cold[..., TERMS.index("cold")] = 0.0
        past_weights = np.linalg.lstsq(inputs[seen], actual[seen], rcond=None)[0] if self.cold_fitted else None
        if self.cold_fitted and (past_weights[TERMS.index("cold")] < 0 or past_weights[TERMS.index("temperature")] < 0):
            self.cold_fitted = False
        weights, error = fit_and_score(inputs if self.cold_fitted else no_cold)
        if not self.cold_fitted:
            weights[TERMS.index("cold")] = 0.0  # Least squares leaves rounding noise on an all-zero input

        self.weights = weights
        self.validation_mae = float(np.mean(np.abs(error)))
        self.validation_bias = float(np.mean(error))
        self.training_timestamp = datetime.now(timezone.utc)
        self.model_initialized = True
        self.log(
            "LoadReg Predictor: Fitted on {} forecast slots: load per 30 min = {:.3f} + {:.3f} x profile + {:.4f} x heating degrees above last week + {:.4f} x degrees below {:g} C above last week ({}) "
            "+ {:.3f} x last 2h above profile + {:.3f} x last 24h above last week; holdout MAE {:.4f} kWh, bias {:+.4f} kWh".format(
                int(usable.sum()),
                *self.weights[:4],
                COLD_TEMP,
                "fitted" if self.cold_fitted else "off, {:.1f} days below {:g} C".format(cold_slots / SLOTS_PER_DAY, COLD_TEMP) if cold_slots < COLD_MIN_DAYS * SLOTS_PER_DAY else "off, slope came out negative",
                *self.weights[4:],
                self.validation_mae,
                self.validation_bias,
            )
        )
        return self.validation_mae

    def _clip(self, energy):
        """Keep 30-minute energies between zero and the maximum load."""
        return np.clip(energy, 0, self.max_load_kw * SLOT_MINUTES / 60.0)

    def explain(self, load_minutes, temp_minutes=None):
        """
        Break the forecast from now into what each term adds.

        Args:
            load_minutes: Dict of {minute: kwh_per_5min}, history only
            temp_minutes: Dict of {minute: temperature_celsius}, history and forecast (negative keys)

        Returns:
            Dict of {minutes_ahead: {term: kwh, ..., "total": kwh}} for each 30-minute slot, the terms being
            weight x input and summing to the total unless it was clipped. Empty when there is no forecast.
        """
        if not self.model_initialized or self.weights is None:
            self.log("Warn: LoadReg Predictor: Model not trained, cannot predict")
            return {}
        if not load_minutes or max(load_minutes) // SLOT_MINUTES + 1 < PROFILE_DAYS * SLOTS_PER_DAY:
            self.log("Warn: LoadReg Predictor: Less than {} days of load history, cannot predict".format(PROFILE_DAYS))
            return {}

        inputs, _ = self._inputs(load_minutes, temp_minutes, np.array([0]))
        terms = inputs[0] * self.weights
        if np.isnan(terms).any():
            self.log("Warn: LoadReg Predictor: Gaps in the load history, cannot predict")
            return {}
        total = self._clip(terms.sum(axis=1))
        breakdown = {}
        for slot in range(PREDICT_HORIZON):
            breakdown[slot * SLOT_MINUTES] = {term: round(float(value), 4) for term, value in zip(TERMS, terms[slot])}
            breakdown[slot * SLOT_MINUTES]["total"] = round(float(total[slot]), 4)
        return breakdown

    def predict(self, load_minutes, temp_minutes=None):
        """
        Forecast the next 48 hours.

        Args:
            load_minutes: Dict of {minute: kwh_per_5min}, history only
            temp_minutes: Dict of {minute: temperature_celsius}, history and forecast (negative keys)

        Returns:
            Dict of {minute: cumulative_kwh} at 5-minute steps, each 30-minute slot being spread evenly
            over its six steps. Empty dict on failure.
        """
        breakdown = self.explain(load_minutes, temp_minutes)
        if not breakdown:
            return {}
        result = {0: 0.0}
        cumulative = 0
        for minute, terms in breakdown.items():
            for step in range(SLOT_STEPS):
                cumulative += terms["total"] / SLOT_STEPS
                result[minute + (step + 1) * STEP_MINUTES] = round(cumulative, 4)
        return result

    def serialise(self):
        """Return plain data for Storage; neural model files are never used."""
        return {
            "version": MODEL_VERSION,
            "weights": self.weights.tolist() if self.weights is not None else None,
            "trained": self.training_timestamp.isoformat() if self.training_timestamp else None,
            "mae": self.validation_mae,
            "bias": self.validation_bias,
            "cold_fitted": self.cold_fitted,
        }

    def restore(self, data):
        """Restore a finite, compatible regression model, without granting planning permission."""
        try:
            if not isinstance(data, dict) or data.get("version") != MODEL_VERSION:
                return False
            weights = np.array(data["weights"], dtype=float)
            stamp = datetime.fromisoformat(data["trained"])
            if weights.shape != (len(TERMS),) or not np.isfinite(weights).all() or stamp.tzinfo is None:
                return False
            if not all(isinstance(data.get(k), (int, float)) and np.isfinite(data[k]) for k in ("mae", "bias")) or data["mae"] < 0:
                return False
            self.weights, self.training_timestamp = weights, stamp
            self.validation_mae, self.validation_bias = data["mae"], data["bias"]
            self.cold_fitted = data.get("cold_fitted") is True
            self.model_initialized = True
            return True
        except (KeyError, TypeError, ValueError):
            return False

    def is_valid(self, now, max_age_hours=48):
        """Reject missing, non-finite, future-dated or old models."""
        if not self.model_initialized or self.weights is None or not np.isfinite(self.weights).all() or self.training_timestamp is None or self.validation_mae is None or not np.isfinite(self.validation_mae) or self.validation_mae < 0:
            return False
        age = (now - self.training_timestamp).total_seconds() / 3600
        return -0.1 <= age <= max_age_hours
