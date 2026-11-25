import {pageFetch as fetch} from './status-json';
// Labels for the stock adapter also make the catalogue useful before first enablement.
// Only the server's published declarations can mark a tool as registered.
const QQ = 'core-infinity-qq';
const guide = [
  ['消息与语音', 'qq_send_text', '发送文字'], ['消息与语音', 'qq_send_reply', '引用并回复消息'],
  ['消息与语音', 'qq_send_voice', '合成并发送语音'], ['消息与语音', 'qq_read_group_history', '回查共同群聊记录'],
  ['图片与表情', 'qq_send_image', '发送插件生成的图片'], ['图片与表情', 'qq_look_image', '查看收到的图片'],
  ['图片与表情', 'qq_send_face', '发送 QQ 原生表情'], ['图片与表情', 'qq_list_custom_faces', '列出收藏表情'],
  ['图片与表情', 'qq_inspect_custom_face', '识别表情包内容'], ['图片与表情', 'qq_collect_sticker', '收藏表情包'],
  ['图片与表情', 'qq_correct_sticker', '按 owner 的纠正更新表情资料'], ['图片与表情', 'qq_send_custom_face', '发送收藏表情'],
  ['图片与表情', 'qq_send_saved_image', '发回收到的图片或 GIF'],
  ['主动分享', 'qq_sharing_preview', '审核拟分享的内容'], ['主动分享', 'qq_share_to_owner', '向 owner 分享'],
] as const;
interface Tool { name: string; provider: string; description: string; parameters: string[]; required: string[]; }
interface GuideEntry {group: string; name: string; title: string}
interface Catalog { guide?: GuideEntry[]; plugins: Record<string, {enabled: boolean; loaded: boolean}>; tools: Tool[]; }

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text = '') {
  const item = document.createElement(tag); item.className = cls; item.textContent = text; return item;
}

export function qqToolbox() {
  const root = el('div', 'infinity-pane infinity-pane-single');
  const card = el('section', 'infinity-voice-card qq-diagnostic-card qq-tools-card');
  const header = el('div', 'qq-diagnostic-heading');
  const status = el('span', 'qq-diagnostic-badge');
  status.setAttribute('role', 'status');
  const reload = el('button', 'btn ghost', '刷新');
  reload.type = 'button';
  header.append(el('h3', '', 'QQ 工具集'), status, reload);
  const search = el('input', 'qq-tools-search');
  search.type = 'search'; search.placeholder = '搜索用途或工具名'; search.setAttribute('aria-label', search.placeholder);
  const list = el('div', 'qq-tools-groups');
  card.append(header, el('p', 'qq-diagnostic-description', '已注册的工具仍受当前会话、账号权限和服务配置限制。展开可查看说明和参数。'), search, list);
  root.append(card);
  let data: Catalog | undefined;
  let state = '正在读取';
  let serial = 0;
  function render() {
    const live = data?.tools.filter(tool => tool.provider === QQ) || [];
    const index = new Map(live.map(tool => [tool.name, tool]));
    const labels = data?.guide?.length ? data.guide : guide.map(([group, name, title]) => ({group, name, title}));
    const rows: {group: string; name: string; title: string; tool?: Tool}[] =
      labels.map(row => ({...row, tool: index.get(row.name)}));
    for (const tool of live) {
      if (rows.some(row => row.name === tool.name)) continue;
      rows.push({group: '其他 QQ 工具', name: tool.name, title: tool.name, tool});
    }
    const query = search.value.trim().toLowerCase();
    const filtered = rows.filter(row => (row.title + ' ' + row.name + ' ' + row.group).toLowerCase().includes(query));
    list.replaceChildren();
    status.textContent = state;
    for (const group of new Set(filtered.map(row => row.group))) {
      const section = el('section', 'qq-tools-section');
      section.append(el('h4', '', group));
      for (const row of filtered.filter(row => row.group === group)) {
        const item = el('details', 'qq-tool-item');
        const line = el('summary');
        const label = el('span', 'qq-tool-label');
        label.append(el('span', '', row.title), el('code', '', row.name));
        line.append(label, el('small', row.tool ? 'qq-tool-on' : '', row.tool ? '已注册' : data ? '未注册' : '待核实'));
        item.append(line);
        if (row.tool) {
          item.append(el('p', '', row.tool.description));
          if (row.tool.parameters.length) item.append(el('p', 'qq-tool-params', '参数 · ' +
            row.tool.parameters.map(p => p + (row.tool!.required.includes(p) ? '（必填）' : '')).join('、')));
        } else {
          item.append(el('p', '', '官方 QQ 插件的功能说明。当前注册状态以本 Bot 加载的插件和配置为准。'));
        }
        section.append(item);
      }
      list.append(section);
    }
    if (!filtered.length) list.append(el('p', 'qq-diagnostic-note', '没有匹配的工具。'));
  }
  async function refresh() {
    const id = ++serial; reload.disabled = true;
    try {
      const response = await fetch('/infinity/qq/tools', {cache:'no-store'});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const result: Catalog = await response.json();
      if (!result.plugins || !Array.isArray(result.tools)) throw new Error('响应格式无效');
      if (id !== serial) return;
      data = result;
      const plugin = result.plugins[QQ];
      state = !plugin ? 'QQ 未安装' : !plugin.enabled ? 'QQ 已停用' : !plugin.loaded ? 'QQ 尚未加载'
        : 'QQ 已注册 ' + result.tools.filter(t => t.provider === QQ).length + ' 项';
    } catch {
      if (id !== serial) return;
      data = undefined; state = '注册状态暂不可用';
    } finally {
      if (id === serial) { reload.disabled = false; render(); }
    }
  }
  search.addEventListener('input', render);
  reload.addEventListener('click', () => void refresh());
  render();
  return {root, refresh};
}
