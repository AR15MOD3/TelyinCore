/** Static assets follow the immutable generation of the loaded bundle. */
export function assetUrl(name: string): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name)) throw new Error('Invalid asset name');
  return new URL(name, import.meta.url).href;
}
