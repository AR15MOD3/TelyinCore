// This local form has its own endpoint. Nothing here is a dialogue frame.
export function mountConsoleIntroduction(root: HTMLElement, composer: HTMLFormElement): void {
  const guide = document.createElement('section');
  guide.className = 'console-introduction';
  guide.hidden = true;
  guide.setAttribute('aria-label', '首次见面');
  root.querySelector('.live-stream')?.before(guide);
  const setPending = (pending: boolean): void => {
    // Keep the ordinary input out of the naming flow, including when the tab is reopened.
    composer.toggleAttribute('data-introducing', pending);
  };
  const line = (text: string): HTMLParagraphElement => {
    const el = document.createElement('p');
    el.textContent = text;
    return el;
  };
  async function load(): Promise<void> {
    try {
      const response = await fetch('/console/introduction', { cache: 'no-store' });
      if (response.status === 404) return;
      if (!response.ok) throw new Error();
      const state = await response.json();
      if (!state.pending) { guide.hidden = true; setPending(false); return; }
      const form = document.createElement('form');
      const heading = line(String(state.bot) + ' · 系统引导');
      const greeting = line('你好，初次见面！我该怎么称呼你？');
      const input = document.createElement('input');
      input.name = 'name';
      input.placeholder = '你的称呼';
      input.setAttribute('aria-label', '你的控制台称呼');
      input.required = true;
      input.autocomplete = 'off';
      const save = document.createElement('button');
      save.type = 'submit';
      save.textContent = '保存称呼';
      const later = document.createElement('button');
      later.type = 'button';
      later.textContent = '稍后再填';
      later.onclick = () => { guide.hidden = true; setPending(false); };
      const status = line('');
      status.setAttribute('role', 'status');
      form.append(input, save, later);
      guide.replaceChildren(heading, greeting, line('设置你在控制台使用的称呼。'), form, status);
      guide.hidden = false;
      setPending(true);
      form.onsubmit = async (event) => {
        event.preventDefault();
        if (save.disabled) return;
        const name = input.value.trim();
        if (!name || Array.from(name).length > 80) {
          status.textContent = '请输入 1 至 80 个字符的称呼。';
          input.focus();
          return;
        }
        input.disabled = save.disabled = later.disabled = true;
        try {
          const reply = await fetch('/console/introduction', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bot: state.bot, name }) });
          const result = await reply.json();
          if (!reply.ok || result.pending !== false) {
            if (reply.status === 409) { form.remove(); setPending(false); }
            throw new Error(result.said || '保存失败，请重试。');
          }
          guide.replaceChildren(heading, line('已保存称呼：' + name));
          setPending(false);
        } catch (error) {
          status.textContent = error instanceof Error ? error.message : '保存状态未确认，请重试。';
        } finally { input.disabled = save.disabled = later.disabled = false; }
      };
    } catch {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = '重新读取';
      retry.onclick = () => { retry.disabled = true; void load(); };
      guide.replaceChildren(line('暂时读不到控制台画像。'), retry);
      guide.hidden = false;
      setPending(false);
    }
  }
  void load();
}
