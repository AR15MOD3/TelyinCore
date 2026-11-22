/** Run in <head>, before stylesheets and the app. No network or hidden-page timeout. */
export function bootTheme(presets, namespace = null, restoreRail = true) {
  const root = document.documentElement;
  // Before Bot identity is known, paint public defaults only.
  const read = key => namespace === null ? null : localStorage.getItem(namespace + key);
  let mode = 'auto'; let pick = ''; let cache = null;
  try {
    mode = read('telyincore-look-mode') || 'auto';
    pick = read('telyincore-look-preset') || '';
    cache = JSON.parse(read('telyincore-look-resolved-v1') || 'null');
  } catch { /* Disabled or corrupt storage must not delay first paint. */ }
  const dark = typeof matchMedia === 'function'
    && matchMedia('(prefers-color-scheme: dark)').matches;
  const scheme = mode === 'dark' || (mode !== 'light' && dark) ? 'dark' : 'light';
  const base = (mode !== 'auto' && presets.find(one => one.file === pick))
    || presets.find(one => one.values.__scheme === scheme) || presets[0];
  const values = { ...base.values };
  const allowed = new Set(Object.keys(presets[0].values).filter(key => key.startsWith('--')));
  if (cache?.version === 1 && cache.mode === mode && cache.pick === pick && cache.dark === dark
      && cache.base === JSON.stringify(base.values)
      && cache.values && typeof cache.values === 'object') {
    for (const [key, value] of Object.entries(cache.values)) {
      if (allowed.has(key) && typeof value === 'string' && value.length < 500
          && !/[;{}<>]|url\s*\(|@import/i.test(value)) values[key] = value;
    }
  }
  for (const [key, value] of Object.entries(values)) {
    if (key.startsWith('--') && typeof value === 'string') root.style.setProperty(key, value);
  }
  root.style.colorScheme = base.values.__scheme === 'dark' ? 'dark' : 'light';
  root.style.backgroundColor = 'var(--color-canvas)';
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', values['--color-canvas']);
  if (!restoreRail) return;
  // Restore layout only for an identified Bot; initial paint uses public defaults.
  let railWidth = innerWidth <= 900 ? 68 : 280;
  try {
    const stored = read('telyincore.rail-width');
    if (stored !== null && stored.trim() !== '' && Number.isFinite(Number(stored))) {
      railWidth = Number(stored);
    }
  } catch { /* The default layout still works without storage. */ }
  railWidth = Math.max(68, Math.min(Math.max(200, Math.min(480, innerWidth - 360)), railWidth));
  if (railWidth < 200) railWidth = 68;
  root.style.setProperty('--rail-w', `${Math.round(railWidth)}px`);
  root.setAttribute('data-core-rail-compact', String(railWidth === 68));
}

export function renderThemeDocument(html, presets) {
  const data = JSON.stringify(presets).replace(/</g, '\\u003c')
    .replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
  const script = `<script data-core-theme-bootstrap>(()=>{const presets=${data};const boot=${bootTheme.toString()};globalThis.__coreApplyScopedTheme=namespace=>boot(presets,namespace,false);boot(presets);})();</script>`;
  return html.replace(/(<meta name="theme-color" content=")[^"]*(">)/,
    `$1${presets[0].values['--color-canvas']}$2`)
    .replace('<link rel="stylesheet"', `${script}\n<link rel="stylesheet"`);
}

/** Also provide the real default without JavaScript, directly from default.json. */
export function defaultThemeCss(values) {
  return '\n/* First-paint default: generated from presets/default.json. */\n:root {\n'
    + Object.entries(values).filter(([key]) => key.startsWith('--'))
      .map(([key, value]) => `  ${key}: ${value};`).join('\n') + '\n  color-scheme: light;\n}\n';
}
