/** UI-only localization: literals are translated; interpolated user data stays verbatim. */
import { traditional } from './ui-phrases';
import { readDisplayLocal, writeDisplayLocal, onDisplayScopeReady } from './display-storage';
export type UILanguage = 'zh-Hans' | 'zh-Hant';
type Phrase = { readonly uiPhrase: string };
type Part = unknown;
type Binding = { node: WeakRef<Node>; key: string; attribute: boolean; parts: Part; last: string };
const bindings = new Set<Binding>();
const owned = new WeakMap<Node, Map<string, Binding>>();
const listeners = new Set<(language: UILanguage) => void>();
let language: UILanguage = 'zh-Hans';
export const uiLanguage = (): UILanguage => language;
export const corePhrase = (uiPhrase: string): Phrase => ({ uiPhrase });
function render(part: Part, locale: UILanguage = language): string {
  if (Array.isArray(part)) return part.map(item => render(item, locale)).join('');
  if (part && typeof part === 'object' && 'uiPhrase' in part) {
    const source = String((part as Phrase).uiPhrase);
    return locale === 'zh-Hant' ? traditional[source] ?? source : source;
  }
  return String(part ?? '');
}
function read(node: Node, key: string, attribute: boolean): string {
  return String(attribute ? (node as Element).getAttribute(key) ?? '' : (node as any)[key] ?? '');
}
function write(node: Node, key: string, value: string, attribute: boolean): void {
  if (attribute) (node as Element).setAttribute(key, value);
  else (node as any)[key] = value;
}
function bind(node: Node, key: string, parts: Part, attribute: boolean): string {
  let map = owned.get(node);
  if (!map) { map = new Map(); owned.set(node, map); }
  const id = (attribute ? '@' : '') + key;
  const old = map.get(id); if (old) bindings.delete(old);
  const last = render(parts);
  const binding = { node: new WeakRef(node), key, attribute, parts, last };
  map.set(id, binding); bindings.add(binding); write(node, key, last, attribute);
  // Prune weak entries during long-running chats without retaining detached DOM trees.
  if (bindings.size % 256 === 0) for (const item of bindings) if (!item.node.deref()) bindings.delete(item);
  return last;
}
export const coreSetUIText = (node: Node, key: string, parts: Part): string => bind(node, key, parts, false);
export const coreSetUIAttribute = (node: Element, key: string, parts: Part): string => bind(node, key, parts, true);
export function coreUITextNode(parts: Part): Text {
  const node = document.createTextNode(''); bind(node, 'textContent', parts, false); return node;
}
/** Use only for built-in label tables, never API names or user-authored text. */
export const uiLabel = (node: Node, text: string, key = 'textContent'): string => bind(node, key, corePhrase(text), false);
export function uiSource(node: Node, key = 'textContent'): string {
  const binding = owned.get(node)?.get(key);
  return binding && read(node, key, false) === binding.last
    ? render(binding.parts, 'zh-Hans') : read(node, key, false);
}
export function setUILanguage(value: UILanguage, persist = true): void {
  language = value === 'zh-Hant' ? 'zh-Hant' : 'zh-Hans';
  if (persist) writeDisplayLocal('ui-language', language);
  if (typeof document !== 'undefined') document.documentElement.lang = language;
  for (const item of bindings) {
    const node = item.node.deref();
    if (!node || (item.key === 'textContent' && node.nodeType === 1 && Array.from(node.childNodes).some(child => child.nodeType !== 3)) || read(node, item.key, item.attribute) !== item.last) { bindings.delete(item); continue; }
    item.last = render(item.parts); write(node, item.key, item.last, item.attribute);
  }
  for (const listener of listeners) listener(language);
}
export function onUILanguage(listener: (language: UILanguage) => void): () => void {
  listeners.add(listener); return () => listeners.delete(listener);
}
onDisplayScopeReady(() => setUILanguage(readDisplayLocal('ui-language') === 'zh-Hant' ? 'zh-Hant' : 'zh-Hans', false));
