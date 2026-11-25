import { showTextDetail } from './text-detail';
type Face = { id: string; url: string; description: string; tags: string[]; reason: string; addedBy?: string; editedReason?: boolean };

export function qqStickers() {
  function element<K extends keyof HTMLElementTagNameMap>(tag: K, text = '') {
    const e = document.createElement(tag);
    if (text) e.textContent = text;
    return e;
  }
  function button(text: string, click: () => void) {
    const b = element('button', text);
    b.type = 'button'; b.className = 'infinity-action';
    b.addEventListener('click', click);
    return b;
  }
  const root = element('section');
  root.className = 'infinity-pane infinity-pane-single qq-stickers';
  const library = element('div'); library.className = 'infinity-settings-card qq-sticker-library';
  const heading = element('div'); heading.className = 'qq-sticker-heading';
  const count = element('span'); count.className = 'qq-sticker-count';
  heading.append(element('h3', '表情包'), count);
  const notice = element('p'); notice.className = 'infinity-hint'; notice.setAttribute('role', 'status');
  const toolbar = element('div'); toolbar.className = 'qq-sticker-toolbar';
  const collection = element('div'); collection.className = 'qq-sticker-grid';
  const editor = element('form'); editor.hidden = true; editor.className = 'qq-sticker-editor';
  const title = element('h3');
  const file = element('input'); file.type = 'file'; file.accept = 'image/gif,image/png,image/jpeg,image/webp';
  const desc = element('textarea'); desc.maxLength = 300; desc.rows = 2;
  const tags = element('input'); tags.placeholder = '用逗号分隔';
  const reason = element('textarea'); reason.rows = 2; reason.maxLength = 300;
  function field(name: string, input: HTMLElement) {
    const label = element('label', name); label.className = 'infinity-field'; label.append(input); return label;
  }
  const upload = element('label'); upload.className = 'qq-sticker-upload';
  const image = element('img'); image.hidden = true; image.alt = '待添加的表情包';
  const pick = element('strong', '选择图片'); const meta = element('small', '也可以把图片拖到这里');
  const choose = element('span'); choose.className = 'qq-sticker-file-action'; choose.textContent = '浏览文件';
  const fileInfo = element('div'); fileInfo.className = 'qq-sticker-file-info'; fileInfo.append(pick,meta);
  upload.append(file, image, fileInfo, choose);
  const format = element('p', 'PNG、JPEG、GIF 或 WebP，最大 8 MiB。描述、标签和使用想法由 Bot 生成。');
  format.className = 'infinity-hint';
  const uploadArea = element('div'); uploadArea.append(upload,format);
  const textFields = element('div'); textFields.className = 'qq-sticker-edit-fields';
  textFields.append(field('描述',desc),field('标签',tags),field('收藏理由或备注',reason));
  let selectedFile: File | null = null, previewURL = '';
  function releaseFile() {
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = ''; selectedFile = null; file.value = '';
    image.removeAttribute('src'); image.hidden = true;
    pick.textContent = '选择图片'; meta.textContent = '也可以把图片拖到这里'; choose.textContent = '浏览文件';
  }
  function selectFile(value?: File) {
    if (!value) return;
    releaseFile(); notice.textContent = '';
    if (!value.size || value.size > 8*1024*1024 ||
        (value.type && !file.accept.split(',').includes(value.type))) {
      notice.textContent = '请选择支持格式的图片，大小须在 8 MiB 以内'; return;
    }
    selectedFile = value; previewURL = URL.createObjectURL(value);
    image.src = previewURL; image.hidden = false; pick.textContent = value.name;
    meta.textContent = Math.max(1, Math.round(value.size / 1024)) + ' KiB'; choose.textContent = '更换图片';
  }
  file.addEventListener('change', () => selectFile(file.files?.[0]));
  upload.addEventListener('dragover', e => {e.preventDefault(); if (!busy) upload.classList.add('is-over');});
  upload.addEventListener('dragleave', () => upload.classList.remove('is-over'));
  upload.addEventListener('drop', e => {
    e.preventDefault(); upload.classList.remove('is-over'); if (!busy) selectFile(e.dataTransfer?.files[0]);
  });
  let current: Face | undefined, bot = '', account = '', faces: Face[] = [], busy = false;
  let loaded = false;
  function edit(face?: Face) {
    current = face; releaseFile(); notice.textContent = ''; uploadArea.hidden = !!face; textFields.hidden = !face;
    title.textContent = face ? '编辑表情包' : '添加表情包';
    save.textContent = face ? '保存修改' : '识别并添加';
    desc.value = face?.description || ''; tags.value = face?.tags.join('，') || '';
    reason.value = face?.reason || ''; editor.hidden = false;
    (face ? desc : file).focus();
  }
  async function api(body: object) {
    const r = await fetch('/infinity/qq/stickers', { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({bot, account, ...body}), cache:'no-store' });
    const value = await r.json();
    if (!r.ok || !value.ok) throw new Error(value.said || '收藏操作失败');
    return value;
  }
  async function run(task: () => Promise<void>) {
    if (busy) return;
    busy = true; root.setAttribute('aria-busy','true');
    const controls = Array.from(root.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLTextAreaElement>('button,input,textarea'));
    controls.forEach(c => c.disabled = true);
    try { await task(); } catch (e) { notice.textContent = e instanceof Error ? e.message : '收藏操作失败'; }
    finally { busy = false; root.removeAttribute('aria-busy'); controls.forEach(c => c.disabled = false); auto.disabled = savedAuto === undefined; }
  }
  const preference = element('label'); preference.className = 'infinity-toggle qq-sticker-preference';
  const auto = element('input'); auto.type = 'checkbox'; auto.disabled = true;
  preference.append(auto, element('span','允许 Bot 自主收藏表情包'));
  let savedAuto: boolean | undefined;
  auto.addEventListener('change', () => {
    const enabled = auto.checked;
    void run(async () => {
      try {
        const updated = await api({action:'setAutoCollect', enabled});
        savedAuto = updated.autoCollect; notice.textContent = enabled ? '已开启自主收藏' : '已关闭自主收藏';
      } finally { auto.checked = savedAuto === true; }
    });
  });
  const search = element('input'); search.type = 'search'; search.placeholder = '搜索描述、标签或收藏理由';
  search.setAttribute('aria-label',search.placeholder);
  function render() {
    collection.replaceChildren();
    const term = search.value.trim().toLocaleLowerCase();
    for (const face of faces) {
      if (term && ![face.description, ...face.tags, face.reason].join(' ').toLocaleLowerCase().includes(term)) continue;
      const card = element('article'); card.className = 'qq-sticker-card';
      const preview = element('div'); preview.className = 'qq-sticker-preview';
      if (face.url) {
        const img = element('img'); img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
        img.alt = face.tags.slice(0, 2).join('、') || '表情包';
        img.src = face.url; preview.append(img);
      } else { preview.textContent = '暂无预览'; }
      const summary = element('p', face.description || '暂无描述');
      summary.className = 'qq-sticker-summary';
      const chips = element('div'); chips.className = 'qq-sticker-tags';
      for (const tag of face.tags.slice(0, 3)) chips.append(element('span', tag));
      if (face.tags.length > 3) chips.append(element('small', '+' + (face.tags.length - 3)));
      const body = element('div'); body.className = 'qq-sticker-body'; body.append(summary, chips);
      const top = element('div'); top.className = 'qq-sticker-overview'; top.append(preview, body);
      const details = element('button', '查看详情'); details.className = 'qq-sticker-details';
      details.type = 'button';
      const fields = element('dl');
      for (const [label, value] of [
        ['添加来源', face.addedBy || '账号原有·来源未记录'],
        ['完整描述', face.description || '暂无描述'],
        ['标签', face.tags.join(' · ') || '暂无标签'],
        [face.editedReason ? '手动备注' : face.addedBy === 'Bot 自主收藏' ? '收藏理由' : '使用想法', face.reason || '尚未记录'],
      ]) fields.append(element('dt', label), element('dd', value));
      details.onclick = () => showTextDetail('表情详情', Array.from(fields.children).map(e => e.textContent || '').join('\n'));
      const actions = element('div'); actions.className = 'qq-sticker-actions';
      actions.append(button('编辑', () => edit(face)), button('删除', () => {
        if (!window.confirm('从当前 QQ 账号删除这张收藏表情？')) return;
        void run(async () => { await api({action:'delete',id:face.id}); editor.hidden = true; await load(); });
      }));
      const footer = element('div'); footer.className = 'qq-sticker-footer';
      footer.append(details, actions); card.append(top, footer); collection.append(card);
    }
    count.textContent = term ? collection.childElementCount + ' / ' + faces.length + ' 张' : faces.length + ' 张';
    if (!collection.childElementCount) collection.append(element('p',term ? '没有匹配的表情' : '暂无收藏表情'));
  }
  async function load() {
    const pref = await api({action:'preferences'});
    if (bot && bot !== pref.bot) { releaseFile(); editor.hidden = true; }
    bot = pref.bot; savedAuto = pref.autoCollect; auto.checked = pref.autoCollect;
    const data = await api({action:'list'});
    if (account && (account !== data.account || bot !== data.bot)) { releaseFile(); editor.hidden = true; }
    bot = data.bot; account = data.account; faces = data.items; loaded = true;
    notice.textContent = ''; render();
  }
  function readFile(f: File): Promise<string> {
    return new Promise((resolve,reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('图片读取失败'));
      reader.readAsDataURL(f);
    });
  }
  const save = element('button','保存'); save.className = 'infinity-action'; save.type = 'submit';
  editor.addEventListener('submit', e => {
    e.preventDefault();
    void run(async () => {

      let duplicate = false;
      if (current) await api({action:'edit',id:current.id,description:desc.value.trim(),
        tags:tags.value.split(/[，,、]/).map(v => v.trim()).filter(Boolean), reason:reason.value.trim()});
      else {
        const f = selectedFile;
        if (!f || f.size > 8*1024*1024) throw new Error('请选择 8 MiB 以内的图片');
        notice.textContent = 'Bot 正在看图并整理标签…';
        save.textContent = '正在识别…';
        try { duplicate = (await api({action:'add', file:await readFile(f)})).duplicate; }
        finally { save.textContent = '识别并添加'; }
      }
      releaseFile(); editor.hidden = true; await load();
      notice.textContent = duplicate ? '这张图已经收藏过了' : current ? '已保存修改' : '已添加表情包';
    });
  });
  const formActions = element('div'); formActions.className = 'qq-sticker-actions';
  formActions.append(button('取消', () => {releaseFile(); editor.hidden=true; notice.textContent='';}), save);
  save.classList.add('qq-primary-action');
  editor.append(title, uploadArea, textFields, formActions);
  search.addEventListener('input',render);
  const add = button('添加表情包', () => {
    if (loaded) edit(); else void run(async () => {await load();edit();});
  });
  add.classList.add('qq-primary-action');
  toolbar.append(search,button('刷新',() => {void run(load);}),add);
  heading.append(preference);
  library.append(heading,toolbar,notice,editor,collection);
  root.append(library);
  return {root, open: () => { if (editor.hidden) void run(load); }};
}
