/** Display declarations, never infer host compatibility from a browser or description. */
export const platformNames = {windows:'Windows',linux:'Linux',macos:'macOS'} as const;
type Platform = keyof typeof platformNames;
export type PlatformInfo = {
  supported: Platform[]; conditional: Platform[]; note: string; source?: string;
};
export function pluginPlatforms(one:{repository:string;version?:string;keywords?:readonly string[]}):PlatformInfo {
  const supported=(Object.keys(platformNames) as Platform[]).filter(id=>one.keywords?.includes(`platform-${id}`));
  if(supported.length)return {supported,conditional:[],note:'来自作者的平台标签；未声明的平台不代表可用。'};
  // Reviewed README for this exact release, not a general rule for all versions.
  if(one.repository.toLowerCase()==='https://github.com/ar15mod3/infinity-comfyui' && one.version?.replace(/^v/,'')==='0.2.4')return {
    supported:['windows'],conditional:['linux'],
    note:'Windows 支持本机部署。Linux 仅连接 Windows 上的 ComfyUI；生图服务必须使用 Windows 秋叶整合包。macOS 未声明。',
    source:one.repository+'#运行环境',
  };
  return {supported:[],conditional:[],note:'作者尚未声明该版本的运行平台。安装前请查看项目说明，确认系统和依赖要求。'};
}
export function platformSummary(info:PlatformInfo):string {
  const values=[...info.supported.map(id=>platformNames[id]),...info.conditional.map(id=>platformNames[id]+'（有条件）')];
  return values.length?values.join(' / '):'平台未声明';
}
