/** Display provenance uses the host builtin flag and recorded GitHub origin. */
export type PluginOrigin = 'Core'|'GitHub'|'本地导入'|'来源待确认';
export function githubRepository(value:string):string {
  return /^https:\/\/github\.com\/([\w.-]+\/[\w.-]+?)(?:\.git)?(?:\/|$)/.exec(value)?.[1] || '';
}
export function pluginOrigin(plugin:{builtin?:boolean;repository?:string;origin?:{url?:string}},repository=''):PluginOrigin {
  if(plugin.builtin===true)return 'Core';
  if(githubRepository(plugin.origin?.url||'')||githubRepository(plugin.repository||'')||githubRepository(repository))return 'GitHub';
  return '本地导入';
}
export const pluginOriginOrder=(group:PluginOrigin)=>({'Core':0,'GitHub':1,'本地导入':2,'来源待确认':3}[group]);
