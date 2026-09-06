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

const PREVIEW_IFRAME_SELECTOR = '.preview-iframe';

// Notes/report-section/finding rendered Markdown preview panes render inside a *separate*
// same-origin `srcdoc` iframe (its own fully isolated document - a <style> tag appended to the
// top-level window's <head> can never reach inside it, no matter the selector). The iframe ships
// with zero stylesheet of its own, so by default its content is plain browser-default black text
// on a transparent background, which just lets the parent app's dark background bleed through
// behind it - reads as barely-visible dark-on-dark. Confirmed via DOM inspection (Sept 2026): the
// iframe's initial srcdoc is just `<div id="preview-content"></div>`, and the actual rendered
// Markdown HTML gets injected into that div by the app's own JS *after* the iframe's load event -
// so we inject our CSS on 'load' too (not just once), since each live-preview re-render swaps in
// a fresh srcdoc and wipes anything previously injected into that iframe's document.
function injectIntoIframe(iframe, css) {
  try {
    const doc = iframe.contentDocument;
    if (doc) applyCss(doc, css);
  } catch (e) {
    // Not ready yet or briefly cross-origin during a reload - the next 'load' event will retry.
  }
}

function watchPreviewIframes(getCss) {
  const attach = (iframe) => {
    if (iframe.dataset.customcssWatched) return;
    iframe.dataset.customcssWatched = '1';
    iframe.addEventListener('load', () => injectIntoIframe(iframe, getCss()));
    injectIntoIframe(iframe, getCss()); // in case it's already loaded by the time we attach
  };
  document.querySelectorAll(PREVIEW_IFRAME_SELECTOR).forEach(attach);
  new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (!(node instanceof Element)) continue;
        if (node.matches && node.matches(PREVIEW_IFRAME_SELECTOR)) attach(node);
        if (node.querySelectorAll) node.querySelectorAll(PREVIEW_IFRAME_SELECTOR).forEach(attach);
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
}

export default function (options) {
  const getCss = () => options.pluginConfig.frontend_settings?.css || '';
  applyCss(document, getCss());
  watchPreviewIframes(getCss);

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
