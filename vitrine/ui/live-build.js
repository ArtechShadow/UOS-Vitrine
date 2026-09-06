const $=id=>document.getElementById(id);
let jobs=[],selected='',following=true,index=0,playback=null,apiOrigin='';
const number=v=>Number.isFinite(v)?v.toLocaleString('en-GB',{maximumFractionDigits:1}):'—';
function show(){
  const job=jobs.find(j=>j.id===selected);if(!job)return;
  const p=job.progress,snapshots=job.snapshots;
  $('state').textContent=job.state==='complete'?'Training complete':job.state==='running'?'Reconstructing now':'No recent update · check the training process';
  const total=p.iterations||p.profile?.iterations||0,step=job.state==='complete'?total:p.step||0;
  $('progress').value=total?100*step/total:0;
  const values=[['Training step',`${number(step)} / ${number(total)}`],['Gaussians',number(p.n_gaussians)],['Elapsed',`${number(p.elapsed_minutes??p.minutes)} min`],['Estimated remaining',job.state==='complete'?'Complete':`${number(p.eta_minutes)} min`]];
  $('metrics').replaceChildren(...values.map(([label,value])=>{const box=document.createElement('div');const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;box.append(dt,dd);return box;}));
  $('timeline').max=Math.max(0,snapshots.length-1);$('timeline').disabled=!snapshots.length;$('play').disabled=snapshots.length<2;
  if(following)index=snapshots.length-1;index=Math.max(0,Math.min(index,snapshots.length-1));$('timeline').value=index;
  $('live').setAttribute('aria-pressed',String(following));$('mode').textContent=following?'Live':playback?'Recorded replay':'Recorded checkpoint';
  if(snapshots.length){const shot=snapshots[index],url=apiOrigin+shot.url;$('preview').hidden=false;$('empty').hidden=true;if($('preview').getAttribute('src')!==url)$('preview').src=url;$('caption').textContent=`Step ${number(shot.step)} · ${shot.camera} · recorded training render`;}
  else{$('preview').hidden=true;$('empty').hidden=false;$('empty').textContent='Training progress is available. A construction image will appear at the next evaluation checkpoint in runs started with preview capture enabled.';$('caption').textContent='No construction snapshots have been recorded for this run yet.';}
}
function stop(){clearInterval(playback);playback=null;$('play').textContent='Replay construction';}
$('job').onchange=()=>{selected=$('job').value;stop();following=true;show();};
$('live').onclick=()=>{stop();following=true;show();};
$('timeline').oninput=()=>{stop();following=false;index=Number($('timeline').value);show();};
$('play').onclick=()=>{if(playback){stop();show();return;}following=false;index=0;playback=setInterval(()=>{const count=jobs.find(j=>j.id===selected)?.snapshots.length||0;if(index>=count-1){stop();show();return;}index++;show();},1500);$('play').textContent='Pause replay';show();};
async function poll(){try{let r=await fetch(apiOrigin+'/api/construction',{cache:'no-store'});if(r.status===404 && !apiOrigin){apiOrigin='http://127.0.0.1:8768';r=await fetch(apiOrigin+'/api/construction',{cache:'no-store'});}if(!r.ok)throw Error();jobs=(await r.json()).jobs;if(!jobs.some(j=>j.id===selected))selected=jobs[0]?.id||'';$('job').replaceChildren(...jobs.map(j=>{const option=document.createElement('option');option.value=j.id;option.textContent=j.label==='appearance-off'?'Video quality test · Version 1':j.label==='appearance-on'?'Video quality test · Version 2':`${j.run.replaceAll('-',' ')} · ${j.label}`;return option;}));$('job').value=selected;$('connection').textContent=jobs.length?'Live monitor · refreshed every 3 seconds':'No active builds or recorded experiments found.';show();}catch{$('connection').textContent='Connection interrupted. Retrying…';}setTimeout(poll,3000);}
poll();
