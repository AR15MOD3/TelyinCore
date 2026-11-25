import { bootTheme } from '../../../scripts/theme-document.mjs';
import { initializeDisplayScope, onDisplayScopeReady } from './display-storage';
import { getStatusJson } from './status-json';

declare const __MANUAL_PALETTES__: {file:string;values:Record<string,string>}[];
let namespace: string | null = null;
let saved: Record<string,string> | null = null;
const apply = () => bootTheme(__MANUAL_PALETTES__, namespace, false, saved);

apply();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', apply);
if (location.protocol === 'http:' || location.protocol === 'https:') {
  onDisplayScopeReady(prefix => {
    namespace = prefix;
    apply();
    window.addEventListener('storage', event => {
      if (event.key === null || event.key.startsWith(prefix + 'telyincore-look-')) apply();
    });
    void getStatusJson<Record<string,string>>('/look').then(profile => {
      saved = profile;
      apply();
    }).catch(() => { /* The scoped cache and built-in palette remain available offline. */ });
  });
  void initializeDisplayScope();
}
