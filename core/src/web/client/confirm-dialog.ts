import { uiLabel } from './ui-language';
import { corePhrase, coreSetUIText } from './ui-language';
let nextDialogId = 0;
/** Shared frosted panel; the page backdrop itself remains unblurred. */
export async function confirmAction(
  heading: string, description: string, action: string, notice = false,
  options: { className?: string; detail?: string } = {},
): Promise<boolean> {
  const opener = document.activeElement as HTMLElement | null;
  const dialog = document.createElement('dialog'); dialog.className = 'avatar-crop-dialog';
  if (options.className) dialog.classList.add(options.className);
  const title = document.createElement('h2'); uiLabel(title, heading);
  title.id = `local-confirm-title-${++nextDialogId}`;
  const detail = document.createElement('p'); detail.className = 'avatar-crop-hint';
  uiLabel(detail, description); detail.id = `local-confirm-detail-${nextDialogId}`;
  dialog.setAttribute('aria-labelledby', title.id);
  dialog.setAttribute('aria-describedby', detail.id);
  const actions = document.createElement('div'); actions.className = 'avatar-crop-actions';
  const cancel = document.createElement('button'); cancel.type = 'button';
  coreSetUIText(cancel, 'textContent', corePhrase("取消"));
  cancel.hidden = notice;
  const proceed = document.createElement('button'); proceed.type = 'button';
  proceed.className = 'primary'; uiLabel(proceed, action);
  let accepted = false;
  let finish: () => void = () => {};
  const closed = new Promise<void>(resolve => {
    finish = resolve;
    dialog.addEventListener('close', () => resolve(), { once: true });
  });
  cancel.onclick = () => { dialog.close(); finish(); };
  proceed.onclick = () => { accepted = true; dialog.close(); finish(); };
  actions.append(cancel, proceed); dialog.append(title);
  if (options.detail) {
    const subject = document.createElement('p'); subject.className = 'plugin-uninstall-subject';
    subject.textContent = options.detail; dialog.append(subject);
  }
  dialog.append(detail, actions);
  document.body.append(dialog);
  try {
    dialog.showModal(); (notice ? proceed : cancel).focus();
    await closed;
    return accepted;
  } finally { dialog.remove(); if (opener?.isConnected) opener.focus(); }
}

export async function notifyAction(heading: string, description: string): Promise<void> {
  await confirmAction(heading, description, '知道了', true);
}
