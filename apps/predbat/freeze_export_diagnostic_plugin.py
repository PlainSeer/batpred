"""Fork-only startup diagnostic and config bridge for Freeze Export battery discharge modelling."""


class FreezeExportDiagnosticPlugin:
    """Initialise and log the configured Freeze Export battery discharge value."""

    def __init__(self, base):
        self.base = base

    def register_hooks(self, plugin_system):
        """Register the post-component startup hook."""
        plugin_system.register_hook("on_init", self.on_init)

    def on_init(self):
        """Initialise the fork-only prediction value before the first plan runs."""
        self.base.inverter_freeze_export_discharge_rate = max(
            float(self.base.args.get("inverter_freeze_export_discharge_rate", 0)), 0.0
        )
        self.base.log(
            "Freeze Export discharge rate configured: {:.0f} W".format(
                self.base.inverter_freeze_export_discharge_rate
            )
        )
