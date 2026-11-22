import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
/** Local-only crop preview; upload is deferred until explicit confirmation. */
export function cropBounds(width: number, height: number, zoom: number, x: number, y: number) {
  const side = Math.min(width, height) / Math.max(1, zoom);
  return { side, sx: Math.max(0, Math.min(width - side, x - side / 2)),
    sy: Math.max(0, Math.min(height - side, y - side / 2)) };
}

export async function cropAvatar(file: File,
  save: (blob: Blob, signal?: AbortSignal) => Promise<boolean>): Promise<void> {
  if (file.size > 20 * 1024 * 1024) throw new Error('图片不能超过 20 MB。');
  const image = await createImageBitmap(file);
  if (image.width * image.height > 40000000) {
    image.close(); throw new Error('图片尺寸过大，请先缩小图片。');
  }
  const previous = document.activeElement as HTMLElement | null;
  const dialog = document.createElement('dialog');
  coreSetUIAttribute(dialog, 'aria-label', corePhrase("裁剪头像"));
  dialog.className = 'avatar-crop-dialog';
  const title = document.createElement('h2'); coreSetUIText(title, 'textContent', corePhrase("裁剪头像"));
  const hint = document.createElement('p'); coreSetUIText(hint, 'textContent', corePhrase("拖动调整位置，滚轮或滑杆缩放；方向键也可移动。"));
  hint.className = 'avatar-crop-hint';
  const canvas = document.createElement('canvas'); canvas.width = 300; canvas.height = 300;
  canvas.tabIndex = 0; coreSetUIAttribute(canvas, 'aria-label', corePhrase("头像裁剪区域，使用方向键调整位置"));
  canvas.className = 'avatar-crop-canvas';
  const ctx = canvas.getContext('2d');
  if (!ctx) { image.close(); throw new Error('浏览器无法预览图片。'); }
  let x = image.width / 2; let y = image.height / 2; let zoom = 1; let busy = false;
  let closed = false;
  let upload: AbortController | null = null;
  let drag: { id: number; x: number; y: number } | null = null;
  const draw = (): void => {
    const box = cropBounds(image.width, image.height, zoom, x, y);
    x = box.sx + box.side / 2; y = box.sy + box.side / 2;
    ctx.clearRect(0, 0, 300, 300);
    ctx.drawImage(image, box.sx, box.sy, box.side, box.side, 0, 0, 300, 300);
  };
  const label = document.createElement('label'); coreSetUIText(label, 'textContent', corePhrase("缩放 "));
  label.className = 'avatar-crop-zoom';
  const range = document.createElement('input'); range.type = 'range';
  range.min = '1'; range.max = '4'; range.step = 'any'; range.value = '1';
  coreSetUIAttribute(range, 'aria-label', corePhrase("头像缩放")); label.append(range);
  let targetZoom = zoom;
  let zoomFrame = 0;
  let previousFrame = 0;
  const stopZoom = (): void => {
    cancelAnimationFrame(zoomFrame); zoomFrame = 0; previousFrame = 0;
    targetZoom = zoom;
  };
  const animateZoom = (time: number): void => {
    const elapsed = previousFrame ? Math.min(64, time - previousFrame) : 16;
    previousFrame = time;
    zoom += (targetZoom - zoom) * (1 - Math.exp(-elapsed / 75));
    if (Math.abs(targetZoom - zoom) < 0.0001) zoom = targetZoom;
    range.value = String(zoom); draw();
    if (zoom !== targetZoom) zoomFrame = requestAnimationFrame(animateZoom);
    else { zoomFrame = 0; previousFrame = 0; }
  };
  range.addEventListener('input', () => {
    stopZoom(); zoom = Number(range.value); targetZoom = zoom; draw();
  });
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    if (busy) return;
    const delta = event.deltaY * (event.deltaMode === 1 ? 16
      : event.deltaMode === 2 ? canvas.clientHeight : 1);
    targetZoom = Math.max(1, Math.min(4, targetZoom * Math.exp(
      -Math.max(-120, Math.min(120, delta)) * 0.0008)));
    if (!zoomFrame) zoomFrame = requestAnimationFrame(animateZoom);
  }, { passive: false });
  canvas.addEventListener('pointerdown', event => {
    if (busy) return;
    stopZoom();
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId || busy) return;
    const factor = Math.min(image.width, image.height) / zoom
      / canvas.getBoundingClientRect().width;
    x -= (event.clientX - drag.x) * factor; y -= (event.clientY - drag.y) * factor;
    drag.x = event.clientX; drag.y = event.clientY; draw();
  });
  for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    canvas.addEventListener(event, () => { drag = null; });
  }
  canvas.addEventListener('keydown', event => {
    if (busy) return;
    const direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0],
      ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (!direction) return;
    event.preventDefault(); const step = Math.min(image.width, image.height) / zoom / 50;
    x += direction[0] * step; y += direction[1] * step; draw();
  });
  const status = document.createElement('p'); status.setAttribute('role', 'status');
  const actions = document.createElement('div');
  actions.className = 'avatar-crop-actions';
  const button = (text: string): HTMLButtonElement => {
    const result = document.createElement('button'); result.type = 'button';
    result.textContent = text; actions.append(result);
    return result;
  };
  const reset = button('重置'); const cancel = button('取消'); const confirm = button('保存头像');
  confirm.className = 'primary';
  const kicker = document.createElement('span');
  kicker.className = 'avatar-crop-kicker'; coreSetUIText(kicker, 'textContent', corePhrase("头像设置"));
  reset.addEventListener('click', () => {
    stopZoom(); targetZoom = 1;
    x = image.width / 2; y = image.height / 2; zoom = 1; range.value = '1'; draw();
  });
  cancel.addEventListener('click', () => { upload?.abort(); dialog.close(); });
  dialog.addEventListener('cancel', () => { upload?.abort(); });
  confirm.addEventListener('click', () => {
    if (busy) return;
    stopZoom();
    busy = true; range.disabled = reset.disabled = confirm.disabled = true;
    upload = new AbortController();
    const signal = upload.signal;
    coreSetUIText(cancel, 'textContent', corePhrase("取消等待"));
    coreSetUIText(status, 'textContent', corePhrase("正在保存…取消等待不会撤回服务器已经完成的保存。"));
    void (async () => {
      try {
        const output = document.createElement('canvas'); output.width = output.height = 256;
        const target = output.getContext('2d');
        if (!target) throw new Error('无法生成裁剪图片。');
        const box = cropBounds(image.width, image.height, zoom, x, y);
        target.drawImage(image, box.sx, box.sy, box.side, box.side, 0, 0, 256, 256);
        const blob = await new Promise<Blob | null>(resolve => output.toBlob(resolve, 'image/png'));
        if (closed || signal.aborted) return;
        if (!blob || !await save(blob, signal)) throw new Error('保存失败，请检查服务后重试。');
        if (closed || signal.aborted) return;
        dialog.close();
      } catch (error) { if (!closed &&
        !signal.aborted) coreSetUIText(status, 'textContent', (error instanceof Error ? error.message : corePhrase("保存失败。"))); }
      finally {
        if (!closed) {
          busy = false; range.disabled = reset.disabled = confirm.disabled = false;
          coreSetUIText(cancel, 'textContent', corePhrase("取消"));
        }
      }
    })();
  });
  dialog.append(kicker, title, hint, canvas, label, status, actions); document.body.append(dialog);
  try {
    draw(); dialog.showModal(); canvas.focus();
    await new Promise<void>(resolve => {
      dialog.addEventListener('close', () => resolve(), { once: true });
    });
  } finally { closed = true; upload?.abort(); stopZoom(); image.close(); dialog.remove();
    previous?.focus(); }
}
