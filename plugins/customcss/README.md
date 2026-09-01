# Custom CSS

Lets a superuser inject raw CSS across the entire SysReptor web app - every page, for every
user - not just this plugin's own settings page.

## Relationship to the built-in `customizetheme` plugin

SysReptor already ships an official plugin (`customizetheme`) for JSON-based theme
customization: colors, logo, per-light/dark-mode overrides, using Vuetify's real theme
variable names (`--v-theme-primary`, `--v-theme-risk-critical`, etc.), configured via the
`PLUGIN_CUSTOMIZETHEME_CONFIG` setting. If what you want is achievable with a color/variable
override, use that instead - it's more robust than raw CSS. This plugin (`customcss`) exists
for anything beyond that: arbitrary selectors, layout tweaks, component-specific overrides.

This plugin's settings page also offers a one-click **Matrix Dark Theme** preset, which applies
through `customizetheme`'s own config (not raw CSS), plus a Preview for it.

## How the global injection works

`plugin.js` runs unsandboxed in the actual top-level app window (same mechanism
`customizetheme` uses - confirmed by reading its source). On every page load, the currently
saved CSS is delivered pre-populated in `pluginConfig.frontend_settings.css` (via
`CustomCssPluginConfig.get_frontend_settings()` in `apps.py`, served through
`/api/public/utils/settings/`) - no extra API round-trip needed. If `CUSTOM_CSS_ENABLED` is
true, `plugin.js` injects it into `document.head` as a `<style id="plugin-customcss-style">`
tag immediately.

## Preview vs. Keep

The settings page (`Custom CSS` in the main menu) is loaded in an iframe, but the iframe is
**not sandboxed** (verified: no `sandbox` attribute on SysReptor's `PluginIFrame` component),
so it has direct same-origin access to `window.parent.document`.

- **Preview**: injects the CSS directly into `window.parent.document.head` under a different
  style tag id (`plugin-customcss-style-preview`) than the persisted one. Applies instantly to
  the app shell you're looking at, in this browser only. Never touches the backend. Cleared by
  "Clear preview" or a page reload.
- **Keep**: `POST`s to this plugin's own `config/` API endpoint, which validates the CSS,
  persists it via SysReptor's `configuration.update()`, and calls `reload_server()` so it takes
  effect immediately for every user/session without needing a container restart - no more
  waiting for a `docker compose restart app` the way earlier config changes in this project did.

## API

- `GET /api/plugins/a3d34829-b4a1-4d80-8e81-269850972f23/api/config/` - current CSS + enabled
  flag. Superuser only.
- `POST` same URL, body `{"css": "...", "enabled": true|false}` - validates and saves.
  Superuser only.

Validation on save: max 200KB, and rejects any value containing `</style`, `<script`,
`javascript:`, or `expression(` (low-risk since the value is only ever placed via
`textContent`, never string-concatenated into HTML - but rejected defensively regardless, in
case some future code path is less careful).

## Configuration fields (also editable via SysReptor's general Settings page)

- `CUSTOM_CSS` - the raw CSS string.
- `CUSTOM_CSS_ENABLED` - boolean, whether it's actually applied. Kept separate from the CSS
  text itself so a saved-but-disabled blob can be kept around without going live.
