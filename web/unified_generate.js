/** Native LiteGraph widgets. Only the detachable task monitor uses HTML. */
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
app.registerExtension({name:'FishS2.NativeGenerate',async beforeRegisterNodeDef(type,data){
 if(data.name!=='FishS2Generate')return;
 const original=type.prototype.onNodeCreated;
 type.prototype.onNodeCreated=function(...args){
  const result=original?.apply(this,args),node=this,find=n=>node.widgets?.find(w=>w.name===n);
  const text=find('text'),count=find('speaker_count'),prefix=find('save_filename_prefix');
  // Runtime date placeholders are resolved in Python on each execution.
  if(prefix && (!prefix.value || prefix.value==='FishS2/FishS2'))prefix.value='FishS2/%date:yyyy-MM-dd%_%date:hh-mm-ss%';
  const status=node.addWidget('text','Generation Status','Ready',()=>{},{serialize:false});status.options={...(status.options||{}),readonly:true};
  const wav=node.addWidget('text','WAV Output Path','',()=>{},{serialize:false});wav.options={...(wav.options||{}),readonly:true};
  // Native widgets reserve their own canvas rows, avoiding DOM overlay/height bugs.
  // The actual media is played with the browser's Audio API, not an embedded DOM widget.
  let audioPreview=null;
  const preview=node.addWidget('button','▶ Preview WAV',null,()=>{
   if(!node.__s2URL)return;
   if(audioPreview && !audioPreview.paused){audioPreview.pause();audioPreview.currentTime=0;preview.name='▶ Preview WAV';node.setDirtyCanvas(true,true);return;}
   if(!audioPreview || audioPreview.src!==new URL(node.__s2URL,location.href).href){
    if(audioPreview){audioPreview.pause();audioPreview.src='';}
    audioPreview=new Audio(node.__s2URL);
    audioPreview.addEventListener('ended',()=>{preview.name='▶ Preview WAV';node.setDirtyCanvas(true,true);});
    audioPreview.addEventListener('error',()=>{preview.name='▶ Preview WAV';status.value='Preview failed';node.setDirtyCanvas(true,true);});
   }
   audioPreview.play().then(()=>{preview.name='■ Stop Preview';node.setDirtyCanvas(true,true);}).catch(e=>{console.warn('[FishS2] Preview failed',e);status.value='Preview failed';node.setDirtyCanvas(true,true);});
  });
  const play=node.addWidget('button','Download WAV',null,()=>{
   if(!node.__s2URL)return;
   const a=document.createElement('a');a.href=node.__s2URL;
   a.download=(node.__s2Path?.split(/[\\/]/).pop()||'FishS2.wav');
   document.body.appendChild(a);a.click();a.remove();
  });
  const folder=node.addWidget('button','Open Output Folder',null,async()=>{if(!node.__s2Path)return;try{const r=await api.fetchApi('/fishs2/output/open-folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:node.__s2Path})});const d=await r.json();if(!d.ok)throw Error(d.error);}catch(e){console.error('[FishS2]',e);}});
  const voices=Array.from({length:10},(_,i)=>find(`selected_voice_${i+1}`));
  // A STRING widget cannot reliably become a LiteGraph COMBO by changing .type.
  // Replace it with a genuine native combo while preserving its serialized name/value.
  voices.forEach((old,i)=>{if(!old)return;const previous=old.value||'';const index=node.widgets.indexOf(old);const combo=node.addWidget('combo',`selected_voice_${i+1}`,previous,()=>{}, {values:['']});combo.name=`selected_voice_${i+1}`;combo.value=previous;combo.serialize=true;node.widgets.splice(node.widgets.indexOf(combo),1);node.widgets.splice(index,1,combo);voices[i]=combo;});
  let popup=null,segments=[],taskLog='',phase='Ready',taskSubfolder='',completedAudio=null;
  const render=()=>{if(!popup)return;popup.state.textContent=phase;const completed=segments.filter(s=>s.done).length;
   popup.summary.textContent=segments.length?`${completed} / ${segments.length} observed segments`:'No segment events yet';
   popup.lines.replaceChildren();segments.forEach((s,i)=>{const el=document.createElement('div');el.style.cssText=`padding:4px 7px;border-left:4px solid ${s.done?'#22c55e':'#64748b'};background:${s.done?'#163b2a':'#202535'};margin:3px 0;white-space:pre-wrap;overflow-wrap:anywhere`;el.textContent=`${i+1}. ${s.speaker||''} ${s.done?'✓':'·'} ${s.text||s.line||''}`;popup.lines.append(el);});
   popup.log.textContent=taskLog||'No task log yet.';
  };
  const close=()=>{if(popup){popup.root.remove();popup=null;}logBtn.name='Open Task Monitor';node.setDirtyCanvas(true,true);};
  const toggle=()=>{if(popup){close();return;}const root=document.createElement('section');root.style.cssText='position:fixed;right:30px;bottom:24px;width:min(840px,92vw);height:min(680px,85vh);z-index:99999;display:flex;flex-direction:column;background:#171923;color:#e5e7eb;border:1px solid #6366f1;border-radius:10px;box-shadow:0 12px 50px #0009;font:13px system-ui';
   const header=document.createElement('div');header.style.cssText='display:flex;gap:12px;align-items:center;padding:12px;border-bottom:1px solid #41445b';const title=document.createElement('strong');title.textContent='Fish S2 · Task Monitor';const state=document.createElement('span');const exit=document.createElement('button');exit.textContent='Close';exit.style.cssText='margin-left:auto;padding:7px 14px;background:#4338ca;color:white;border:0;border-radius:5px;cursor:pointer';exit.onclick=close;header.append(title,state,exit);
   const upper=document.createElement('div');upper.style.cssText='height:44%;display:flex;flex-direction:column;min-height:0;padding:10px;border-bottom:1px solid #41445b';const summary=document.createElement('strong');const lines=document.createElement('div');lines.style.cssText='overflow:auto;flex:1;min-height:0;margin-top:8px;font:12px/1.4 monospace';upper.append(summary,lines);
   const lower=document.createElement('div');lower.style.cssText='display:flex;flex-direction:column;flex:1;min-height:0;padding:10px';const logTitle=document.createElement('strong');logTitle.textContent='Generation Log';const log=document.createElement('pre');log.style.cssText='flex:1;min-height:0;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 monospace';lower.append(logTitle,log);
   const footer=document.createElement('div');footer.style.cssText='display:flex;gap:8px;flex-wrap:wrap;padding:10px;border-top:1px solid #41445b';const button=(label,fn)=>{const b=document.createElement('button');b.textContent=label;b.style.cssText='background:#334155;color:white;border:0;border-radius:5px;padding:8px 14px;cursor:pointer';b.onclick=fn;footer.append(b);};button('Copy Log',()=>navigator.clipboard?.writeText(taskLog));button('Save Log',()=>{const url=URL.createObjectURL(new Blob([taskLog],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='FishS2-generation.log';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
   button('Preview Completed',()=>{if(!taskSubfolder)return;const url=api.apiURL(`/fishs2/task/completed.wav?task=${encodeURIComponent(taskSubfolder)}&t=${Date.now()}`);if(completedAudio){completedAudio.pause();completedAudio=null;return;}completedAudio=new Audio(url);completedAudio.onended=()=>{completedAudio=null;};completedAudio.play().catch(e=>{completedAudio=null;console.warn('[FishS2] Preview completed',e);});});
   button('Export Completed WAV',()=>{if(!taskSubfolder)return;const a=document.createElement('a');a.href=api.apiURL(`/fishs2/task/completed.wav?task=${encodeURIComponent(taskSubfolder)}&t=${Date.now()}`);a.download='FishS2-completed.wav';document.body.append(a);a.click();a.remove();});
   root.append(header,upper,lower,footer);document.body.append(root);popup={root,state,summary,lines,log};logBtn.name='Close Task Monitor';render();node.setDirtyCanvas(true,true);};
  const logBtn=node.addWidget('button','Open Task Monitor',null,toggle);
  // Native LiteGraph button: reliable on-demand rescan without custom HTML or menu hooks.
  const refreshVoicesBtn=node.addWidget('button','Refresh Voices',null,()=>load());
  const advancedOrder=['sentence_pause_ms','between_segments_ms','between_speakers_ms','per_sentence','let_server_segment_sentences','save_filename_prefix','temperature','top_p','top_k','max_new_tokens','timeout_seconds','max_segment_chars','retry_count','resume_completed_segments','normalize_loudness','target_lufs','peak_limiter','peak_ceiling_db','server_url'];
  const first=[text,status,wav,preview,play,folder,count,refreshVoicesBtn,...voices,logBtn];
  node.widgets=[...first,...advancedOrder.map(find).filter(Boolean),...node.widgets.filter(w=>!first.includes(w)&&!advancedOrder.includes(w.name))].filter(Boolean);
  // Keep the server connection first regardless of dynamic voice input changes.
  const orderInputs=()=>{const ix=node.inputs?.findIndex(x=>x.name==='server_config')??-1;if(ix>0){const [entry]=node.inputs.splice(ix,1);node.inputs.unshift(entry);}};
  // A connected voice_N input always overrides its cached-voice selector.
  // Present a read-only native text widget while overridden, restoring the
  // original native combo (and its selection) on disconnect.
  const syncOverrides=()=>{
   for(let i=1;i<=10;i++){
    const w=voices[i-1];if(!w)continue;
    const connected=!!node.inputs?.some(input=>input.name===`voice_${i}` && input.link!=null);
    if(connected && !w._s2overridden){
     w._s2overridden=true;
     w._s2cachedValue=w.value;
     w._s2cachedCallback=w.callback;
     w.type='text';w.value='External voice connected';
     w.options={...(w.options||{}),readonly:true};
     w.callback=()=>{};
    }else if(!connected && w._s2overridden){
     w._s2overridden=false;
     w.type='combo';w.value=w._s2cachedValue||'';
     w.callback=w._s2cachedCallback;
     w.options={...(w.options||{}),readonly:false};
    }
   }
  };
  const update=()=>{const n=Math.max(1,Math.min(10,Number(count?.value||1)));for(let i=1;i<=10;i++){const w=voices[i-1];if(!w)continue;if(i>n){if(!w._s2hidden){w._s2hidden=true;w._s2type=w.type;w._s2size=w.computeSize;w.type='hidden';w.computeSize=()=>[0,0];}}else if(w._s2hidden){w.type=w._s2type;w.computeSize=w._s2size;w._s2hidden=false;}}
   for(let i=10;i>n;i--){const ix=node.inputs?.findIndex(x=>x.name===`voice_${i}`)??-1;if(ix>=0&&node.inputs[ix].link==null)node.removeInput(ix);}
   for(let i=1;i<=n;i++)if(!node.inputs?.some(x=>x.name===`voice_${i}`))node.addInput(`voice_${i}`,'S2VOICE_CONFIG');orderInputs();syncOverrides();node.setSize([Math.max(node.size[0],470),node.computeSize()[1]]);node.setDirtyCanvas(true,true);};
  if(count){const cb=count.callback;count.callback=function(...a){cb?.apply(this,a);update();};}
  const directory=()=>{const link=node.inputs?.find(x=>x.name==='server_config')?.link;if(link==null)return '';const edge=app.graph?.links?.[link];const parent=app.graph?.getNodeById(edge?.origin_id);return String(parent?.widgets?.find(w=>w.name==='voice_dir')?.value||'');};
  let voiceScanVersion=0;
  const load=async()=>{
   const version=++voiceScanVersion,dir=directory();
   if(!dir){refreshVoicesBtn.name='Refresh Voices (connect Server Control)';node.setDirtyCanvas(true,true);return;}
   refreshVoicesBtn.name='Scanning Voices…';node.setDirtyCanvas(true,true);
   try{
    const r=await api.fetchApi(`/fishs2/voices/list?directory=${encodeURIComponent(dir)}`);
    if(!r.ok)throw Error(`HTTP ${r.status}`);
    const d=await r.json();if(!d.ok)throw Error(d.error||'Voice scan failed');
    if(version!==voiceScanVersion || dir!==directory())return;
    for(const w of voices){if(!w)continue;
     const selected=w._s2overridden?w._s2cachedValue:w.value;
     const options=['',...new Set(d.voices||[])];
     if(selected&&!options.includes(selected))options.push(selected);
     w.options={...(w.options||{}),values:options};
    }
    refreshVoicesBtn.name=`Refresh Voices (${(d.voices||[]).length})`;
    update();
   }catch(e){if(version!==voiceScanVersion)return;refreshVoicesBtn.name='Refresh Voices (scan failed)';console.warn('[FishS2] Voice scan failed:',e);}
   finally{node.setDirtyCanvas(true,true);}
  };
  node._fishS2RefreshVoices=load;
  const connected=node.onConnectionsChange;node.onConnectionsChange=function(...a){const ret=connected?.apply(this,a);orderInputs();syncOverrides();setTimeout(load,100);return ret;};
  const progress=e=>{const d=e.detail||{};if(d.event==='start'){segments=(d.segments||[]).map(s=>({...s,done:false}));taskSubfolder=d.task_subfolder||'';phase=`Generating · 0/${segments.length}`;taskLog='';}
   else if(d.event==='completed'&&segments[d.index]){segments[d.index].done=true;phase=`Generating · ${segments.filter(s=>s.done).length}/${segments.length}`;}
   else if(d.event==='log')taskLog+=(taskLog?'\n':'')+(d.line||'');
   else if(d.event==='done')phase='Saving WAV';status.value=phase;render();node.setDirtyCanvas(true,true);};
  api.addEventListener('fishs2:segment',progress);
  const executed=node.onExecuted;node.onExecuted=function(msg){executed?.apply(this,arguments);if(audioPreview){audioPreview.pause();audioPreview.src='';audioPreview=null;preview.name='▶ Preview WAV';}node.__s2URL='';node.__s2Path=msg?.fishs2_path?.[0]||'';taskLog=msg?.fishs2_log?.[0]||'';wav.value=node.__s2Path;const a=msg?.audio?.[0];if(a){node.__s2URL=api.apiURL(`/view?${new URLSearchParams({filename:a.filename,subfolder:a.subfolder||'',type:'output'})}`);phase='Generation complete';status.value=phase;}render();node.setDirtyCanvas(true,true);};
  const removed=node.onRemoved;node.onRemoved=function(...a){if(audioPreview){audioPreview.pause();audioPreview.src='';audioPreview=null;}if(completedAudio){completedAudio.pause();completedAudio=null;}close();api.removeEventListener('fishs2:segment',progress);return removed?.apply(this,a);};
  const configured=node.onConfigure;node.onConfigure=function(...a){const ret=configured?.apply(this,a);setTimeout(()=>{syncOverrides();load();},350);return ret;};
  update();setTimeout(load,400);return result;
 };
}});
