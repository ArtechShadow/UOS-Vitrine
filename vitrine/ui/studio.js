import { manageRun, showTrash } from './run-management.js';
/** Presentation workspace, backed by the existing local run and sidecar APIs. */
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = `<svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="m24 5 17 10v19L24 44 7 34V15L24 5Zm0 20L7 15m17 10 17-10M24 25v19" stroke="currentColor" stroke-width="1.3"/></svg>`;
const stages = [['images','01','Images','Your capture material'],['splat','02','3D splat','Explore the reconstruction'],['objects','03','Objects','Inspect separated objects']];
const title = (run, ctx) => run.title || ctx.displayName(run.name);
const captureKind = run => run.capture_type === 'object' ? 'Object capture' : 'Scene capture';
const fileLink = (run, path) => `/files/${encodeURIComponent(run.name)}/${path}`;

export function renderStudioLibrary(ctx) {
  const {state, viewEl, openRun, switchView, loadRuns, fmt} = ctx;
  const query = state.libraryQuery || '';
  const filter = state.libraryFilter || 'all';
  const allRuns = state.runs.flatMap(run => [run, ...(run.experiments || []).map(experiment => ({
    name: `${run.name}/${experiment.name}`, title: `${title(run,ctx)} · ${experiment.name}`,
    experiment, has_viewer: experiment.complete, capture_type: run.capture_type,
    preview: experiment.preview ? {url:experiment.preview,kind:'splat-render'} : run.preview,
    headline:{running:experiment.state==='running',interrupted:experiment.state==='failed'},
  }))]);
  const runs = allRuns.filter(r => title(r,ctx).toLowerCase().includes(query.toLowerCase()) && (filter !== 'ready' || r.has_viewer));
  viewEl.innerHTML = `<div class="studio-library">
    <header class="studio-heading"><div><p class="page-kicker">Your workspace</p><h2>Capture library</h2><p>Preserve spaces and objects from photographs.</p></div><button class="primary" id="studio-create">+ New capture</button></header>
    <div class="studio-process" aria-label="Preservation workflow">${stages.map(([id,n,label,sub])=>`<div><span>${n}</span><strong>${label}</strong><small>${sub}</small></div>`).join('')}</div>
    <a class="download-row" href="/?view=construction">Watch live construction ↗ <small>Training progress and checkpoint replay</small></a>
    <div class="library-controls"><label class="library-search"><span class="sr-only">Search captures</span><input type="search" id="studio-search" placeholder="Search captures…" value="${esc(query)}"/></label><div class="library-filters" aria-label="Filter captures"><button data-filter="all" aria-pressed="${filter==='all'}">All captures <span>${state.runs.length}</span></button><button data-filter="ready" aria-pressed="${filter==='ready'}">Ready to explore <span>${state.runs.filter(r=>r.has_viewer).length}</span></button></div><button class="ghost" id="studio-trash">Trash</button><button class="ghost" id="studio-refresh" aria-label="Refresh captures">Refresh</button></div>
    <div class="studio-captures">${runs.map(run=>`<button class="capture-tile" data-run="${esc(run.name)}"><div class="capture-cover">${run.preview?.url ? `<img src="${esc(run.preview.url)}" alt="" loading="lazy"/>` : `<div class="capture-no-image">${icon}<span>${run.has_viewer?'3D reconstruction':'Capture in progress'}</span></div>`}<span class="cover-label">${run.preview?.kind==='splat-render'?'Splat preview':run.preview?.url?'Source photograph':'Local capture'}</span><span class="cover-action">Open capture ↗</span></div><div class="capture-tile-body"><div class="capture-tile-title"><h3>${esc(title(run,ctx))}</h3><span class="capture-state ${run.has_viewer?'ready':''}">${run.capture_job?.running?'Processing':run.capture_job?.returncode?'Needs attention':run.headline?.running?'Building':run.has_viewer?'Ready to explore':run.headline?.interrupted?'Needs attention':'In preparation'}</span></div><p>${captureKind(run)} · ${run.headline?.accepted_images!=null?`${fmt(run.headline.accepted_images)} images · `:''}${run.headline?.cameras!=null?`${fmt(run.headline.cameras)} camera groups · `:''}${run.stages?.package?.done?'Archive packaged':'Archive not packaged'}</p><div class="capture-tile-foot"><span>${run.splat_created_mtime?'Created '+new Date(run.splat_created_mtime*1000).toLocaleString('en-GB',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}):'Splat not created yet'}</span><span>${run.objects?.count?`${fmt(run.objects.count)} objects`:'View workspace →'}</span></div></div></button>`).join('')}</div>
    ${runs.length?'':`<div class="studio-empty">${icon}<h3>${state.runs.length?'No matching captures':'Your first capture starts here'}</h3><p>${state.runs.length?'Try another name or choose All captures.':'Add photographs or a video of a space or object. Track its reconstruction and review the result here.'}</p><button class="soft" id="studio-empty-action">${state.runs.length?'Clear filters':'New capture'}</button></div>`}
    <div class="library-note"><span class="local-indicator"></span>Capture files and processing stay on this workstation.</div></div>`;
  viewEl.querySelector('#studio-create').onclick=()=>switchView('create');
  viewEl.querySelector('#studio-trash').onclick=()=>showTrash(ctx);
  viewEl.querySelector('#studio-refresh').onclick=()=>loadRuns(true);
  viewEl.querySelectorAll('[data-run]').forEach(el=>el.onclick=()=>{
    const selected = allRuns.find(run => run.name === el.dataset.run);
    if (selected?.experiment) location.href = selected.experiment.url;
    else openRun(el.dataset.run);
  });
  viewEl.querySelector('[data-filter="all"] span').textContent = allRuns.length;
  viewEl.querySelector('[data-filter="ready"] span').textContent = allRuns.filter(run=>run.has_viewer).length;
  viewEl.querySelectorAll('[data-run]').forEach(el=>{
    const experiment = allRuns.find(run=>run.name===el.dataset.run)?.experiment;
    if (!experiment) return;
    el.querySelector('.cover-action').textContent = 'Open run ↗';
    el.querySelector('.capture-state').textContent = experiment.state==='running' ? 'Building' : experiment.complete ? 'Output available' : experiment.state==='failed' ? 'Needs attention' : 'Preparing';
    el.querySelector('.capture-tile-body > p').textContent = 'Experiment · Original capture preserved';
    el.querySelector('.capture-tile-foot').textContent = experiment.complete ? 'Completed output · Review reconstruction →' : 'Follow live construction →';
  });
  viewEl.querySelectorAll('[data-filter]').forEach(el=>el.onclick=()=>{state.libraryFilter=el.dataset.filter;renderStudioLibrary(ctx);});
  viewEl.querySelector('#studio-search').oninput=e=>{const pos=e.target.selectionStart;state.libraryQuery=e.target.value;renderStudioLibrary(ctx);const input=viewEl.querySelector('#studio-search');input.focus();try{input.setSelectionRange(pos,pos);}catch{}};
  const empty=viewEl.querySelector('#studio-empty-action');if(empty)empty.onclick=()=>{if(!state.runs.length)return switchView('create');state.libraryQuery='';state.libraryFilter='all';renderStudioLibrary(ctx);};
}

export function renderStudioDetail(run,ctx) {
  const {state,viewEl,fmt,fmtBytes,switchView,startObjectSeparation} = ctx;
  const tab=state.studioTab || 'splat';
  state.studioTab=tab;
  const flow=run.object_workflow || {};
  const meshes=run.object_meshes || {};
  const meshState=meshes.status || {};
  const meshPanel=`<section class="inspector-section"><h3>Splat → surface mesh</h3><p>Experimental: render object depth, fuse the views and reconstruct a coloured PLY surface. The splat stays available. Texture baking and watertight geometry are not guaranteed.</p><p role="status" id="mesh-feedback">${esc(meshState.error || meshState.message || 'Ready after object separation.')}${meshState.state ? ' · '+esc(meshState.state) : ''}</p><button class="soft" id="btn-object-meshes" ${meshState.state==='running'||flow.running||!flow.outputs?.objects?.some(o=>o.splat_url)?'disabled':''}>${meshState.state==='running'?'Creating meshes…':'Create object meshes'}</button>${(meshes.objects||[]).map(m=>`<a class="download-row" href="${esc(m.url)}" download><span>${esc(m.label)} · coloured PLY${m.stale?' · previous separation':''}</span><small>${fmt(m.faces)} triangles ↓</small></a>`).join('')}<p>Generated meshes enter the archive on the next packaging run.</p></section>`;
  const h=run.headline || {};
  const evaluation=run.stages?.evaluate?.report;
  const measured=Number.isFinite(evaluation?.overall_psnr) && Number.isFinite(evaluation?.overall_ssim);
  const quality=measured?{psnr:evaluation.overall_psnr,ssim:evaluation.overall_ssim}:h;
  const jobMessage=run.capture_job?.running
    ? (!run.stages?.ingest?.done?'Preparing images':!run.stages?.sfm?.done?'Mapping camera positions':!run.stages?.train?.done?'Reconstructing the 3D splat':'Preparing the preservation package')
    : run.capture_job?.returncode ? 'Processing stopped. Open Advanced view to inspect the log before retrying.' : '';
  const samples=run.samples || [];
  const priorViewer=viewEl.querySelector('iframe[data-studio-viewer]');
  const viewerUrl=run.viewer_url;
  const objectCount=flow.outputs?.objects?.length || 0;
  const status = tab==='images' ? `${samples.length} preview images` : tab==='splat' ? (run.has_viewer?'Interactive reconstruction':'Model not available yet') : (objectCount?`${objectCount} object candidates`:flow.running?'Separation in progress':flow.configured?'Ready for separation':'Separator not connected');
  const imagePane=`<div class="source-toolbar"><div><h3>Source photographs</h3><p>${h.accepted_images!=null?`${fmt(h.accepted_images)} accepted images. `:''}A selection of the capture material used for this reconstruction.</p></div><span class="quiet-badge">${h.cameras!=null?`${fmt(h.cameras)} camera groups`:'Local media'}</span></div><div class="studio-photo-grid">${samples.map(s=>`<button class="source-photo" data-photo="${esc(s.url)}" data-caption="${esc(s.name)}"><img src="${esc(s.url)}" alt="${esc(s.name)}" loading="lazy"/><span>${esc(s.group.replaceAll('_',' '))}</span></button>`).join('')}</div>${samples.length?'':`<div class="studio-empty">${icon}<h3>No source previews available</h3><p>Previews appear after photographs have been prepared. Existing model files can still be explored in the 3D splat stage.</p></div>`}`;
  const splatPane=`<iframe class="construction-frame" data-studio-viewer="${esc(run.name)}" src="/static/construction.html?embedded=1&run=${encodeURIComponent(run.name)}" title="Live construction of ${esc(title(run,ctx))}" allow="fullscreen"></iframe>`;
  const objectsPane=`${meshPanel}<div class="source-toolbar"><div><h3>Object isolation & separation</h3><p>Candidates can include nearby geometry. Compare each 3D result with its source crop before reuse.</p></div><span class="quiet-badge">${flow.running?'Processing':objectCount?'Results available':flow.configured?'Connected':'Not connected'}</span></div>${objectCount?`<div class="studio-object-grid">${flow.outputs.objects.map(o=>`<article class="studio-object"><div>${o.thumb_url?`<img src="${esc(o.thumb_url)}" alt="${esc(o.label || o.object_id)}"/>`:icon}</div><h4>${esc(o.label || o.object_id)}</h4>${o.thumb_kind==='source-crop'?'<small class="muted">Source crop · inspect the 3D result below</small>':''}${o.splat_url?`<a class="soft" href="${esc(o.viewer_url)}" target="_blank" rel="noopener">Explore isolated splat ↗</a><br/><a href="${esc(o.splat_url)}" download>Download splat ↓</a>`:o.mesh_url?`<a class="soft" href="/static/mesh-viewer.html?run=${encodeURIComponent(run.name)}&asset=${encodeURIComponent('objects/'+decodeURIComponent(o.mesh_url.split('/objects/')[1]))}&label=${encodeURIComponent(o.label || o.object_id)}" target="_blank" rel="noopener">Inspect 3D model ↗</a><br/><a href="${esc(o.mesh_url)}" download>Download GLB ↓</a>`:'<span class="muted">No 3D asset available</span>'}</article>`).join('')}</div>`:`<div class="studio-empty object-empty-state">${icon}<h3>${flow.running?'Separating objects':flow.configured?'Ready to separate your scene':'Connect the object separator'}</h3><p>${flow.running?'The local sidecar is processing this capture. Validated outputs will appear here automatically.':flow.configured?'Use the reconstruction and registered photographs to recover individual 3D assets.':'This capture has no separated objects yet. A compatible local sidecar must be connected before this step can run.'}</p>${!flow.configured?'<details><summary>Connection details</summary><p>Configure VITRINE_OBJECT_SIDECAR on this workstation and restart the dashboard. The separator runs as a separate local process.</p></details>':''}</div>`}<div class="object-stage-actions"><span id="object-feedback" role="status">${!flow.ready?'A reconstructed model is required.':flow.running?'Processing continues if you leave this page.':objectCount?'Original reconstruction retained.':'The original reconstruction is retained.'}</span><button class="primary" id="btn-separate-objects" ${!flow.ready||!flow.configured||flow.running?'disabled':''}>${flow.running?'Separating…':objectCount?'Separate again':'Separate objects'}</button></div>${flow.outputs?.composed_scene?`<a href="${esc(flow.outputs.composed_scene.url)}" download>Download composed scene ↓</a>`:''}`;
  const markup=`<div class="studio-detail"><header class="studio-heading"><div><button class="back" id="studio-back">← Capture library</button><h2>${esc(title(run,ctx))}</h2><p>${esc(status)}</p></div><div class="studio-header-actions"><span class="quiet-badge"><span class="local-indicator"></span> Local workspace</span><button class="soft" id="studio-rename">Rename</button><button class="ghost" id="studio-delete">Delete</button><button class="soft" id="studio-present" aria-pressed="${!!state.presenting}">${state.presenting?'Exit presentation':'Present'}</button></div></header>
    <nav class="studio-tabs" aria-label="Capture stages">${stages.map(([id,n,label,sub])=>`<button data-stage="${id}"  aria-current="${id===tab?'step':'false'}"><span class="stage-number">${n}</span><span><strong>${label}</strong><small>${id==='images'?(h.accepted_images!=null?`${fmt(h.accepted_images)} images`:'Capture material'):id==='splat'?(run.has_viewer?'Explore & replay':'Live construction'):(flow.running?'Separating objects':objectCount?`${objectCount} candidates`:'Object workspace')}</small></span><span class="stage-arrow">→</span></button>`).join('')}</nav>
    ${jobMessage && !h.running ? `<div class="studio-progress" role="status">${esc(jobMessage)}</div>` : ''}
    ${h.running?`<div class="studio-progress" role="status"><span>Building 3D splat</span><progress max="${h.iterations||100}" value="${h.step||0}"></progress><span>${fmt(h.step)} / ${fmt(h.iterations)} steps${h.eta_minutes!=null?` · ~${fmt(h.eta_minutes)} min remaining`:''}</span></div>`:''}
    <div class="studio-workspace"><section class="studio-canvas ${tab==='splat'?'is-splat':''}" aria-label="${esc(tab)} workspace">${tab==='images'?imagePane:tab==='splat'?splatPane:objectsPane}</section><aside class="studio-inspector"><p class="page-kicker">Capture record</p><h3>Preserved in detail.</h3><dl><div><dt>Capture type</dt><dd>${captureKind(run)}</dd></div><div><dt>Input images</dt><dd>${h.accepted_images!=null?fmt(h.accepted_images):'Not recorded'}</dd></div><div><dt>Registered views</dt><dd>${h.registered_images!=null?fmt(h.registered_images):'Not recorded'}</dd></div><div><dt>Trained splats</dt><dd>${h.n_gaussians!=null?fmt(h.n_gaussians):'Not recorded'}</dd></div><div><dt>Build time</dt><dd>${h.minutes!=null?`${fmt(h.minutes,1)} min`:'Not recorded'}</dd></div></dl><details class="inspector-section"><summary>Advanced · quality record</summary><div class="quality-pair"><div><strong>${quality.psnr!=null?fmt(quality.psnr,2):'—'}</strong><span>PSNR · dB</span></div><div><strong>${quality.ssim!=null?fmt(quality.ssim,3):'—'}</strong><span>SSIM · 0–1</span></div></div><p>${measured?'Evaluated saved PLY.':h.metric_source==='export'?'Recorded export metrics.':'Recorded training metrics.'} The interactive splat is a viewing derivative.</p><span class="record-status">${run.stages?.evaluate?.done?'Evaluation report available':'Separate evaluation report not recorded'}</span></details><div class="inspector-section"><h4>Preservation archive</h4><p>${run.stages?.package?.done?'Archive manifest available. Use checksum verification before deposit.':'Not packaged yet. Originals, poses and checksums belong in the preservation package.'}</p>${run.stages?.package?.done?`<a href="${fileLink(run,'archive/manifest.json')}" target="_blank" rel="noopener">View manifest ↗</a>`:''}</div><div class="inspector-section"><h4>Download files</h4>${run.artefacts?.scene_splat?`<a class="download-row" href="${fileLink(run,'model/scene.splat')}" download><span>Web splat</span><small>${fmtBytes(run.artefacts.scene_splat.bytes)} ↓</small></a>`:''}${run.artefacts?.scene_ply?`<a class="download-row" href="${fileLink(run,'model/scene.ply')}" download><span>Master PLY</span><small>${fmtBytes(run.artefacts.scene_ply.bytes)} ↓</small></a>`:''}</div></aside></div></div><dialog class="photo-dialog"><button class="ghost" aria-label="Close photograph">Close ×</button><img alt=""/><p></p></dialog>`;
  if(priorViewer && tab==='splat' && priorViewer.dataset.studioViewer===run.name){
    // Refresh metadata without detaching the iframe and losing the user's camera.
    const next=document.createElement('div');next.innerHTML=markup;
    for(const selector of ['.studio-heading','.studio-tabs','.studio-inspector']) {
      viewEl.querySelector(selector).replaceWith(next.querySelector(selector));
    }
    viewEl.querySelector('.studio-progress')?.remove();
    const progress=next.querySelector('.studio-progress');
    if(progress)viewEl.querySelector('.studio-tabs').after(progress);
  } else viewEl.innerHTML=markup;
  viewEl.querySelector('#studio-back').onclick=()=>{state.selected=null;switchView('runs');};
  viewEl.querySelectorAll('[data-stage]').forEach(el=>el.onclick=()=>{state.studioTab=el.dataset.stage;renderStudioDetail(run,ctx);});
  viewEl.querySelector('#studio-rename').onclick=()=>manageRun(run,ctx,'rename');
  viewEl.querySelector('#studio-delete').onclick=()=>manageRun(run,ctx,'trash');
  viewEl.querySelector('#studio-present').onclick=()=>{state.presenting=!state.presenting;document.body.classList.toggle('presentation-mode',state.presenting);const b=viewEl.querySelector('#studio-present');b.textContent=state.presenting?'Exit presentation':'Present';b.setAttribute('aria-pressed',String(state.presenting));};
  const meshButton=viewEl.querySelector('#btn-object-meshes');
  if(meshButton)meshButton.onclick=async()=>{
    meshButton.disabled=true;
    const feedback=viewEl.querySelector('#mesh-feedback');
    feedback.textContent='Starting surface reconstruction…';
    try {
      const response=await fetch(`/api/runs/${encodeURIComponent(run.name)}/object-meshes`,{method:'POST'});
      const result=await response.json();
      if(!response.ok)throw new Error(result.error||'Could not start meshing');
      feedback.textContent='Mesh generation started. Processing continues in the background.';
      await ctx.openRun(run.name);
    } catch(error) {feedback.textContent=error.message;meshButton.disabled=false;}
  };
  const separate=viewEl.querySelector('#btn-separate-objects');if(separate)separate.onclick=()=>startObjectSeparation(run.name);
  const dialog=viewEl.querySelector('dialog');viewEl.querySelectorAll('[data-photo]').forEach(el=>el.onclick=()=>{dialog.querySelector('img').src=el.dataset.photo;dialog.querySelector('img').alt=el.dataset.caption;dialog.querySelector('p').textContent=el.dataset.caption;dialog.showModal();});dialog.querySelector('button').onclick=()=>dialog.close();dialog.onclick=e=>{if(e.target===dialog)dialog.close();};
}

