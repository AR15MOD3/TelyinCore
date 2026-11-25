import {pluginVersions, type VersionChoice} from './plugin-versions';
import {openCoreUpdateDialog} from './package-updates';
import {pluginOrigin, pluginOriginOrder} from './plugin-origin';
import { runPluginOperation, continuePluginOperation, pluginOperation } from './plugin-operation';
import { acceptPluginRuntime, takePluginRestartRequest } from './plugin-restart';
import { icon, type Chrome, type Panel } from './shell';
import { pageFetch as fetch } from './status-json';
import { confirmAction } from './confirm-dialog';
import { assetUrl } from './asset-url';
import { PLUGIN_GUIDE_VERSION } from './plugin-guide-version';
import { pluginCategories, pluginCategory, pluginCategoryLabel, pluginRequirement } from './plugin-catalog';
import { platformNames, pluginPlatforms, platformSummary } from './plugin-platforms';
import { updateFor, updateText, checkPluginUpdates, takePluginUpdateRequest, pluginUpdateSnapshot } from './plugin-updates';
import type { InstalledPlugin, PluginsAnswer, MarketPackage, MarketAnswer } from './plugins-page';

type View='local'|'discover';
type Entry={name:string; local?:InstalledPlugin; market?:MarketPackage};
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
const button=(text:string,action:()=>void,cls='')=>{const b=node('button','pw-button '+cls,text);b.type='button';b.onclick=action;return b;};
const installed=(p?:InstalledPlugin)=>!!p&&p.installed!==false;
const runtime=(p:InstalledPlugin)=>{
  const operation=pluginOperation();
  if(operation?.busy&&operation.name===p.name)return {enable:'正在启用',disable:'正在停用',remove:'正在卸载',apply:'正在应用更改'}[operation.action];
  return p.installed===false?'未安装':p.contract?'不兼容':p.lifecycle_error?'运行异常':p.restart_required?(p.loaded?(p.restart_action==='config'?'运行中 · 配置待重启':'仍在运行 · 重启后停用'):p.restart_action==='enable'?'重启后启用':'重启后完成停用'):p.loaded?'运行中':p.enabled?'已启用 · 未运行':'已停用';
};
const repo=(entry:Entry)=>{
  if(entry.local?.builtin)return 'https://github.com/AR15MOD3/TelyinCore/tree/main/core/builtin/infinity/'+encodeURIComponent(entry.name);
  if(entry.market?.full&&/^[\w.-]+\/[\w.-]+$/.test(entry.market.full))return 'https://github.com/'+entry.market.full;
  const url=entry.local?.origin?.url||'';
  const match=/^https:\/\/github\.com\/([\w.-]+\/[\w.-]+?)(?:\.git)?(?:\/|$)/.exec(url);
  if(match)return 'https://github.com/'+match[1];
  if(entry.local?.repository)return entry.local.repository;
  const remote=updateFor(entry.name)?.repository;
  return remote&&/^[\w.-]+\/[\w.-]+$/.test(remote)?'https://github.com/'+remote:'';
};
const originGroup=(entry:Entry)=>pluginOrigin(entry.local||{},repo(entry));
const originOrder=(entry:Entry)=>pluginOriginOrder(originGroup(entry));
const platforms=(entry:Entry)=>pluginPlatforms({repository:repo(entry),version:entry.local?.version||entry.market?.version,keywords:entry.local?.keywords||entry.market?.keywords});

/** Separate overview and marketplace routes share the same Core list/detail language. */
export function pluginWorkspace(shell:Chrome,view:View='local'):Panel {
  const root=node('section','plugin-workspace page-world');
  root.dataset.pluginView=view;
  const header=node('header','pw-header');const heading=node('div','pw-heading');heading.append(node('h1','page-title',view==='local'?'插件总览 · PLUGIN OVERVIEW':'插件市场 · PLUGIN MARKETPLACE'));
  if(view==='discover'){const guide=node('a','pw-guide','开发规范手册 ↗');guide.href=assetUrl('infinity-plugin-spec.html');guide.target='_blank';guide.rel='noopener noreferrer';guide.title='插件开发规范 '+PLUGIN_GUIDE_VERSION;heading.append(guide);}header.append(heading);
  const scope=node('div','pw-scope');const scopeTitle=node('h2','',view==='local'?'内置 & 已安装':'浏览插件');const scopeCount=node('span','pw-scope-count');scope.append(scopeTitle,scopeCount);
  const body=node('div','pw-body');const browse=node('section','pw-browser');browse.setAttribute('aria-label','插件列表');
  const toolbar=node('div','pw-searchbar');const search=node('input');search.type='search';search.placeholder='搜索插件';search.setAttribute('aria-label','搜索插件');toolbar.append(icon('search'),search);
  const controls=node('div','pw-filters');controls.setAttribute('role','group');controls.setAttribute('aria-label','筛选插件');
  const refresh=button('刷新',()=>{void read();},'pw-quiet');
  const searchActions=node('div','pw-search-actions');
  const includeLocal=node('input');includeLocal.type='checkbox';includeLocal.setAttribute('aria-label','显示内置与已安装');
  if(view==='discover'){const label=node('label','pw-include-local');label.append(includeLocal,document.createTextNode('显示内置与已安装'));searchActions.append(label);}
  searchActions.append(refresh);
  const navigation=node('div','pw-navigation');navigation.append(scope,searchActions);
  const searchRow=node('div','pw-search-row');searchRow.append(toolbar);
  const searchTools=node('section','pw-search-tools');searchTools.setAttribute('aria-label','插件搜索与标签');searchTools.append(searchRow,controls);
  const feedback=node('p','pw-feedback');feedback.setAttribute('role','status');
  const list=node('div','pw-list');const footer=node('footer','page-footer pw-page-footer');browse.append(searchTools,feedback,list);
  const detail=node('section','pw-detail');detail.setAttribute('aria-label','插件详情');
  const restartSummary=node('div','pw-restart-summary');restartSummary.setAttribute('role','status');restartSummary.hidden=true;
  const progress=node('section','pw-operation-progress');progress.setAttribute('role','status');progress.setAttribute('aria-live','polite');progress.hidden=true;
  body.append(browse,detail);root.append(header,navigation,progress,restartSummary,body,footer);shell.content.replaceChildren(root);
  let selected='',locals:InstalledPlugin[]=[],market:MarketPackage[]=[],alive=false,epoch=0,busy=false;
  let localLoaded=false,marketLoaded=false,localError='',marketError='',operationError='',page=1;
  let lastDetail='',lastList='';
  let filterValue='all';
  let checkingUpdates=false;
  let requestedName:string|undefined;
  const filterButtons=new Map<string,HTMLButtonElement>();
  function setFilters(){
    filterValue='all';controls.replaceChildren();filterButtons.clear();
    search.placeholder=view==='discover'?'搜索插件、作者或 #标签':'搜索插件、作者或标签';
    const values=view==='discover'?pluginCategories:[['all','全部'],['running','运行中'],['disabled','已停用'],['available','可更新']];
    for(const [value,label] of values){
      const chip=button(label,()=>{if(busy||pluginOperation()?.busy)return;filterValue=filterValue===value?'all':value;page=1;lastList='';render();},'pw-filter-tag');
      chip.dataset.value=value;filterButtons.set(value,chip);controls.append(chip);
    }
  }
  function discoverable():Entry[]{
    // The catalog remains complete; this is a default display filter only.
    if(!localLoaded)return [];
    const rows:Entry[]=market.map(m=>({name:m.name,market:m,local:locals.find(p=>p.name===m.name)}));
    if(includeLocal.checked){
      const names=new Set(rows.map(e=>e.name));
      for(const p of locals)if(!names.has(p.name)){rows.push({name:p.name,local:p});names.add(p.name);}
      return rows;
    }
    return rows.filter(e=>!e.local?.builtin&&!installed(e.local));
  }
  function entries():Entry[]{
    const all=view==='discover'?discoverable():locals.map(p=>({name:p.name,local:p,market:market.find(m=>m.name===p.name)}));
    return all.filter(e=>{
      const tags=[...(e.local?.keywords||[]),...(e.market?.keywords||[]),...(e.market?[pluginCategory(e.market),pluginCategoryLabel(e.market)]:[])].map(tag=>tag.toLowerCase());
      const searchable=[e.name,e.local?.displayName,e.local?.description,e.market?.description,e.local?.author,e.market?.author,...tags].join(' ').toLowerCase();
      const terms=search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      if(!terms.every(term=>term.startsWith('#')?term.length===1||tags.some(tag=>tag===term.slice(1)):searchable.includes(term)))return false;
      const f=filterValue;if(f==='all')return true;
      if(view==='discover')return pluginCategory(e.market||{keywords:e.local?.keywords})===f;
      if(f==='available')return installed(e.local)&&!e.local?.builtin&&updateFor(e.name)?.status==='available';
      return f==='running'?!!e.local?.loaded:installed(e.local)&&!e.local?.enabled&&!e.local?.loaded&&!e.local?.tracked&&!e.local?.restart_required;
    }).sort((a,b)=>view==='discover'?0:originOrder(a)-originOrder(b)||Number(updateFor(b.name)?.status==='available')-Number(updateFor(a.name)?.status==='available')||a.name.localeCompare(b.name));
  }
  search.oninput=()=>{page=1;lastList='';render();};
  includeLocal.onchange=()=>{page=1;lastList=lastDetail='';render();};
  function render(){
    if(!alive)return;
    const op=pluginOperation();progress.hidden=!op;progress.replaceChildren();
    if(op){progress.dataset.phase=op.phase;progress.setAttribute('aria-busy',String(op.busy));progress.append(node('strong','',({enable:'启用插件',disable:'停用插件',remove:'卸载插件',apply:'应用更改'}[op.action])+' · '+(locals.find(p=>p.name===op.name)?.displayName||op.name)),node('p','',op.text));if(op.phase==='uncertain')progress.append(button('继续检查',()=>{void continuePluginOperation();}));}
    const pending=locals.filter(p=>installed(p)&&p.restart_required);
    restartSummary.hidden=view!=='local'||!pending.length||!!op?.busy;restartSummary.replaceChildren();
    if(pending.length){restartSummary.append(node('span','',`${pending.length} 个插件有待生效的更改。可在详情中重启并应用，当前页面会保留。`),button('查看待生效',()=>{filterValue='all';search.value='';page=1;requestedName=pending[0].name;lastList=lastDetail='';render();}));}
    const counts={local:locals.filter(installed).length,discover:discoverable().length,updates:locals.filter(p=>installed(p)&&!p.builtin&&updateFor(p.name)?.status==='available').length};
    search.disabled=busy||!!pluginOperation()?.busy;includeLocal.disabled=search.disabled;
    scopeCount.textContent='';
    refresh.textContent=busy?'处理中…':'刷新';refresh.disabled=busy||!!pluginOperation()?.busy;
    for(const [value,chip] of filterButtons){chip.disabled=busy||!!pluginOperation()?.busy;chip.setAttribute('aria-pressed',String(value===filterValue));if(value==='available')chip.textContent=`可更新 ${counts.updates}`;}
    const status=pluginUpdateSnapshot();
    feedback.textContent=view==='discover'?[localError,marketError].filter(Boolean).join(' '):[localError,status.error,status.checking?'正在后台检查更新…':status.message].filter(Boolean).join(' ');
    const rows=entries();const pages=Math.max(1,Math.ceil(rows.length/30));

    if(requestedName){const index=rows.findIndex(e=>e.name===requestedName);if(index>=0){selected=requestedName;page=Math.floor(index/30)+1;requestedName=undefined;requestAnimationFrame(()=>{if(alive)detail.querySelector('.pw-update-note')?.scrollIntoView({block:'nearest'});});}else if(localLoaded)requestedName=undefined;}
    page=Math.min(page,pages);const shown=rows.slice((page-1)*30,page*30);
    if(!shown.some(e=>e.name===selected)){selected=shown[0]?.name||'';lastDetail='';}
    const listKey=JSON.stringify([view,shown,selected,busy,pluginOperation(),localLoaded,marketLoaded,localError,marketError,search.value,filterValue,shown.map(e=>updateFor(e.name))]);
    if(listKey!==lastList){lastList=listKey;list.replaceChildren();let group='';
      if(!shown.length){
        const waiting=!localLoaded||view==='discover'&&!marketLoaded;
        const failed=!localLoaded&&localError||view==='discover'&&!marketLoaded&&marketError;
        list.append(node('p','pw-empty',waiting?(failed?'暂时无法列出插件，请刷新重试。':'正在读取插件…'):!search.value&&filterValue==='available'?'暂无可更新的插件':search.value||filterValue!=='all'?'没有匹配的插件':view==='discover'?'没有匹配的插件':'暂无插件'));
      }
      for(const e of shown){
        if(view==='local'&&originGroup(e)!==group){group=originGroup(e);const label=node('h3','pw-group',group);label.append(node('span','',String(rows.filter(row=>originGroup(row)===group).length)));list.append(label);}
        const item=button('',()=>{operationError='';selected=e.name;lastDetail='';render();},'pw-entry');item.disabled=busy||!!pluginOperation()?.busy;item.dataset.name=e.name;item.setAttribute('aria-pressed',String(e.name===selected));
        const mark=node('span','pw-mark');mark.append(icon(e.local?.builtin?'grid':'puzzle'));
        const copy=node('span','pw-entry-copy');copy.append(node('strong','',e.local?.displayName||e.name));
        const text=filterValue==='available'&&updateFor(e.name)?updateText(updateFor(e.name)!):e.local?.description||e.market?.description||e.name;const summary=node('span','pw-entry-summary',text);summary.title=text;copy.append(summary);
        const state=node('span','pw-entry-state');const update=updateFor(e.name);
        if(update?.status==='available'&&installed(e.local)&&!e.local?.builtin){state.textContent=`${runtime(e.local!)} · 可更新`;state.dataset.tone='update';}
        else if(e.local){state.textContent=(e.local.builtin?'内置 · ':'')+runtime(e.local);state.dataset.tone=e.local.loaded?'on':'off';}
        else state.textContent=e.market?pluginCategoryLabel(e.market):'';
        const meta=node('span','pw-entry-meta');meta.append(state);if(view==='discover')meta.append(node('span','pw-entry-platform',platformSummary(platforms(e))));copy.append(meta);item.append(mark,copy);list.append(item);
      }
    }
    footer.replaceChildren(node('span','page-footer-label',view==='local'?'内置与已安装':'社区插件'));const footerInfo=node('div','page-footer-summary');footerInfo.append(node('span','',!localLoaded||view==='discover'&&!marketLoaded?'正在读取…':view==='local'?`共 ${counts.local} 个 · 运行中 ${locals.filter(p=>installed(p)&&p.loaded).length} 个 · 可更新 ${counts.updates} 个${rows.length!==counts.local?' · 筛选结果 '+rows.length+' 个':''}`:`搜索到 ${rows.length} 个插件`));footer.append(footerInfo);if(pages>1){const back=button('上一页',()=>{page--;lastList='';render();});back.disabled=page===1;const next=button('下一页',()=>{page++;lastList='';render();});next.disabled=page===pages;footerInfo.append(back,node('span','',`${page}/${pages}`),next);}
    const entry=shown.find(e=>e.name===selected);const detailKey=JSON.stringify([entry,view,entry&&updateFor(entry.name),operationError,busy,pluginOperation(),checkingUpdates,status.checking]);
    if(detailKey!==lastDetail){lastDetail=detailKey;drawDetail(entry);}
  }
  function drawDetail(e?:Entry){
    detail.replaceChildren();if(!e){detail.append(node('div','pw-detail-empty','选择一个插件，查看详情与管理选项'));return;}
    const p=e.local,m=e.market,exists=installed(p),u=updateFor(e.name);
    detail.classList.toggle('pw-builtin-qq', e.name === 'core-infinity-qq' && !!p?.builtin);
    const hero=node('header','pw-hero');const mark=node('div','pw-mark pw-mark-large');mark.append(icon(p?.builtin?'grid':'puzzle'));
    const title=node('div','pw-identity');title.append(node('div','pw-eyebrow',p?.builtin?'CORE 内置':m?pluginCategoryLabel(m):'本地插件'),node('h2','',p?.displayName||e.name),node('p','pw-package',e.name));hero.append(mark,title);detail.append(hero);
    const metadata=node('div','pw-metadata');metadata.append(node('span','',`版本 ${p?.version||m?.version||'未声明'}`),node('span','',p?.author||m?.author||'未声明作者'));if(p?.license)metadata.append(node('span','',p.license));detail.append(metadata);
    let compatibilityBox:HTMLElement|undefined;
    if(!p?.builtin){
      const compatibility=platforms(e);const box=node('section','pw-compatibility');box.append(node('h3','','运行平台'));const badges=node('div','pw-platforms');
      for(const [id,label] of Object.entries(platformNames)){const support=compatibility.supported.some(value=>value===id)?'supported':compatibility.conditional.some(value=>value===id)?'conditional':'unknown';const badge=node('div','pw-platform');badge.dataset.support=support;badge.append(node('strong','',label),node('span','',support==='supported'?'已声明支持':support==='conditional'?'有条件支持':'未声明'));badges.append(badge);}
      box.append(badges,node('p','',compatibility.note));if(compatibility.source){const link=node('a','pw-source','平台说明 ↗');link.href=compatibility.source;link.target='_blank';link.rel='noopener noreferrer';box.append(link);}compatibilityBox=box;
    }
    const actions=node('div','pw-actions');actions.setAttribute('role','group');actions.setAttribute('aria-label','插件操作');
    if(exists){
      const configure=node('a','pw-button pw-primary','打开配置');configure.href=`#/infinity/${encodeURIComponent(e.name)}`;if(busy){configure.removeAttribute('href');configure.setAttribute('aria-disabled','true');}actions.append(configure);
      const toggle=button(p!.restart_required&&p!.restart_action!=='config'?(p!.enabled?'撤销启用':'撤销停用'):p!.enabled?'停用插件':'启用插件',()=>{void mutate(e,'switch');});toggle.disabled=busy||!!pluginOperation()?.busy;actions.append(toggle);
      const status=node('span','pw-runtime',runtime(p!));status.dataset.tone=p!.loaded?'on':'off';metadata.append(status);
    }else{const install=button(p?.builtin?'恢复安装':!m?.url?'暂无正式版':'安装插件',()=>{void mutate(e,'install');},'pw-primary');install.disabled=busy||!!pluginOperation()?.busy||!p?.builtin&&!m?.url;actions.append(install);}
    if(exists&&p?.builtin){const check=button('检查 Core 更新',()=>openCoreUpdateDialog(check));actions.append(check);}
    else if(exists){const check=button(checkingUpdates||pluginUpdateSnapshot().checking?'检查中…':'检查更新',()=>{void checkUpdates();});check.disabled=busy||!!pluginOperation()?.busy||checkingUpdates||pluginUpdateSnapshot().checking;actions.append(check);}
    const source=repo(e);
    if(source){const link=node('a','pw-button','GitHub 源代码 ↗');link.href=source;link.target='_blank';link.rel='noopener noreferrer';actions.append(link);}
    if(exists){const remove=button('卸载插件',()=>{void mutate(e,'remove');},'pw-remove');remove.disabled=busy||!!pluginOperation()?.busy||p?.can_remove===false;actions.append(remove);}
    if(exists&&p?.restart_required){const apply=button('重启并应用',()=>{void mutate(e,'apply');});apply.disabled=busy||!!pluginOperation()?.busy;actions.append(apply);}
    const versionRepo=!p?.builtin&&source.startsWith('https://github.com/')?source.slice('https://github.com/'.length):'';
    const versions=versionRepo?pluginVersions(versionRepo,e.name,exists?(p?.version||''):'',busy||!!pluginOperation()?.busy,row=>{void changeVersion(e,versionRepo,row);}):undefined;
    if(versions){const choose=button(u?.status==='available'?'选择更新版本':'选择版本',()=>{void versions.loadVersions();versions.scrollIntoView({block:'nearest'});});choose.disabled=busy||!!pluginOperation()?.busy;actions.insertBefore(choose,actions.querySelector('.pw-remove'));}
    detail.append(actions);
    if(versions)detail.append(versions);
    const result=node('p','pw-operation',operationError);result.setAttribute('role','status');detail.append(result);
    if(p?.restart_required){const note=node('section','pw-restart-note');note.append(node('h3','','重启后生效'),node('p','',p.restart_reason||'设置已保存，重启当前 Bot 的 Core 服务后生效。'),node('p','',p.loaded?'插件当前仍在运行，任务结束后再重启服务。刷新网页不会使此更改生效。':'点击“重启并应用”后，页面将显示重启进度并核对生效结果。'));detail.append(note);}
    if(p?.lifecycle_error)detail.append(node('p','pw-operation',p.lifecycle_error));
    if(compatibilityBox)detail.append(compatibilityBox);
    if(u&&exists&&!p?.builtin){
      const update=node('section','pw-update-note');update.append(node('h3','',u.status==='available'?'有新版本可用':'版本检查'),node('p','',updateText(u)));
      if(u.checkedAt)update.append(node('small','',`上次成功检查 ${new Date(u.checkedAt*1000).toLocaleString()}`));
      if(u.error)update.append(node('p','',u.error));
      if(u.status==='available'){const instructions=node('section');instructions.append(node('h3','','查看升级步骤'),node('p','','停用插件后，点击“选择更新版本”，核对发布说明再安装。旧包会备份，配置和缓存保留，安装后按需启用。多个 Bot 共用安装包时，需要各 Bot 均已停用该插件。'));update.append(instructions);}
      detail.append(update);
    }
    const descriptions=node('div','pw-detail-sections');
    const about=node('section','pw-about');about.append(node('h3','','关于插件'),node('p','',p?.description||m?.description||'作者尚未提供简介。'));const requirement=m&&pluginRequirement(m);if(requirement)about.append(node('p','pw-requirement',requirement));if(p?.contract)about.append(node('p','pw-operation',p.contract));descriptions.append(about);
    if (p?.builtin && e.name === 'core-infinity-qq') {
      const features=node('div','pw-feature-list');
      for (const [title,text] of [
        ['消息与会话','收发私聊与群聊消息，配置允许接入的账号和群。'],
        ['图片与表情','查看图片、收藏表情，并发送已保存的图片。'],
        ['语音交流','配置语音识别和合成模型，进行本机自检。'],
        ['工具与扩展','查看已注册工具，为 ComfyUI 等 QQ 扩展提供消息通道。'],
      ]) {const row=node('div','pw-feature');row.append(node('h4','',title),node('p','',text));features.append(row);}
      about.append(features,node('p','pw-feature-note','首次使用请打开配置，连接 SnowLuma 并设置会话路由。具体可用功能取决于当前配置与权限。'));
    }
    const info=node('section','pw-info');info.append(node('h3','','安装信息'));const fields=node('dl');
    const field=(label:string,value:string)=>{fields.append(node('dt','',label),node('dd','',value||'未声明'));};
    field('来源',p?.builtin?'Core 内置':p?.origin?.source==='github'?'GitHub':p?'本地导入':m?.source||'GitHub');
    field('安装状态',exists?'已安装':p?.builtin?'可从 Core 恢复':'尚未安装');
    if(p?.where)field('安装位置',p.where);
    if(p?.api!==undefined)field('兼容接口',`Infinity API ${p.api??'未声明'}`);
    if(m?.date)field('仓库更新',m.date);
    if(p?.builtin)field('更新方式','随 Core 更新');
    if(p?.reload_mode)field('生效方式',p.reload_mode==='restart'?'启用和停用需重启 Core 服务':p.reload_mode==='hot'?'支持热加载':'声明无效');
    info.append(fields);descriptions.append(info);detail.append(descriptions);
    const scroll=node('div','pw-detail-scroll');
    const children=[...detail.children];
    scroll.append(...children.slice(children.indexOf(actions)+1));
    detail.append(scroll);
  }
  async function changeVersion(e:Entry,repository:string,row:VersionChoice){
    if(busy||pluginOperation()?.busy||!alive)return;
    if(e.local&&(e.local.enabled||e.local.loaded||e.local.restart_required)){operationError='请先停用插件；如提示需要重启，请完成重启后再切换版本。';render();return;}
    const accepted=await confirmAction('安装所选版本',`将安装 ${row.tag}${row.prerelease?'（预发布）':''}。原安装包会备份，配置和缓存保留，安装后保持停用。旧版可能不兼容新版配置，请先阅读发布说明。`,'安装',false,{className:'pw-confirm',detail:e.local?.displayName||e.name});
    if(!accepted||!alive||busy)return;
    busy=true;operationError='正在下载、核对并安装所选版本…';render();
    try{
      const r=await fetch('/market/install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:{repository,tag:row.tag,name:e.name,current:installed(e.local)?e.local?.version||'':''}})});
      if(!r.ok)throw Error((await r.text()).trim()||'版本安装失败');const result=await r.json();if(!result.ok)throw Error(result.said||'版本安装失败');operationError=result.said;
      const token=++epoch;await readLocal(token);if(view==='discover')await readMarket(token);await checkPluginUpdates();window.dispatchEvent(new Event('plugins-changed'));
    }catch(error){operationError=error instanceof Error?error.message:'版本安装失败';}
    finally{busy=false;lastList=lastDetail='';render();}
  }
  async function checkUpdates(){if(checkingUpdates||busy)return;checkingUpdates=true;render();try{await checkPluginUpdates(true);}finally{checkingUpdates=false;render();}}
  async function mutate(e:Entry,action:'switch'|'install'|'remove'|'apply'){
    if(busy||pluginOperation()?.busy||!alive)return;busy=true;operationError='';
    const startedEpoch=++epoch;render();
    try{
      const label=action==='apply'?'重启并应用':action==='remove'?'卸载插件':action==='install'?(e.local?.builtin?'恢复安装':'安装插件'):e.local?.enabled?'停用插件':'启用插件';
      let message=action==='apply'?'重启当前 Bot 服务并应用更改。页面会保留并显示进度。':action==='remove'?'该安装包由所有 Bot 共用。卸载将先在各 Bot 停用插件，保留配置和缓存。':action==='install'?'安装插件程序，完成后再配置并启用。'+(e.local?.builtin?'':` 运行平台：${platformSummary(platforms(e))}。${platforms(e).note}`):`确认${e.local?.enabled?'停用':'启用'}当前 Bot 的这个插件？`;
      if(action==='switch'&&e.local?.reload_mode==='restart')message=e.local.restart_required&&e.local.restart_action!=='config'?'撤销尚未生效的切换，恢复之前的启用设置。':'如果插件需要重启，将在保存设置后自动重启当前 Bot 服务。页面会保留并显示进度。';
      if(action==='remove'&&e.local?.reload_mode==='restart')message+=' 如当前 Bot 需要重启，页面会等待重连后继续完成卸载。其他 Bot 仍占用时会保留安装包并说明原因。';
      const accepted=await confirmAction(label,message,label,false,{className:'pw-confirm',detail:e.local?.displayName||e.name});
      if(!accepted||!alive||epoch!==startedEpoch)return;
      if(action!=='install'){
        await runPluginOperation(e.name,action==='switch'?(e.local?.enabled?'disable':'enable'):action);
        epoch++;await readLocal(epoch);if(view==='discover')await readMarket(epoch);
        return;
      }
      const path='/market/install';
      const payload=e.local?.builtin?{from:e.local.where}:{url:e.market?.url};
      try{const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!response.ok)throw new Error((await response.text()).trim()||`${label}失败`);const answer=await response.json();if(answer.ok===false)throw new Error(answer.said||`${label}失败`);}
      catch(error){operationError=error instanceof Error?error.message:`${label}失败`;}
      epoch++;await readLocal(epoch);if(view==='discover')await readMarket(epoch);window.dispatchEvent(new Event('plugins-changed'));
      if(localError)operationError=[operationError,'操作后未能刷新安装状态，请重新刷新。'].filter(Boolean).join(' ');
    }finally{busy=false;lastList=lastDetail='';render();requested();}
  }
  async function readLocal(token:number){try{const response=await fetch('/plugins',{cache:'no-store'});if(!response.ok)throw Error();const data=await response.json() as PluginsAnswer;if(!data.wired||!Array.isArray(data.plugins))throw Error();if(alive&&epoch===token){locals=data.plugins;acceptPluginRuntime(locals);localLoaded=true;localError=data.broken?.length?'部分插件清单读取失败：'+data.broken.join('、'):'';}}catch{if(alive&&epoch===token)localError=localLoaded?'无法刷新，当前显示上次读取的本机插件。':'无法读取本机插件，请重试。';}render();}
  let marketRequest=0;
  async function readMarket(token:number){const request=++marketRequest;try{const response=await fetch('/market',{cache:'no-store'});if(!response.ok)throw Error();const data=await response.json() as MarketAnswer;if(!data.wired||!Array.isArray(data.found))throw Error(data.said);if(alive&&epoch===token&&request===marketRequest){market=data.found;marketLoaded=true;marketError=data.stale?(data.error||'显示上次缓存，市场正在后台刷新。'):'';}}catch{if(alive&&epoch===token)marketError=marketLoaded?'市场暂时无法刷新，保留上次结果。':'无法读取插件市场，请重试。';}finally{render();}}
  async function read(){if(busy||!alive)return;const token=++epoch;await Promise.allSettled([readLocal(token),view==='discover'?readMarket(token):Promise.resolve()]);}
  function requested(){if(busy||!alive||view!=='local')return;const request=takePluginUpdateRequest();if(request){search.value='';page=1;selected='';operationError='';filterValue=request.name&&updateFor(request.name)?.status!=='available'?'all':'available';requestedName=request.name||pluginUpdateSnapshot().rows.find(row=>row.status==='available')?.name;lastList=lastDetail='';render();}}
  function restartRequested(){if(busy||!alive||view!=='local')return;const name=takePluginRestartRequest();if(name){filterValue='all';search.value='';page=1;requestedName=name;lastList=lastDetail='';render();}}
  const updateChanged=()=>render();const pluginsChanged=()=>{if(!busy)void read();};let timer:number|undefined;
  setFilters();
  return {root,show(){alive=true;root.hidden=false;window.addEventListener('plugin-updates-changed',updateChanged);window.addEventListener('plugin-operation-changed',updateChanged);window.addEventListener('plugin-updates-open',requested);window.addEventListener('plugin-restart-open',restartRequested);window.addEventListener('plugins-changed',pluginsChanged);requested();restartRequested();render();void read();timer=window.setInterval(()=>{if(!document.hidden&&view==='local'&&!busy)void read();},30000);},stop(){alive=false;epoch++;root.hidden=true;clearInterval(timer);window.removeEventListener('plugin-updates-changed',updateChanged);window.removeEventListener('plugin-operation-changed',updateChanged);window.removeEventListener('plugin-updates-open',requested);window.removeEventListener('plugin-restart-open',restartRequested);window.removeEventListener('plugins-changed',pluginsChanged);}};
}
