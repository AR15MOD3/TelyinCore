/** Browse categories describe the primary purpose, independently of origin and dependencies. */
export const pluginCategories = [
  ['all', '全部'], ['communication', '通讯接入'], ['creation', '内容创作'],
  ['information', '信息查询'], ['automation', '自动任务'], ['interaction', '互动体验'], ['uncategorized', '未分类'],
] as const;
type Category = typeof pluginCategories[number][0];
// Reviewed repository identities for packages published before category topics existed.
// Unknown repositories stay unclassified; prose and package-name prefixes are not authority.
const reviewed: Record<string, Category> = {
  'ar15mod3/infinity-comfyui': 'creation',
  'ar15mod3/core-infinity-wavesuid': 'information',
  'yluow/core-infinity-chaofan': 'automation',
  'yluow/jingjing-town': 'interaction',
};
export function pluginCategory(one: {full?: string; keywords?: readonly string[]}): Category {
  const declared=pluginCategories.filter(([id])=>id!=='all' && one.keywords?.includes(`category-${id}`));
  if(declared.length===1)return declared[0][0];
  if(declared.length>1)return 'uncategorized';
  return reviewed[(one.full||'').toLowerCase()]||'uncategorized';
}
export function pluginCategoryLabel(one: {full?:string;keywords?:readonly string[]}): string {
  return pluginCategories.find(([id])=>id===pluginCategory(one))![1];
}

/** A reviewed display note, not a loader dependency declaration. */
export function pluginRequirement(one: {full?: string}): string {
  return (one.full||'').toLowerCase()==='ar15mod3/infinity-comfyui'?'需启用 QQ 连接':'';
}
