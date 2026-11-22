/** One adapter contract for native plugin pages, icons and health. Unknown plugins
 * use the declarative config page and standard lifecycle signal without changes to the shell. */
import type { Chrome, Panel } from './shell';
import { ICONS } from './shell';
import { infinityPage } from './infinity-page';
import { pluginConfigPage } from './plugin-config-page';
import { qqSignals, type QQHealthSnapshot, type HealthSignal } from './qq-signals';
import { getStatusJson } from './status-json';

export interface PluginNavigationInfo {
  name: string; displayName?: string; installed?: boolean; enabled?: boolean; loaded?: boolean;
    icon?: string;
}
export interface PluginUIAdapter {
  icon: string;
  createPage: (shell: Chrome, name: string) => Panel;
  health?: { endpoint: string; signals: (value: unknown,
    plugin: PluginNavigationInfo) => HealthSignal[] };
}
const adapters = new Map<string, PluginUIAdapter>();
export function registerPluginUI(name: string, adapter: PluginUIAdapter): void {
  if (adapters.has(name)) throw new Error(`插件页面重复注册：${name}`);
  if (!Object.hasOwn(ICONS, adapter.icon)) throw new Error(`未知图标：${adapter.icon}`);
  adapters.set(name, adapter);
}
registerPluginUI('core-infinity-qq', { icon: 'chat', createPage: infinityPage,
  health: { endpoint: '/infinity/qq', signals: (data,
    plugin) => qqSignals(data as QQHealthSnapshot, plugin.loaded === true) } });
// Waves now declares its own sandboxed configPage in the installed package.
function lifecycle(plugin: PluginNavigationInfo): HealthSignal[] {
  return [{ state: plugin.loaded ? 'on' : plugin.enabled ? 'warn' : 'off',
    label: plugin.loaded ? '插件运行中' : plugin.enabled ? '插件已启用但未加载' : '插件已停用' }];
}
export function pluginPage(shell: Chrome, name: string): Panel {
  return (adapters.get(name)?.createPage ?? pluginConfigPage)(shell, name);
}
export function pluginIcon(plugin: PluginNavigationInfo): string {
  return plugin.icon && Object.hasOwn(ICONS, plugin.icon)
    ? plugin.icon : adapters.get(plugin.name)?.icon ?? 'puzzle';
}
const snapshots = new Map<string, unknown>();
const pending = new Set<string>();
export async function refreshPluginHealth(plugins: PluginNavigationInfo[]): Promise<void> {
  await Promise.all(plugins.map(async plugin => {
    const provider = adapters.get(plugin.name)?.health;
    if (!provider || pending.has(plugin.name)) return;
    pending.add(plugin.name);
    try {
      // Shared with the page's own status read: one request for both readers.
      snapshots.set(plugin.name, await getStatusJson(provider.endpoint, 5000));
    } catch { snapshots.delete(plugin.name); }
    finally { pending.delete(plugin.name); }
  }));
}
export function pluginSignals(plugin: PluginNavigationInfo): HealthSignal[] {
  const provider = adapters.get(plugin.name)?.health;
  const value = snapshots.get(plugin.name);
  return provider && value ? provider.signals(value, plugin) : lifecycle(plugin);
}
