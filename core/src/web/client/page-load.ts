import { corePhrase, coreSetUIText } from './ui-language';
/** Per-section loading feedback. Never clear usable content during a refresh. */
export function pageLoad(root: HTMLElement, label: string, retry: () => void) {
  const note = document.createElement('p'); note.className = 'infinity-hint page-load-status';
  note.hidden = true;
  let hasContent = false;
  note.setAttribute('role', 'status');
  const button = document.createElement('button'); button.type = 'button';
  button.className = 'infinity-text-link'; coreSetUIText(button, 'textContent', corePhrase("重试")); button.hidden = true;
  note.append(button); root.append(note);
  const show = (text: string, failed: boolean): void => {
    note.textContent = text; button.hidden = !failed; note.append(button); note.hidden = false;
  };
  button.addEventListener('click', retry);
  return {
    start(): void {
      // Refreshes must not insert a loading row between already rendered cards.
      // Keep a failure visible until a successful retry, rather than flashing it away.
      if (!hasContent) show(`正在加载${label}…`, false);
    },
    ready(): void { hasContent = true; note.hidden = true; },
    fail(): void { show(hasContent ? `${label}刷新失败，当前显示上次内容。` : `${label}加载失败，请重试。`, true); },
  };
}
