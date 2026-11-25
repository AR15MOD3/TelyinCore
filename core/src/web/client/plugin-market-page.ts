import { pluginWorkspace } from './plugin-workspace';
import type { Chrome, Panel } from './shell';

/** New packages have their own page, independent of the installed inventory. */
export function pluginMarketPage(shell: Chrome): Panel {
  return pluginWorkspace(shell, 'discover');
}
