import {pageFetch as fetch} from './status-json';
export interface VersionChoice {tag:string;title:string;date?:string;prerelease:boolean;notes:string;source:string;download:string;}
interface History {rows:VersionChoice[];page:number;more:boolean;kind:string;}
interface Saved {kind:string;page:number;selected:string;data?:History;error:string;loading:boolean;}
const states=new Map<string,Saved>();
const painters=new Map<string,()=>void>();
const el=<K extends keyof HTMLElementTagNameMap>(tag:K,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;n.className=cls;return n;};
async function releasePage(repo:string, page:number):Promise<History> {
  const params = new URLSearchParams({repository:repo, page:String(page), kind:'releases'});
  const response = await fetch('/plugin-versions?' + params, {cache:'no-store'}, 20000);
  if (!response.ok) throw Error('版本读取失败，请检查网络后重试。');
  const result = await response.json();
  if (!Array.isArray(result.rows)) throw Error('版本列表格式异常');
  return result;
}
// Older checkers report the version without the author's exact release tag.
export async function tagForVersion(repo:string, version:string) {
  for (let page=1; page<=5; page++) {
    const result = await releasePage(repo,page);
    const match = result.rows.find(row => row.tag.replace(/^v/, '') === version && !row.prerelease);
    if (match) return match.tag;
    if (!result.more) break;
  }
  throw Error('发布记录中未找到对应版本，请重新检查更新。');
}
export function pluginVersions(repo:string,name:string,current:string,blocked:boolean,install:(row:VersionChoice)=>void){
  const key=repo+'/'+name;
  let state=states.get(key);
  if(!state){state={kind:'releases',page:1,selected:'',error:'',loading:false};states.set(key,state);}
  const s=state;
  const box=el('section','','pw-versions');
  const body=el('div','','pw-version-body');
  if (!current) box.append(el('h3','选择安装版本'));
  box.append(body);
  const link=(label:string,url:string)=>{const a=el('a',label,'pw-button');a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;};
  const button=(label:string,click:()=>void)=>{const b=el('button',label,'pw-button');b.type='button';b.onclick=click;return b;};
  function draw(){
    body.replaceChildren();
    const controls=el('div','','pw-version-controls');
    const refresh=button(s.loading?'正在读取…':s.data?'刷新版本':'读取版本',()=>{void read();});refresh.disabled=s.loading||blocked;controls.append(el('span','正式发布版本'),refresh);body.append(controls);
    if(s.error){const error=el('p',s.error,'pw-operation');error.setAttribute('role','status');body.append(error);}
    if(!s.data){body.append(el('p',s.loading?'正在读取版本…':'点击“读取版本”查看正式版和历史正式版。'));return;}
    if(!s.data.rows.length){body.append(el('p','作者尚未发布正式 Release，暂不提供下载。'));}
    else{
      if(current&&s.page===1&&!s.data.more&&s.data.rows.length===1)body.append(el('p','该仓库目前仅有一个正式发布版本。'));
      const select=el('select');select.setAttribute('aria-label','选择插件版本');select.disabled=blocked||s.loading;
      for(const row of s.data.rows)select.add(new Option(row.tag+(row.prerelease?' · 预发布':'')+(row.date?' · '+row.date.slice(0,10):'')+(row.tag.replace(/^v/,'')===current?' · 当前版本':''),row.tag));
      if(!s.data.rows.some(row=>row.tag===s.selected))s.selected=s.data.rows[0].tag;select.value=s.selected;
      select.onchange=()=>{s.selected=select.value;draw();};body.append(select);
      const row=s.data.rows.find(row=>row.tag===s.selected)!;
      const actions=el('div','','pw-version-controls');const apply=button(current?'替换安装':'安装此版本',()=>install(row));apply.disabled=blocked||s.loading||row.tag.replace(/^v/,'')===current;actions.append(apply,link('下载源码包 ↗',row.download),link('发布说明 ↗',row.source));body.append(actions);
      if(row.notes){const notes=el('section','','pw-version-notes');notes.append(el('h3','版本说明'),el('p',row.notes));body.append(notes);}
      body.append(el('p',current?'替换安装会备份原安装包并保留配置和缓存。旧版本的配置兼容性请查看发布说明。':'按所选正式版本安装并核对插件清单，安装后默认停用。','pw-version-hint'));
    }
    if(s.page>1||s.data.more){const pages=el('div','','pw-version-controls');const previous=button('上一页',()=>{s.page--;s.selected='';void read();});previous.disabled=blocked||s.loading||s.page===1;const next=button('下一页',()=>{s.page++;s.selected='';void read();});next.disabled=blocked||s.loading||!s.data.more;pages.append(previous,el('span','第 '+s.page+' 页'),next);body.append(pages);}
  }
  async function read(){if(s.loading)return;s.loading=true;s.error='';draw();try{
    s.data=await releasePage(repo,s.page);
  }catch(error){s.error=error instanceof Error?error.message:'版本读取失败';}finally{s.loading=false;painters.get(key)?.();}}
  painters.set(key,draw);draw();return Object.assign(box,{loadVersions:()=>s.data?Promise.resolve():read()});
}
