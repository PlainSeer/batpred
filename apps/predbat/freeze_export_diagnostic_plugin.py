"""Fork-only startup diagnostic for Freeze Export battery discharge modelling."""


class FreezeExportDiagnosticPlugin:
    """Log the configured Freeze Export battery discharge value after HA logging starts."""

    def __init__(self, base):
        self.base = base

    def register_hooks(self, plugin_system):
        """Register the post-component startup hook."""
        plugin_system.register_hook("on_init", self.on_init)

    def on_init(self):
        """Emit the configured value once the HA/logging component is active."""
        self.base.log(
            "Freeze Export discharge rate configured: {:.0f} W".format(
                self.base.inverter_freeze_export_discharge_rate
            )
        )
