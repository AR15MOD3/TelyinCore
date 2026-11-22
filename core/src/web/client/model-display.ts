/** Display only: keep provider IDs intact, shorten filesystem model paths. */
export function modelDisplayName(model: string): string {
  const path = model.trim();
  const isFilePath = /^(?:[a-z]:[\\/]|\/|\\\\)/i.test(path);
  const full = isFilePath ? path.replace(/\\/g, '/').split('/').pop() || path : path;
  if (/^qwen[ -]*3\.8[ _-]*flash[ _-]*next\b/i.test(full)) {
    const quant = full.match(/\bI?Q\d(?:_[A-Z0-9]+)+\b/i)?.[0];
    return `Qwen3.8 FN${quant ? ` · ${quant.toUpperCase()}` : ''}`;
  }
  if (!isFilePath) return model;
  const filename = path.replace(/\\/g, '/').split('/').pop();
  if (!filename) return model;
  return filename.replace(/\.gguf$/i, '');
}
