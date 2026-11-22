import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { pageFetch as fetch } from './status-json';
import { speaker } from './speaker';

interface BotList {
  active: string;
  bots: Array<{ id: string; displayName: string }>;
}

/** Display metadata only; the running Bot is chosen by Start.bat. */
export async function botDialog(shownName = speaker()): Promise<void> {
  const opener = document.activeElement as HTMLElement | null;
  const response = await fetch('/infinity/bots', { cache: 'no-store' });
  if (!response.ok) throw new Error('Bot 信息暂时不可用。');
  const state = await response.json() as BotList;
  const dialog = document.createElement('dialog'); dialog.className = 'avatar-crop-dialog';
  coreSetUIAttribute(dialog, 'aria-label', corePhrase("修改显示名"));
  const title = document.createElement('h2'); coreSetUIText(title, 'textContent', corePhrase("修改显示名"));
  const hint = document.createElement('p'); hint.className = 'avatar-crop-hint';
  coreSetUIText(hint, 'textContent', corePhrase("只修改当前 Bot 的显示名，不修改文件夹或人设。"));
  const input = document.createElement('input'); input.maxLength = 80;
  coreSetUIAttribute(input, 'aria-label', corePhrase("Bot 显示名"));
  input.value = state.bots.find(one => one.id === state.active)?.displayName || shownName;
  const status = document.createElement('p'); status.setAttribute('role', 'status');
  const actions = document.createElement('div'); actions.className = 'avatar-crop-actions';
  const cancel = document.createElement('button'); coreSetUIText(cancel, 'textContent', corePhrase("取消"));
  const save = document.createElement('button'); coreSetUIText(save, 'textContent', corePhrase("保存"));
  save.className = 'primary'; let busy = false;
  cancel.onclick = () => { if (!busy) dialog.close(); };
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  save.onclick = () => {
    if (busy) return;
    busy = true; save.disabled = cancel.disabled = true;
    void (async () => {
      try {
        const answer = await fetch('/infinity/bots/rename', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ active: state.active, displayName: input.value.trim() }),
        });
        if (!answer.ok) {
          const error = await answer.json() as { said?: string };
          throw new Error(error.said || '保存失败');
        }
        window.dispatchEvent(new Event('bot-display-changed')); dialog.close();
      } catch (error) { status.textContent = String(error); }
      finally { busy = false; save.disabled = cancel.disabled = false; }
    })();
  };
  actions.append(cancel, save); dialog.append(title, hint, input, status, actions);
  document.body.append(dialog);
  try {
    dialog.showModal(); input.focus();
    await new Promise<void>(resolve => {
      dialog.addEventListener('close', () => resolve(), { once: true });
    });
  } finally { dialog.remove(); opener?.focus(); }
}
