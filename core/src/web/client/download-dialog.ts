import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
/** Themed, local confirmation for optional model downloads. */
export async function chooseASRSource(kind: 'ASR' | 'TTS' | '视觉' = 'ASR', preferredSource?: string): Promise<string | null> {
  const opener = document.activeElement as HTMLElement | null;
  const dialog = document.createElement('dialog'); dialog.className = 'avatar-crop-dialog';
  const title = document.createElement('h2'); coreSetUIText(title, 'textContent', [corePhrase("下载推荐 "), kind, corePhrase(" 模型")]);
  const text = document.createElement('p'); text.className = 'avatar-crop-hint';
  coreSetUIText(text, 'textContent', (kind === 'ASR' ? corePhrase("SenseVoiceSmall INT8，约 241 MB，包含识别模型、词表、VAD 和运行依赖，完成后自动转写自检。") : (kind === '视觉' ? corePhrase("Qwen3.5-0.8B Q4_K_M + F16 视觉投影，约 703 MB。校验大小和 SHA256 后安装；不含 llama.cpp 运行时。") : corePhrase("Fun-CosyVoice3-0.5B-2512，约 5.43 GB。自动准备运行环境、默认参考音频和性能配置，并执行合成自检。首次环境安装还需下载数 GB 依赖。"))));
  const label = document.createElement('label'); coreSetUIText(label, 'textContent', corePhrase("下载源 "));
  const select = document.createElement('select'); coreSetUIAttribute(select, 'aria-label', corePhrase("下载源"));
  select.append(new Option('国内镜像 · HF-Mirror（直连）', 'mirror'),
    new Option('官方源 · Hugging Face（按下载代理设置）', 'official'));
  select.value = preferredSource === 'official' ? 'official' : 'mirror';
  label.append(select);
  const notice = document.createElement('p'); notice.className = 'avatar-crop-hint';
  coreSetUIText(notice, 'textContent', corePhrase("国内镜像始终直连。官方源遵循设置中的下载代理。TTS 和 ASR 暂停后可换源续传，两种来源校验同一份模型。"));
  const actions = document.createElement('div'); actions.className = 'avatar-crop-actions';
  const cancel = document.createElement('button'); cancel.type = 'button';
  coreSetUIText(cancel, 'textContent', corePhrase("取消"));
  const start = document.createElement('button'); start.type = 'button';
  start.className = 'primary'; coreSetUIText(start, 'textContent', corePhrase("开始下载"));
  actions.append(cancel, start); dialog.append(title, text, label, notice, actions);
  coreSetUIAttribute(dialog, 'aria-label', [corePhrase("下载推荐 "), kind, corePhrase(" 模型")]);
  let result: string | null = null;
  cancel.addEventListener('click', () => dialog.close());
  start.addEventListener('click', () => { result = select.value; dialog.close(); });
  document.body.append(dialog);
  try {
    dialog.showModal(); select.focus();
    await new Promise<void>(resolve => {
      dialog.addEventListener('close', () => resolve(), { once: true });
    });
    return result;
  } finally { dialog.remove(); opener?.focus(); }
}
