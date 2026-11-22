import { assetUrl } from './asset-url';
import { corePhrase, coreSetUIText } from './ui-language';

export function voiceSetup(kind: 'asr' | 'tts', selection: () => { model: string; voice?: string }) {
  const section = document.createElement('details');
  section.className = 'qq-voice-setup';
  section.setAttribute('aria-label', kind === 'tts' ? 'TTS 首次配置' : 'ASR 首次配置');
  const summary = document.createElement('summary');
  summary.textContent = '首次配置';
  const checklist = document.createElement('ol');
  const steps = kind === 'tts' ? [
    '点击「下载推荐模型」自动安装模型、运行环境和默认参考音频；已有模型时点击「补全运行环境」。',
    '安装时按硬件准备推理依赖，预热参考音频并验证性能配置。',
    '合成自检通过后，选择模型和音色，保存语音设置即可启用。',
  ] : [
    '点击「下载推荐模型」自动安装模型、词表、VAD 和识别依赖；已有模型时点击「补全运行环境」。',
    '安装完成后自动使用随包语音进行转写自检。',
    '也可点击「运行自检」重新检查，或选择自己的 WAV（15 秒以内）。',
  ];
  for (const step of steps) {
    const item = document.createElement('li');
    coreSetUIText(item, 'textContent', corePhrase(step));
    checklist.append(item);
  }
  if (kind === 'tts') {
    const guide = document.createElement('a');
    guide.href = 'https://github.com/FunAudioLLM/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/README.md#install';
    guide.target = '_blank';
    guide.rel = 'noopener noreferrer';
    guide.textContent = 'CosyVoice 安装说明 ↗';
    guide.className = 'qq-voice-install-guide';
    checklist.children[1].append(guide);
  }
  const state = document.createElement('p');
  state.className = 'infinity-hint';
  const result = document.createElement('p');
  result.className = 'infinity-hint';
  result.setAttribute('role', 'status');
  const file = document.createElement('input');
  file.type = 'file';
  file.accept = '.wav,audio/wav';
  file.setAttribute('aria-label', '自选 WAV（可选，默认使用随包语音）');
  const check = document.createElement('button');
  check.type = 'button';
  check.className = 'infinity-action';
  check.textContent = '运行自检';
  const actions = document.createElement('div');
  actions.className = 'qq-voice-setup-actions';
  let bundled: Promise<File> | undefined;
  const bundledFile = (): Promise<File> => {
    if (!bundled) {
      bundled = fetch(assetUrl('asr-self-check.wav')).then(async response => {
        if (!response.ok) throw new Error('无法读取随包自检音频，请检查 Core 文件或选择自己的 WAV。');
        return new File([await response.blob()], 'asr-self-check.wav', { type: 'audio/wav' });
      }).catch(error => { bundled = undefined; throw error; });
    }
    return bundled;
  };
  if (kind === 'asr') {
    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'infinity-action';
    restore.textContent = '恢复默认音频';
    restore.hidden = true;
    const selectDefault = async () => {
      try {
        const sample = await bundledFile();
        if (file.files?.length) return;
        const transfer = new DataTransfer();
        transfer.items.add(sample);
        file.files = transfer.files;
        restore.hidden = true;
      } catch {
        result.textContent = '未能预选随包音频，可重试或选择自己的 WAV。';
      }
    };
    section.addEventListener('toggle', () => {
      if (section.open && !file.files?.length) void selectDefault();
    });
    file.addEventListener('change', () => {
      restore.hidden = !file.files?.length;
      if (restore.hidden) void selectDefault();
    });
    restore.addEventListener('click', () => { file.value = ''; void selectDefault(); });
    actions.append(file, restore);
  }
  actions.append(check);
  section.append(summary, checklist, state, actions, result);
  let passed = '';
  let pending = false;
  let blocked = false;
  const key = () => JSON.stringify(selection());
  check.addEventListener('click', async () => {
    if (pending || blocked || section.closest('fieldset')?.disabled) return;
    const selected = selection();
    const chosen = file.files?.[0];
    if (!selected.model || (kind === 'asr' && chosen && chosen.size > 1_500_000)) {
      result.textContent = !selected.model ? '请先选择模型。' : '请选择不超过 1.5 MB 的短 WAV。';
      return;
    }
    pending = check.disabled = true;
    passed = '';
    result.textContent = kind === 'tts' ? '正在加载模型并合成测试语音…' : '正在检测人声并转写…';
    try {
      const sample = chosen ?? (kind === 'asr' ? await bundledFile() : undefined);
      const audio = sample ? await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
        reader.onerror = reject;
        reader.readAsDataURL(sample);
      }) : undefined;
      const response = await fetch('/infinity/qq/voice-setup-check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, ...selected, audio }),
      });
      const answer = await response.json();
      if (!response.ok || !answer.ok) throw new Error(answer.said || '语音自检失败。');
      passed = JSON.stringify(selected);
      result.textContent = passed !== key() ? '原选择已通过，请对当前选择重新自检。'
        : (answer.transcript ? '转写通过 · ' + answer.transcript : '合成自检通过。');
    } catch (error) {
      result.textContent = error instanceof Error ? error.message : '语音自检失败。';
    } finally {
      pending = false;
      check.disabled = blocked;
    }
  });
  return {
    section,
    ready: () => passed === key(),
    require: () => {
      section.open = true;
      result.textContent = '首次启用前请完成自检。';
      summary.focus();
    },
    status(issue: string, unavailable = false) {
      blocked = unavailable;
      check.disabled = pending || blocked;
      state.hidden = !issue;
      state.textContent = issue;
    },
  };
}
