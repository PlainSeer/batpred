# -----------------------------------------------------------------------------
# Predbat Home Battery System
# PlainSeer fork logo URL compatibility plugin
# -----------------------------------------------------------------------------
"""Keep the built-in PredBat web dashboard logo working in this fork.

Upstream web_helper currently points at bat_logo_light.png / bat_logo_dark.png
under springfall2008/batpred main.  Those PNG assets are no longer present on
upstream main, while this fork still carries them in docs/images.  Patch the
HTML returned by the already-imported web.get_header_html reference so the
normal dashboard and flying-bat animation use the fork-hosted copies.

This is deliberately isolated in a plugin rather than modifying upstream web
files, so it is easy to drop when upstream fixes the asset URLs.
"""


class ForkLogoPlugin:
    """Rewrite stale upstream logo URLs to the copies carried by this fork."""

    UPSTREAM_BASE = "https://raw.githubusercontent.com/springfall2008/batpred/refs/heads/main/docs/images/"
    FORK_BASE = "https://raw.githubusercontent.com/PlainSeer/batpred/main/docs/images/"

    def __init__(self, base):
        self.base = base
        self._original_get_header_html = None

    def register_hooks(self, plugin_system):
        plugin_system.register_hook("on_init", self.on_init)

    def on_init(self):
        try:
            import web  # PredBat's apps/predbat/web.py module

            original = web.get_header_html
            if getattr(original, "_plainseer_logo_url_patch", False):
                return

            self._original_get_header_html = original

            def get_header_html_with_fork_logo(*args, **kwargs):
                text = original(*args, **kwargs)
                return text.replace(self.UPSTREAM_BASE, self.FORK_BASE)

            get_header_html_with_fork_logo._plainseer_logo_url_patch = True
            web.get_header_html = get_header_html_with_fork_logo
            self.base.log("PlainSeer web logo URL fix enabled")
        except Exception as error:
            self.base.log("Warn: PlainSeer web logo URL fix could not be enabled: {}".format(error))
