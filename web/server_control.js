/** Native LiteGraph controls. Only the detachable monitor uses HTML. */
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
const COLORS={CHECKING:'#64748b',STARTING:'#86dfa0',RUNNING:'#22c55e',STOPPING:'#facc15',STOPPED:'#ef4444',ERROR:'#f97316'};
app.registerExtension({name:'FishS2.NativeServerControl',async beforeRegisterNodeDef(type,data){
 if(data.name!=='S2ServerControl')return;
 const original=type.prototype.onNodeCreated;
 type.prototype.onNodeCreated=function(...args){
  const result=original?.apply(this,args),node=this;
  const find=n=>node.widgets?.find(w=>w.name===n),read=n=>find(n)?.value;
  const set=(n,v)=>{const w=find(n);if(w)w.value=v;};
  const request=async(method,url,body)=>{const r=await api.fetchApi(url,{method,...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||`HTTP ${r.status}`);return d;};
  let state='CHECKING',busy=false,monitor=null,timer=null,lastRoot='',lifecycleTimer=null,scanSequence=0,restoring=false;
  const repaint=(s)=>{state=String(s||'ERROR').toUpperCase();if(state==='EXITED')state='STOPPED';if(!COLORS[state])state='ERROR';node.color=COLORS[state];node.setDirtyCanvas(true,true);action.name=busy?'Working…':state==='RUNNING'?'Stop Server':state==='STARTING'?'Starting…':state==='STOPPING'?'Stopping…':state==='ERROR'?'Retry Start':'Start Server';};
  const ensureLifecycle=()=>{if(lifecycleTimer)return;lifecycleTimer=setInterval(async()=>{if(!busy && (state==='RUNNING'||state==='STARTING'||state==='STOPPING'))await refresh();else if(state==='STOPPED'||state==='ERROR'){clearInterval(lifecycleTimer);lifecycleTimer=null;}},2200);};
  const report=e=>{console.error('[FishS2]',e);repaint('ERROR');status.value=`ERROR: ${e.message||e}`;};
  const status=node.addWidget('text','Server Status','STOPPED',()=>{},{serialize:false});status.options={...(status.options||{}),readonly:true};
  const action=node.addWidget('button','Start Server',null,async()=>{
   if(busy||state==='CHECKING')return;busy=true;const stop=state==='RUNNING';repaint(stop?'STOPPING':'STARTING');status.value=state;
   try{if(stop)await request('POST','/fishs2/server/stop');else await request('POST','/fishs2/server/start',Object.fromEntries(['executable','model','tokenizer','voice_dir','dll_dir','host','port','cuda_device','gpu_layers','show_console','codec_backend'].map(k=>[k,read(k)])));await refresh();if(!stop)ensureLifecycle();}catch(e){report(e);}finally{busy=false;repaint(state);}
  });
  const model=node.addWidget('combo','Model','Scan components',v=>{if(v==='Custom path…'){const p=prompt('Absolute GGUF path',String(read('model')||''));if(p)set('model',p);}else if(v)set('model',v);},{values:['Scan components','Custom path…'],serialize:false});
  const voices=node.addWidget('combo','Voice Library','Scan components',v=>{if(v==='Custom path…'){const p=prompt('Absolute voice directory',String(read('voice_dir')||''));if(p)set('voice_dir',p);}else if(v)set('voice_dir',v);app.graph?._nodes?.forEach(n=>n._fishS2RefreshVoices?.());},{values:['Scan components','Custom path…'],serialize:false});
  const refresh=async()=>{try{const d=await request('GET',`/fishs2/server/status?host=${encodeURIComponent(read('host')||'127.0.0.1')}&port=${encodeURIComponent(read('port')||3030)}`);const raw=String(d.state||'stopped').toUpperCase();repaint(raw);if(state==='STARTING'||state==='RUNNING'||state==='STOPPING')ensureLifecycle();status.value=`${state} · owned=${!!d.owned} · listening=${!!d.port_listening}`;if(monitor){monitor.label.textContent=state;const log=(d.logs||[]).join('\n');if(monitor.log.textContent!==log){const stick=monitor.log.scrollTop+monitor.log.clientHeight>=monitor.log.scrollHeight-30;monitor.log.textContent=log;if(stick)monitor.log.scrollTop=monitor.log.scrollHeight;}}}catch(e){report(e);}};
  const syncChoices=()=>{for(const [key,w] of [['model',model],['voice_dir',voices]]){const saved=String(read(key)||'').trim();const current=Array.isArray(w.options?.values)?w.options.values:[];w.options.values=[...new Set([...(saved?[saved]:[]),...current.filter(x=>x!=='Scan components'&&x!=='Custom path…'),'Custom path…'])];w.value=saved||'Custom path…';}node.setDirtyCanvas(true,true);};
  const scan=async({rootChanged=false}={})=>{const root=String(read('root_dir')||'').trim();if(!root)return;const seq=++scanSequence;try{const d=await request('POST','/fishs2/server/scan',{root_dir:root});if(seq!==scanSequence||String(read('root_dir')||'').trim()!==root)return;for(const k of ['executable','tokenizer','dll_dir'])if(rootChanged||!read(k))set(k,d[k]||'');for(const [key,w,values,preferred] of [['model',model,d.model_candidates,d.model],['voice_dir',voices,d.voice_candidates,d.voice_dir]]){const items=values||[],old=String(read(key)||'').trim();const selected=rootChanged?(items.includes(preferred)?preferred:items[0]||old):old||(items.includes(preferred)?preferred:items[0]||'');if(selected&&selected!==old)set(key,selected);w.options.values=[...new Set([...(selected?[selected]:[]),...items,'Custom path…'])];w.value=selected||'Custom path…';}lastRoot=root;app.graph?._nodes?.forEach(n=>n._fishS2RefreshVoices?.());node.setDirtyCanvas(true,true);}catch(e){report(e);}};
  const closeMonitor=()=>{if(!monitor)return;monitor.root.remove();monitor=null;if(timer){clearInterval(timer);timer=null;}monitorBtn.name='Open Monitor';node.setDirtyCanvas(true,true);};
  const toggleMonitor=()=>{if(monitor){closeMonitor();return;}const root=document.createElement('section');root.style.cssText='position:fixed;right:24px;bottom:24px;width:min(780px,90vw);height:min(500px,75vh);z-index:99999;background:#171923;color:#eee;border:1px solid #6366f1;border-radius:10px;display:flex;flex-direction:column;font:13px system-ui;box-shadow:0 12px 50px #0009';const header=document.createElement('header');header.style.cssText='display:flex;gap:12px;align-items:center;padding:12px;border-bottom:1px solid #444';const title=document.createElement('strong');title.textContent='Fish S2 · Server Monitor';const label=document.createElement('span');label.textContent=state;const close=document.createElement('button');close.textContent='Close';close.style.cssText='margin-left:auto;padding:6px 15px;background:#4338ca;color:white;border:0;border-radius:6px;cursor:pointer';close.onclick=closeMonitor;header.append(title,label,close);const log=document.createElement('pre');log.style.cssText='flex:1;min-height:0;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;padding:12px;margin:0;font:12px/1.5 monospace';root.append(header,log);document.body.append(root);monitor={root,label,log};monitorBtn.name='Close Monitor';refresh();timer=setInterval(refresh,1200);};
  const monitorBtn=node.addWidget('button','Open Monitor',null,toggleMonitor);
  node.addWidget('button','Refresh Status',null,refresh);
  node.addWidget('button','Scan Components',null,scan);
  // Preserve native controls. Move advanced inputs below actions, without DOM overlays.
  const priority=['root_dir','Server Status','Start Server','Model','Voice Library','Open Monitor','Refresh Status','Scan Components'];
  const front=priority.map(k=>find(k)).filter(Boolean);node.widgets=[...front,...node.widgets.filter(w=>!front.includes(w))];
  const originalConnections=node.onConnectionsChange;node.onConnectionsChange=function(...a){const r=originalConnections?.apply(this,a);const ix=node.inputs?.findIndex(x=>x.name==='server_config')??-1;if(ix>0){const [port]=node.inputs.splice(ix,1);node.inputs.unshift(port);}return r;};
  // Hide duplicate native path fields; retain their serialized values for backend calls.
  for(const key of ['model','voice_dir']){const w=find(key);if(w){w.type='hidden';w.computeSize=()=>[0,0];}}
  const root=find('root_dir');if(root){const cb=root.callback;root.callback=function(...a){cb?.apply(this,a);if(restoring)return;scan({rootChanged:true});};}
  const originalConfigure=node.onConfigure;node.onConfigure=function(...a){restoring=true;try{return originalConfigure?.apply(this,a);}finally{restoring=false;syncChoices();repaint('CHECKING');status.value='CHECKING';setTimeout(()=>{refresh();scan();},350);}};
  repaint('CHECKING');status.value='CHECKING';syncChoices();setTimeout(()=>{refresh();if(read('root_dir'))scan();},650);
  const removed=node.onRemoved;node.onRemoved=function(...a){closeMonitor();if(lifecycleTimer)clearInterval(lifecycleTimer);return removed?.apply(this,a);};
  return result;
 };
}});
