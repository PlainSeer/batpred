"""A separate regression forecast using LoadML's prepared household data, never its weights."""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

from const import TIME_FORMAT
from load_regression import LoadRegressionPredictor, MIN_HISTORY_DAYS, MODEL_VERSION, TERMS


class RegressionForecast:
    """Fit and publish a lightweight candidate without changing the neural lifecycle."""

    def __init__(self, component):
        """Keep model and saved state separate from neural files and accuracy qualification."""
        self.component = component
        self.predictor = LoadRegressionPredictor(log_func=component.log, max_load_kw=component.ml_max_load_kw)
        self.definition = None
        self.loaded = False
        self.empty_reads = []
        self.status = "not_initialized"
        self.pending_save = False

    async def update(self):
        """Forecast from a snapshot of the same filtered data as the neural model."""
        c = self.component
        now = c.now_utc
        definition = hashlib.sha256(
            json.dumps(
                [c.get_arg(k, None, indirect=False) for k in ("load_today", "load_power", "car_charging_energy", "car_charging_hold", "car_energy_reported_load", "iboost_energy_today", "iboost_energy_subtract")], sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        if self.definition != definition:
            self.predictor = LoadRegressionPredictor(log_func=c.log, max_load_kw=c.ml_max_load_kw)
            self.definition, self.loaded, self.empty_reads = definition, False, []
            self.pending_save = False
        components = getattr(c.base, "components", None)
        storage = components.get_component("storage") if components else None
        try:
            if storage and not self.loaded:
                saved = await storage.load("predbat", "load_regression_model")
                if isinstance(saved, dict):
                    if saved.get("definition") == definition and not self.predictor.model_initialized:
                        self.predictor.restore(saved.get("model"))
                    self.loaded = True
                elif saved is None:
                    self.empty_reads.append(now)
                    self.loaded = len(set(self.empty_reads)) >= 3 and (now - self.empty_reads[0]).total_seconds() >= 600
        except Exception as exc:
            c.log("Warn: Regression model storage read failed: {}".format(exc))
        predictions = {}
        breakdown = {}
        if c.load_data_age_days < MIN_HISTORY_DAYS:
            self.status = "insufficient_data"
        else:
            try:
                async with c.data_lock:
                    load = dict(c.load_data or {})
                    temperature = dict(c.temperature_data or {})
                    minutes_now = c.minutes_now
                stamp = self.predictor.training_timestamp
                due = stamp is None or (now - stamp).total_seconds() >= c.ml_retrain_interval_seconds or not self.predictor.is_valid(now)
                if due:
                    fitted = await asyncio.to_thread(self.predictor.train, load, temperature, minutes_now=minutes_now)
                    if fitted is None:
                        self.status = "insufficient_data"
                    else:
                        self.pending_save = True
                if storage and self.loaded and self.pending_save:
                    try:
                        saved = await storage.save("predbat", "load_regression_model", {"definition": definition, "model": self.predictor.serialise()}, format="json", expiry=now + timedelta(days=9))
                        self.pending_save = not bool(saved)
                    except Exception as exc:
                        c.log("Warn: Regression model storage save failed: {}".format(exc))
                if self.predictor.is_valid(datetime.now(timezone.utc), c.ml_max_model_age_hours):
                    predictions, breakdown = await asyncio.to_thread(lambda: (self.predictor.predict(load, temperature, minutes_now=minutes_now), self.predictor.explain(load, temperature, minutes_now=minutes_now)))
                    self.status = "active" if predictions else "invalid_forecast"
                elif self.status != "insufficient_data":
                    self.status = "not_initialized"
            except Exception as exc:
                # A failed candidate cannot stop the existing neural or battery plan.
                self.status = "unavailable"
                c.log("Warn: Regression forecast failed: {}".format(exc))
        self.publish(predictions, breakdown)

    def publish(self, predictions, breakdown):
        """Publish 5-minute cumulative energy with local-midnight resets and explicit units."""
        c = self.component
        baseline = float(c._load_baseline_now())
        results = {}
        reset = 0.0
        for minute, value in predictions.items():
            stamp = c.midnight_utc + timedelta(minutes=c.minutes_now + minute)
            if minute > 0 and (c.minutes_now + minute) % 1440 == 0:
                reset = value + baseline
            results[stamp.strftime(TIME_FORMAT)] = round(float(value - reset + baseline), 4)
        now = c.midnight_utc + timedelta(minutes=c.minutes_now)
        stats = {
            "friendly_name": "Regression Load Stats",
            "model": "regression",
            "model_version": MODEL_VERSION,
            "status": self.status,
            "load_today": round(baseline, 2),
            "load_total": round(float(max(predictions.values())), 2) if predictions else 0,
            "load_today_h1": results.get((now + timedelta(hours=1)).strftime(TIME_FORMAT)),
            "load_today_h8": results.get((now + timedelta(hours=8)).strftime(TIME_FORMAT)),
            "last_trained": self.predictor.training_timestamp.isoformat() if self.predictor.training_timestamp else None,
            "mae_kwh_per_30min": self.predictor.validation_mae,
            "bias_kwh_per_30min": self.predictor.validation_bias,
            "weights": {term: float(weight) for term, weight in zip(TERMS, self.predictor.weights)} if self.predictor.weights is not None else {},
            "heat_dropped": list(self.predictor.heat_dropped),
            "training_days": c.load_data_age_days,
            "unit_of_measurement": "kWh",
            "icon": "mdi:chart-line",
        }
        c.dashboard_item("sensor." + c.prefix + "_load_regression_forecast", state=self.status, attributes={"friendly_name": "Regression Load Forecast", "results": results, "breakdown": breakdown, "model_version": MODEL_VERSION}, app="load_ml")
        c.dashboard_item("sensor." + c.prefix + "_load_regression_stats", state=stats["load_total"], attributes=stats, app="load_ml")
