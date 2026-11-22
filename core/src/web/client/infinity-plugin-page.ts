import type { Chrome, Panel } from './shell';
import { pluginPage } from './plugin-ui-registry';

/** Installed plugin routes share one registry and a declarative fallback. */
export function infinityPluginPage(shell: Chrome): Panel {
  const route = location.hash.replace(/^#\/?infinity\//, '');
  let name = '';
  try { name = decodeURIComponent(route); } catch { name = ''; }
  return pluginPage(shell, name);
}
