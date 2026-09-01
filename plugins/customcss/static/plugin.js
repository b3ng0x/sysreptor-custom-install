/**
 * CustomCSS plugin frontend entry point.
 *
 * Global injection mechanism: this file runs once, live, in the actual top-level app window
 * (confirmed via SysReptor's own built-in `customizetheme` plugin, which does the same thing -
 * plugin.js is NOT sandboxed). `pluginConfig.frontend_settings.css` arrives pre-populated from
 * the server on every page load (baked into the app's initial bootstrap via
 * /api/public/utils/settings/ - see apps.py get_frontend_settings()), so no extra fetch is
 * needed here: it's already reactive to whatever was last saved, for every user, on every load.
 */
const STYLE_TAG_ID = 'plugin-customcss-style';

export function applyCss(doc, css) {
  let el = doc.getElementById(STYLE_TAG_ID);
  if (!css) {
    if (el) el.remove();
    return;
  }
  if (!el) {
    el = doc.createElement('style');
    el.id = STYLE_TAG_ID;
    doc.head.appendChild(el);
  }
  el.textContent = css;
}

export default function (options) {
  const css = options.pluginConfig.frontend_settings?.css || '';
  applyCss(document, css);

  options.pluginHelpers.addRoute({
    scope: 'main',
    route: {
      path: 'custom-css',
      component: () => options.pluginHelpers.iframeComponent({
        src: 'index.html',
      }),
    },
    menu: {
      title: 'Custom CSS',
      icon: 'mdi-palette',
    },
  });
}
