/** One explicit operation survives service disconnects without reloading the page.
 * Only reads retry. Mutation failures are reconciled, never blindly retransmitted.
 */
import { pageFetch as fetch } from './status-json';
import type { InstalledPlugin, PluginsAnswer, PluginRuntime } from './plugins-page';
import { acceptPluginRuntime } from './plugin-restart';
export type PluginAction = 'enable'|'disable'|'remove'|'apply';
type Phase = 'changing'|'restarting'|'reconnecting'|'verifying'|'removing'|'complete'|'failed'|'uncertain';
export interface PluginOperation {name:string;action:PluginAction;phase:Phase;text:string;busy:boolean;}
let state:PluginOperation|undefined;
let continuation:(()=>Promise<void>)|undefined;
export const pluginOperation=()=>state;
const update=(phase:Phase,text:string,busy=true)=>{if(state)state={...state,phase,text,busy};window.dispatchEvent(new Event('plugin-operation-changed'));};
const sleep=(ms:number)=>new Promise<void>(resolve=>setTimeout(resolve,ms));
async function read():Promise<PluginsAnswer>{
  const r=await fetch('/plugins',{},4000);if(!r.ok)throw Error('无法读取插件状态');
  const data=await r.json() as PluginsAnswer;if(!data.wired||!Array.isArray(data.plugins))throw Error('插件状态未接入');
  acceptPluginRuntime(data.plugins);return data;
}
const find=(data:PluginsAnswer,name:string)=>data.plugins.find(p=>p.name===name&&p.installed!==false);
function samePackage(before:InstalledPlugin,after:InstalledPlugin){
  if(before.package_id&&after.package_id)return before.package_id===after.package_id;
  return before.version===after.version&&before.where===after.where;
}
function sameBot(before:PluginRuntime,after?:PluginRuntime){
  if(!after?.instance||before.bot!==after.bot)throw Error('连接到了不同的 Bot，操作已停止，请核对服务。');
}
class RejectedOperation extends Error {}
async function post(path:string,body:object,instance?:string){
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),10000);
  try{
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json',...(instance?{'X-Telyin-Shutdown-Instance':instance}:{})},body:JSON.stringify(body),signal:controller.signal});
    const answer=await response.json();if(!response.ok)throw new RejectedOperation(answer.said||answer.error||`请求失败（${response.status}）`);return answer;
  }finally{clearTimeout(timer);}
}
function finish(data:PluginsAnswer,name:string,action:PluginAction){
  const p=find(data,name);
  if(action==='remove'){
    if(p)throw Error('插件仍在安装列表中，卸载尚未完成。');
    update('complete','已卸载，配置和缓存按原有规则保留。',false);
  }else{
    if(!p)throw Error('插件已不在安装列表中，请检查是否被其他页面更改。');
    if(p.lifecycle_error)throw Error(p.lifecycle_error);
    if(p.restart_required)throw Error('插件仍有待生效更改，不能确认操作完成。');
    if(action==='enable'&&(!p.enabled||!p.loaded))throw Error('插件尚未正常运行，请打开配置查看缺失项或错误。');
    if(action==='disable'&&(p.enabled||p.loaded||p.tracked))throw Error('插件尚未完全停用，实例仍需清理。');
    if(action==='apply'&&p.enabled&&!p.loaded)throw Error('重启后插件未正常运行，请检查配置和日志。');
    update('complete',action==='disable'?'已停用。':action==='enable'?'已启用，插件正常运行。':'重启完成，更改已生效。',false);
  }
  continuation=undefined;window.dispatchEvent(new Event('plugins-changed'));
}
async function afterRestart(before:PluginsAnswer,p:InstalledPlugin,action:PluginAction){
  const old=before.runtime!;const deadline=Date.now()+180000;
  update('reconnecting','等待 Bot 服务重新连接，当前页面会保留。');
  while(Date.now()<deadline){
    let next:PluginsAnswer;
    try{next=await read();}catch{await sleep(1200);continue;}
    sameBot(old,next.runtime);
    if(next.runtime!.instance===old.instance){await sleep(1200);continue;}
    const current=find(next,p.name);
    if(!current){if(action==='remove'){finish(next,p.name,action);return;}throw Error('重启后找不到此插件。');}
    if(!samePackage(p,current))throw Error('重启期间插件安装包发生变化，已停止后续操作。');
    update('verifying','已重新连接，正在核对插件实际状态…');
    if(action==='remove'){
      if(current.enabled||current.loaded||current.tracked||current.restart_required)throw Error('重启后插件尚未完全停止，已保留安装包。');
      update('removing','插件已停止，正在完成卸载…');
      try{
        const answer=await post('/plugins/remove',{name:p.name,confirm_shared:true},next.runtime!.instance);
        if(!answer.ok)throw new RejectedOperation(answer.said||'卸载尚未完成，安装包已保留。');
      }catch(error){
        if(error instanceof RejectedOperation)throw error;
        const observed=await read();sameBot(old,observed.runtime);
        if(find(observed,p.name))throw Error('卸载结果尚未确认，安装列表中仍有此插件；没有重复提交。');
      }
      next=await read();sameBot(old,next.runtime);
    }
    finish(next,p.name,action);return;
  }
  update('uncertain','尚未确认 Bot 已重新启动。页面和操作记录已保留，可继续检查；不会重复发送重启请求。',false);
}
export async function continuePluginOperation(){
  if(state?.busy||!continuation)return;
  try{await continuation();}catch(e){update('failed',e instanceof Error?e.message:String(e),false);}
}
export async function runPluginOperation(name:string,action:PluginAction){
  if(state?.busy)throw Error('请等待当前插件操作完成。');
  continuation=undefined;state={name,action,phase:'changing',text:'正在读取并核对插件状态…',busy:true};update('changing',state.text);
  try{
    const before=await read();const p=find(before,name);if(!p)throw Error('插件未安装，请刷新列表。');
    let next=before;
    const predictableRestart=(action==='apply'&&p.restart_required)||(p.reload_mode==='restart'&&(action==='enable'?!p.loaded:action==='disable'||action==='remove'?(p.loaded||p.tracked||p.restart_action==='disable'):false));
    if(predictableRestart&&!before.runtime?.restart_supported)throw Error(before.runtime?.restart_reason||'当前启动器不支持页面内重启，请先更新并重启启动器。');
    if(action!=='apply'){
      update('changing',action==='remove'?'正在停止插件并检查卸载条件…':action==='enable'?'正在启用插件…':'正在停用插件…');
      let answer;
      try{answer=await post(action==='remove'?'/plugins/remove':'/plugins/switch',action==='remove'?{name,confirm_shared:true}:{name,on:action==='enable'},before.runtime?.instance);}
      catch(error){
        if(error instanceof RejectedOperation)throw error;
        // A missing response never causes a second state-changing request.
        const observed=await read();const actual=find(observed,name);
        const saved=action==='remove'?(!actual||(!actual.enabled&&actual.restart_action==='disable')):actual?.enabled===(action==='enable');
        if(!saved)throw Error('操作结果未能确认，请检查当前插件状态；没有重复提交请求。');
        answer={ok:true};
      }
      if(answer.restart_required&&answer.restart_local===false)throw Error(answer.said||'其他 Bot 仍需重启，安装包已保留。');
      if(answer.ok===false&&!answer.restart_required)throw Error(answer.said||'操作未完成');
      next=await read();
      if(before.runtime){sameBot(before.runtime,next.runtime);if(next.runtime!.instance!==before.runtime.instance)throw Error('操作期间服务已变化，请重新核对状态。');}
    }
    const current=find(next,name);
    if(current&& !samePackage(p,current))throw Error('插件包已变化，操作已停止。');
    if(!current?.restart_required){finish(next,name,action);return;}
    if(!next.runtime?.restart_supported)throw Error(next.runtime?.restart_reason||'设置已保存，当前启动器不支持页面内重启。');
    const restartAction=action==='apply'?(current.restart_action==='config'?'config':current.restart_action):action;
    update('restarting','设置已保存，正在请求 Bot 安全退出并重新启动…');
    continuation=()=>afterRestart(next,p,action);
    // A lost acknowledgement is not proof of failure. Observe the new instance;
    // never send the same restart a second time merely because HTTP disconnected.
    try{await post('/plugins/restart',{name,action:restartAction},next.runtime.instance);}
    catch(e){if(e instanceof RejectedOperation)throw e;}
    await continuation();
  }catch(e){continuation=undefined;update('failed',e instanceof Error?e.message:String(e),false);}
  finally{window.dispatchEvent(new Event('plugins-changed'));}
}
