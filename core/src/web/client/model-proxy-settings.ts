import { pageFetch } from './status-json';
import { configPresentation } from './config-presentation';
import { confirmAction } from './confirm-dialog';
interface ProxySettings {
  enabled:boolean; model_fallback?:boolean; proxy_url:string; providers:string[];
  downloads_enabled?:boolean; downloads_source?:string; downloads_fallback?:boolean;
  market_enabled?:boolean; market_source?:string; market_fallback?:boolean;
  revision:string; system_proxy?:string;
}
const PROVIDERS=[['openai','OpenAI / ChatGPT'],['google','Google Gemini'],['anthropic','Anthropic Claude'],['xai','xAI Grok']] as const;
const el=<K extends keyof HTMLElementTagNameMap>(tag:K,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
const modes:[string,string][]=[['direct','直接连接'],['fallback','代理优先，失败后直连'],['required','仅使用代理']];
const mode=(on?:boolean,fallback?:boolean)=>!on?'direct':fallback?'fallback':'required';
const select=(label:string,options:[string,string][])=>{const box=el('label','public-network-field',label),input=el('select');input.setAttribute('aria-label',label);for(const [value,text] of options)input.add(new Option(text,value));box.append(input);return {box,input};};

/** One save boundary, common defaults and explicit exceptions. */
export function modelProxySettings():HTMLElement {
  const root=el('section','settings-card model-proxy-settings');configPresentation(root,'loading');
  const header=el('div','model-proxy-header');header.append(el('h3','','网络与下载'));
  const note=el('p','set-note','下载与模型 API 分别选择连接方式，共用一个代理地址。');
  const addressLabel=el('label','model-proxy-address','代理地址');const addressRow=el('div','model-proxy-address-row');
  const address=el('input','engine-input engine-input-wide');address.type='url';address.autocomplete='off';address.spellcheck=false;address.placeholder='http://127.0.0.1:7893';address.setAttribute('aria-label','共用代理地址');
  const system=el('button','btn ghost','填入系统代理');system.type='button';addressRow.append(address,system);addressLabel.append(addressRow);
  const addressHint=el('p','set-note','支持 HTTP(S) 代理；修改后保存生效。');
  const policies=el('div','public-network-policies');
  const download=el('section','public-network-policy');download.append(el('h4','','下载与插件市场'));
  const source=select('来源顺序',[['mirror','镜像优先'],['official','官方优先']]);const connection=select('官方连接方式',modes);
  const downloadFlow=el('p','public-network-flow');download.append(source.box,connection.box,downloadFlow);
  const overrides=el('details','model-proxy-details');const overrideSummary=el('summary','','插件市场单独设置');
  const separateLabel=el('label','model-proxy-provider');const separate=el('input');separate.type='checkbox';separate.setAttribute('aria-label','插件市场使用独立设置');separateLabel.append(separate,'插件市场使用独立设置');
  const marketFields=el('div','model-network-fields');const marketSource=select('插件市场来源顺序',[['mirror','镜像优先'],['official','官方优先']]);const marketConnection=select('插件市场官方连接方式',modes);marketFields.append(marketSource.box,marketConnection.box);
  const marketFlow=el('p','public-network-flow');overrides.append(overrideSummary,separateLabel,marketFields,marketFlow);download.append(overrides);
  const downloadHint=el('p','public-network-caption','用于 Core 更新、插件搜索与安装、模型和运行环境下载。镜像始终直连；校验摘要只向官方核验。');download.append(downloadHint);
  const model=el('section','public-network-policy');model.append(el('h4','','模型 API'));
  const modelConnection=select('模型连接方式',modes);modelConnection.box.classList.add('model-network-wide');const modelFlow=el('p','public-network-flow');model.append(modelConnection.box,modelFlow);
  const providers=el('details','model-proxy-details');const providerSummary=el('summary');const targets=el('div','model-proxy-targets');const boxes=PROVIDERS.map(([id,title])=>{const label=el('label','model-proxy-provider'),input=el('input');input.type='checkbox';input.setAttribute('aria-label',title);label.append(input,title);targets.append(label);return {id,input};});
  providers.append(providerSummary,targets);model.append(providers,el('p','public-network-caption','仅连接尚未建立、模型请求尚未发送时允许直连回退；已发送请求和生成中断不重发。自定义 API 地址保持直连。'));
  policies.append(download,model);
  const actions=el('div','model-proxy-actions');const save=el('button','btn','保存'),reload=el('button','btn ghost','重新读取');save.type=reload.type='button';save.setAttribute('aria-label','保存网络设置');actions.append(save,reload);
  const status=el('p','model-proxy-status');status.setAttribute('role','status');root.append(header,note,addressLabel,addressHint,policies,actions,status);
  let revision:string|null=null,systemProxy='',busy=false,saving=false,baseline='',guarding=false;
  const fields=[address,source.input,connection.input,separate,marketSource.input,marketConnection.input,modelConnection.input,...boxes.map(b=>b.input)];
  const draft=()=>JSON.stringify(fields.map(f=>f instanceof HTMLInputElement&&f.type==='checkbox'?f.checked:f.value));
  const dirty=()=>!!baseline&&draft()!==baseline;
  const beforeUnload=(event:BeforeUnloadEvent)=>{if(saving||dirty()){event.preventDefault();event.returnValue='';}};
  const guard=()=>{const needed=saving||dirty();if(needed===guarding)return;guarding=needed;if(needed)window.addEventListener('beforeunload',beforeUnload);else window.removeEventListener('beforeunload',beforeUnload);};
  const path=(src:string,transport:string)=>{const official=transport==='direct'?'官方直连':transport==='fallback'?'官方代理 → 官方直连':'官方代理';return src==='mirror'?'镜像直连 → '+official:official+' → 镜像直连';};
  const paint=()=>{
    downloadFlow.textContent=path(source.input.value,connection.input.value);
    marketFields.hidden=marketFlow.hidden=!separate.checked;overrideSummary.textContent=separate.checked?'插件市场 · 使用独立设置':'插件市场 · 跟随下载设置';
    marketFlow.textContent=path(marketSource.input.value,marketConnection.input.value);
    modelFlow.textContent=modelConnection.input.value==='direct'?'直接连接官方模型接口':modelConnection.input.value==='fallback'?'官方代理 → 官方直连（仅发送前连接失败）':'仅通过代理连接；连接失败时报错';
    const chosen=boxes.filter(b=>b.input.checked);providerSummary.textContent=chosen.length===boxes.length?'适用范围 · 全部官方供应商':`适用范围 · 已选 ${chosen.length} 家官方供应商`;
    system.hidden=!systemProxy;
  };
  const lock=(value:boolean)=>{busy=value;for(const field of fields)field.disabled=value||revision===null;save.disabled=value||revision===null;reload.disabled=value;system.disabled=value||revision===null||!systemProxy;};
  for(const field of fields)for(const event of ['input','change'])field.addEventListener(event,()=>{paint();guard();});
  separate.addEventListener('change',()=>{if(separate.checked){marketSource.input.value=source.input.value;marketConnection.input.value=connection.input.value;}paint();guard();});
  const apply=(settings:ProxySettings)=>{
    revision=settings.revision;systemProxy=settings.system_proxy||'';address.value=settings.proxy_url;
    source.input.value=settings.downloads_source||'mirror';connection.input.value=mode(settings.downloads_enabled,settings.downloads_fallback);
    marketSource.input.value=settings.market_source||'mirror';marketConnection.input.value=mode(settings.market_enabled,settings.market_fallback);
    separate.checked=source.input.value!==marketSource.input.value||connection.input.value!==marketConnection.input.value;overrides.open=separate.checked;
    modelConnection.input.value=mode(settings.enabled,settings.model_fallback);for(const box of boxes)box.input.checked=settings.providers.includes(box.id);
    paint();baseline=draft();guard();configPresentation(root,'ready');
  };
  const read=async()=>{lock(true);status.textContent='正在读取网络设置…';try{const r=await pageFetch('/settings/model-proxy');const result=await r.json();if(!r.ok||!result.ok||typeof result.settings?.revision!=='string')throw Error('无法读取网络设置，未用默认值覆盖。');apply(result.settings);status.textContent='已读取当前设置';}catch(e){revision=null;configPresentation(root,'error');status.textContent=e instanceof Error?e.message:'读取失败，未覆盖。';}finally{lock(false);}};
  save.onclick=()=>{void(async()=>{
    if(busy||revision===null)return;
    const marketMode=separate.checked?marketConnection.input.value:connection.input.value;
    if([connection.input.value,marketMode,modelConnection.input.value].some(m=>m!=='direct')&&!address.value.trim()){status.textContent='使用代理前，请填写代理地址。';return;}
    if(modelConnection.input.value!=='direct'&&!boxes.some(b=>b.input.checked)){status.textContent='请选择至少一家适用的模型供应商。';return;}
    saving=true;guard();lock(true);status.textContent='正在保存…';
    try{const r=await pageFetch('/settings/model-proxy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision,proxy_url:address.value.trim(),enabled:modelConnection.input.value!=='direct',model_fallback:modelConnection.input.value==='fallback',providers:boxes.filter(b=>b.input.checked).map(b=>b.id),downloads_source:source.input.value,downloads_enabled:connection.input.value!=='direct',downloads_fallback:connection.input.value==='fallback',market_source:separate.checked?marketSource.input.value:source.input.value,market_enabled:marketMode!=='direct',market_fallback:marketMode==='fallback'})});const result=await r.json();if(!r.ok||!result.ok||!result.settings)throw Error(result.error||'保存未确认，请重新读取核对；未自动重试。');apply(result.settings);status.textContent='已保存，下次请求生效。';}catch(e){status.textContent=e instanceof Error?e.message:'保存未确认，请重新读取核对。';}finally{saving=false;guard();lock(false);}
  })();};
  reload.onclick=()=>{void(async()=>{if(busy)return;if(dirty()&&!await confirmAction('重新读取网络设置','尚未保存的修改将被替换。','重新读取'))return;void read();})();};
  system.onclick=()=>{if(busy||!systemProxy)return;address.value=systemProxy;status.textContent='已填入系统代理，尚未保存。';guard();};
  void read();return root;
}
