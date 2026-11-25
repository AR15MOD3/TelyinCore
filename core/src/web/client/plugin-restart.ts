/** Runtime restart requirements come only from the host, never inferred from version changes. */
import { pageFetch as fetch } from './status-json';
import type { InstalledPlugin } from './plugins-page';
let pending: readonly InstalledPlugin[] = [];
let request: Promise<void>|undefined;
let requested: string|undefined;
let stale = false;
export function acceptPluginRuntime(rows: readonly InstalledPlugin[]) {
  pending=rows.filter(row=>row.installed!==false&&row.restart_required===true);stale=false;paint();
}
export function takePluginRestartRequest(){const value=requested;requested=undefined;return value;}
export function openPluginRestart(name?:string){
  requested=name||pending[0]?.name;
  if(location.hash!=='#/infinity')location.hash='#/infinity';
  else window.dispatchEvent(new Event('plugin-restart-open'));
}
function paint(){
  const button=document.querySelector<HTMLButtonElement>('.topbar-plugin-restart');
  if(!button)return;
  button.hidden=!pending.length;
  button.textContent=`插件待重启 ${pending.length}${stale?' · 待确认':''}`;
  button.title=stale?'状态刷新失败，显示上次确认的待生效插件':pending.map(p=>p.displayName||p.name).join('、')+'：重启当前 Bot 的 Core 服务后生效';
}
async function read(){
  if(request)return request;
  request=(async()=>{try{const response=await fetch('/plugins',{cache:'no-store'});if(!response.ok)throw Error();const data=await response.json();if(!data.wired||!Array.isArray(data.plugins))throw Error();acceptPluginRuntime(data.plugins);}catch{stale=true;paint();}})().finally(()=>{request=undefined;});
  return request;
}
export function startPluginRestart(){
  const row=document.querySelector('.topbar-status');
  if(!row||row.querySelector('.topbar-plugin-restart'))return;
  const button=document.createElement('button');button.type='button';button.className='topbar-update topbar-plugin-restart';button.hidden=true;button.onclick=()=>openPluginRestart();row.append(button);
  void read();window.setInterval(()=>{if(!document.hidden)void read();},60000);
  window.addEventListener('plugins-changed',()=>{void read();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void read();});
}
