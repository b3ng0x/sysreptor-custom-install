from sysreptor.plugins import BooleanField, FieldDefinition, PluginConfig, StringField, configuration


class CustomCssPluginConfig(PluginConfig):
    """
    Lets a superuser inject raw CSS across the whole SysReptor web app (every page, not just
    this plugin's own settings page), on top of what the official `customizetheme` plugin
    already covers via Vuetify theme/JSON variables. Two independent, complementary knobs:
    JSON theme vars (customizetheme) for anything Vuetify already exposes as a themeable
    color/variable, and raw CSS (this plugin) for anything beyond that - navbar layout tweaks,
    button shapes, arbitrary selectors, etc.

    Persisted CSS (CUSTOM_CSS_ENABLED=true) is delivered to every page load via
    get_frontend_settings() below, so plugin.js can inject it with zero extra API round-trip -
    it's baked into the app's initial bootstrap payload, same mechanism the built-in
    customizetheme plugin uses for its frontend_settings.
    """

    plugin_id = 'a3d34829-b4a1-4d80-8e81-269850972f23'

    configuration_definition = FieldDefinition(fields=[
        StringField(
            id='CUSTOM_CSS',
            default='',
            help_text='Raw CSS injected into every page of the app when CUSTOM_CSS_ENABLED is true. '
                      'Edit via the "Custom CSS" plugin page in the main menu rather than here directly - '
                      'it has a live preview and validates the value before saving.'),
        BooleanField(
            id='CUSTOM_CSS_ENABLED',
            default=False,
            help_text='Whether the persisted CUSTOM_CSS is actually applied app-wide. Kept separate from '
                      'CUSTOM_CSS itself so a saved-but-disabled CSS blob can be kept around without being live.'),
    ])

    def get_frontend_settings(self, request) -> dict:
        return {
            'css': configuration.CUSTOM_CSS if configuration.CUSTOM_CSS_ENABLED else '',
        }
