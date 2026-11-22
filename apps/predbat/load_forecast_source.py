# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
"""Planning-source preferences, independent of model training and accuracy evidence."""

SOURCE_LABELS = {"auto": "Automatic", "classic": "Historical", "ml": "LoadML", "regression": "Regression"}
DEFAULT_SOURCE_OPTIONS = ("classic", "ml", "regression")


def source_settings(mode, choice, default, configured_ml):
    """Validate settings; explicit learned choices permit planning in Automatic mode only."""
    choice = choice if isinstance(choice, str) and choice in SOURCE_LABELS else "auto"
    default = default if isinstance(default, str) and default in DEFAULT_SOURCE_OPTIONS else "classic"
    requested = bool(configured_ml)
    if mode == "auto":
        if choice == "classic":
            requested = False
        elif choice in ("ml", "regression") or (choice == "auto" and default in ("ml", "regression")):
            requested = True
    return choice, default, requested


def choose_planning_source(choice, default, selected, recommended, available, judgments, external_only=False):
    """Apply a manual override or provisional default without changing any earned permission.

    An automatic default is eligible while evidence is insufficient to rank models,
    or if that candidate has already earned permission.
    Earned recommendations and established historical preference take precedence.
    Manual choices bypass accuracy qualification, never availability validation.
    """

    def fallback():
        """Prefer historical, respecting valid external-only feeds and keeping a usable last resort."""
        if available.get("classic") and not external_only:
            return "classic"
        if external_only and available.get("external"):
            return "external"
        for model in (selected, recommended, "ml", "regression", "classic"):
            if available.get(model):
                return model
        return selected

    if choice != "auto":
        allowed = bool(available.get(choice)) and (choice != "classic" or not external_only)
        return (choice, "manual") if allowed else (fallback(), "manual_unavailable")
    if recommended in ("ml", "regression") and available.get(recommended):
        return selected, "automatic"
    if default in ("ml", "regression"):
        if not available.get(default):
            return fallback(), "default_unavailable"
        if judgments.get(default) in ("measuring", "ml_earned", "regression_earned"):
            return default, "automatic_default"
    return selected, "automatic"
