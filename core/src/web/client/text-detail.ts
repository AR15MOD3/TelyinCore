import { corePhrase, coreSetUIText } from './ui-language';
export function showTextDetail(title: string, text: string): void {
  const opener = document.activeElement as HTMLElement | null;
  const dialog = document.createElement('dialog'); dialog.className = 'avatar-crop-dialog text-detail-dialog';
  dialog.setAttribute('aria-label', title);
  const heading = document.createElement('h2'); heading.textContent = title;
  const body = document.createElement('div'); body.className = 'text-detail-body'; body.textContent = text;
  const actions = document.createElement('div'); actions.className = 'avatar-crop-actions';
  const close = document.createElement('button'); close.type = 'button';
  coreSetUIText(close, 'textContent', corePhrase('关闭')); close.onclick = () => dialog.close();
  actions.append(close); dialog.append(heading, body, actions); document.body.append(dialog);
  dialog.addEventListener('close', () => { dialog.remove(); if (opener?.isConnected) opener.focus(); }, {once: true});
  dialog.showModal(); close.focus();
}
