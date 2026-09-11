import { manageRun, showTrash } from './run-management.js';
// Keep one page scrollbar while letting the embedded studio size its content.
function sizeConstructionViewer(frame) {
  frame?.contentWindow?.postMessage({type:'vitrine:viewer-height', height:Math.max(520, Math.min(1100, innerHeight * .82))}, location.origin);
}
window.addEventListener('message', event => {
  if (event.origin !== location.origin) return;
  const frame = document.querySelector('iframe[data-studio-viewer]');
  if (!frame || event.source !== frame.contentWindow) return;
  if (event.data?.type === 'vitrine:construction-ready') sizeConstructionViewer(frame);
  if (event.data?.type === 'vitrine:construction-height' && Number.isFinite(event.data.height)) {
    frame.style.height = `${Math.max(520, Math.min(10000, event.data.height))}px`;
  }
});
window.addEventListener('resize', () => sizeConstructionViewer(document.querySelector('iframe[data-studio-viewer]')));
/** Presentation workspace, backed by the existing local run and sidecar APIs. */
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = `<svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="m24 5 17 10v19L24 44 7 34V15L24 5Zm0 20L7 15m17 10 17-10M24 25v19" stroke="currentColor" stroke-width="1.3"/></svg>`;
const stages = [['images','01','Images','Your capture material'],['splat','02','3D splat','Explore the reconstruction'],['objects','03','Objects','Inspect separated objects']];
const title = (run, ctx) => run.title || ctx.displayName(run.name);
const captureKind = run => run.capture_type === 'object' ? 'Object capture' : 'Scene capture';
const fileLink = (run, path) => `/files/${encodeURIComponent(run.name)}/${path}`;

function libraryKindTabs(kind, sceneCount, objectCount) {
  return `<div class="library-kind" role="tablist" aria-label="Library collection">
    <button type="button" role="tab" data-library-kind="scene" aria-pressed="${kind==='scene'}"><strong>Scene</strong><small>${sceneCount} ${sceneCount===1?'space':'spaces'}</small></button>
    <button type="button" role="tab" data-library-kind="object" aria-pressed="${kind==='object'}"><strong>Object</strong><small>${objectCount} ${objectCount===1?'item':'items'}</small></button>
  </div>`;
}

function isSceneRun(run) {
  return run.capture_type !== 'object';
}

function isBuilding(run) {
  return !!(run.capture_job?.running || run.headline?.running || run.headline?.worker_state === 'running');
}

function tileState(run) {
  if (run.capture_job?.running) return 'Processing';
  if (run.capture_job?.stale || run.capture_job?.recovery_required || run.headline?.worker_stale || run.headline?.interrupted) return 'Needs attention';
  if (isBuilding(run)) return 'Building';
  if (run.has_viewer) return 'Ready to explore';
  return 'In preparation';
}

function bindLibraryKind(ctx) {
  ctx.viewEl.querySelectorAll('[data-library-kind]').forEach(el => {
    el.onclick = () => {
      ctx.state.libraryKind = el.dataset.libraryKind;
      ctx.switchView(el.dataset.libraryKind === 'object' ? 'objects' : 'runs');
    };
  });
}

export function collectSeparatedObjects(runs, ctx) {
  const items = [];
  for (const run of runs) {
    const records = run.objects?.objects || run.object_workflow?.outputs?.objects || [];
    for (const object of records) {
      items.push({
        ...object,
        parent: run.name,
        parentTitle: title(run, ctx),
      });
    }
  }
  return items;
}

function captureTile(run, ctx, {action} = {}) {
  const {fmt} = ctx;
  const building = isBuilding(run);
  return `<button class="capture-tile" data-run="${esc(run.name)}"><div class="capture-cover">${run.preview?.url ? `<img src="${esc(run.preview.url)}" alt="" loading="lazy"/>` : `<div class="capture-no-image">${icon}<span>${run.has_viewer?'3D reconstruction':'Capture in progress'}</span></div>`}<span class="cover-label">${run.preview?.kind==='splat-render'?'Splat preview':run.preview?.url?'Source photograph':'Local capture'}</span><span class="cover-action">${action || (building?'Watch live →':'Open capture ↗')}</span></div><div class="capture-tile-body"><div class="capture-tile-title"><h3>${esc(title(run,ctx))}</h3><span class="capture-state ${run.has_viewer?'ready':''}">${tileState(run)}</span></div><p>${captureKind(run)} · ${run.headline?.accepted_images!=null?`${fmt(run.headline.accepted_images)} images · `:''}${run.headline?.cameras!=null?`${fmt(run.headline.cameras)} camera groups · `:''}${run.stages?.package?.done?'Archive packaged':'Archive not packaged'}</p><div class="capture-tile-foot"><span>${run.splat_created_mtime?'Created '+new Date(run.splat_created_mtime*1000).toLocaleString('en-GB',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}):'Splat not created yet'}</span><span>${run.objects?.count?`${fmt(run.objects.count)} objects`:'View workspace →'}</span></div></div></button>`;
}

export function renderStudioLibrary(ctx) {
  const {state, viewEl, openRun, switchView, loadRuns, fmt} = ctx;
  const query = state.libraryQuery || '';
  const filter = state.libraryFilter || 'all';
  const sceneRuns = state.runs.filter(isSceneRun);
  const objectRuns = state.runs.filter(run => run.capture_type === 'object');
  const separated = collectSeparatedObjects(state.runs, ctx);
  const allRuns = sceneRuns.flatMap(run => [run, ...(run.experiments || []).map(experiment => ({
    name: `${run.name}/${experiment.name}`, title: `${title(run,ctx)} · ${experiment.name}`,
    experiment, has_viewer: experiment.complete, capture_type: run.capture_type,
    preview: experiment.preview ? {url:experiment.preview,kind:'splat-render'} : run.preview,
    headline:{running:experiment.state==='running',interrupted:experiment.state==='failed'},
  }))]);
  const runs = allRuns.filter(r => title(r,ctx).toLowerCase().includes(query.toLowerCase()) && (filter !== 'ready' || r.has_viewer));
  viewEl.innerHTML = `<div class="studio-library">
    <header class="studio-heading"><div><p class="page-kicker">Your workspace</p><h2>Scene library</h2><p>Rooms, installations and places preserved from photographs.</p></div><button class="primary" id="studio-create">+ New capture</button></header>
    ${libraryKindTabs('scene', sceneRuns.length, objectRuns.length + separated.length)}
    <div class="studio-process" aria-label="Preservation workflow">${stages.map(([id,n,label,sub])=>`<div><span>${n}</span><strong>${label}</strong><small>${sub}</small></div>`).join('')}</div>
    <div class="library-controls"><label class="library-search"><span class="sr-only">Search captures</span><input type="search" id="studio-search" placeholder="Search scenes…" value="${esc(query)}"/></label><div class="library-filters" aria-label="Filter captures"><button data-filter="all" aria-pressed="${filter==='all'}">All scenes <span></span></button><button data-filter="ready" aria-pressed="${filter==='ready'}">Ready to explore <span></span></button></div><button class="ghost" id="studio-trash">Trash</button><button class="ghost" id="studio-refresh" aria-label="Refresh captures">Refresh</button></div>
    <div class="studio-captures">${runs.map(run=>captureTile(run,ctx)).join('')}</div>
    ${runs.length?'':`<div class="studio-empty">${icon}<h3>${sceneRuns.length?'No matching scenes':'Your first scene starts here'}</h3><p>${sceneRuns.length?'Try another name or choose All scenes.':'Add photographs or a video of a room or installation. Track its reconstruction and review the result here.'}</p><button class="soft" id="studio-empty-action">${sceneRuns.length?'Clear filters':'New capture'}</button></div>`}
    <div class="library-note"><span class="local-indicator"></span>Capture files and processing stay on this workstation.</div></div>`;
  viewEl.querySelector('#studio-create').onclick=()=>{state.captureDraft={...(state.captureDraft||{}),capture_type:'scene'};switchView('create');};
  viewEl.querySelector('#studio-trash').onclick=()=>showTrash(ctx);
  viewEl.querySelector('#studio-refresh').onclick=()=>loadRuns(true);
  bindLibraryKind(ctx);
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
    el.querySelector('.cover-action').textContent = experiment.state==='running' ? 'Watch live →' : 'Open run ↗';
    el.querySelector('.capture-state').textContent = experiment.state==='running' ? 'Building' : experiment.complete ? 'Output available' : experiment.state==='failed' ? 'Needs attention' : 'Preparing';
    el.querySelector('.capture-tile-body > p').textContent = 'Experiment · Original capture preserved';
    el.querySelector('.capture-tile-foot').textContent = experiment.complete ? 'Completed output · Review reconstruction →' : 'Follow live construction →';
  });
  viewEl.querySelectorAll('[data-filter]').forEach(el=>el.onclick=()=>{state.libraryFilter=el.dataset.filter;renderStudioLibrary(ctx);});
  viewEl.querySelector('#studio-search').oninput=e=>{const pos=e.target.selectionStart;state.libraryQuery=e.target.value;renderStudioLibrary(ctx);const input=viewEl.querySelector('#studio-search');input.focus();try{input.setSelectionRange(pos,pos);}catch{}};
  const empty=viewEl.querySelector('#studio-empty-action');if(empty)empty.onclick=()=>{if(!sceneRuns.length)return switchView('create');state.libraryQuery='';state.libraryFilter='all';renderStudioLibrary(ctx);};
}

export function renderObjectLibrary(ctx) {
  const {state, viewEl, openRun, switchView, loadRuns, fmt, openSeparatedObject} = ctx;
  const query = (state.objectQuery || '').toLowerCase();
  const objectRuns = state.runs.filter(run => run.capture_type === 'object');
  const sceneRuns = state.runs.filter(isSceneRun);
  const separated = collectSeparatedObjects(state.runs, ctx).filter(item => {
    const hay = `${item.label || ''} ${item.object_id || ''} ${item.parentTitle || ''}`.toLowerCase();
    return !query || hay.includes(query);
  });
  const captures = objectRuns.filter(run => title(run, ctx).toLowerCase().includes(query));
  const objectCards = separated.map(item => `<button class="capture-tile" data-separated="${esc(item.parent)}" data-object-id="${esc(item.object_id || '')}">
    <div class="capture-cover">${item.thumb_url ? `<img src="${esc(item.thumb_url)}" alt="" loading="lazy"/>` : `<div class="capture-no-image">${icon}<span>Separated object</span></div>`}<span class="cover-label">From ${esc(item.parentTitle)}</span><span class="cover-action">Open object ↗</span></div>
    <div class="capture-tile-body"><div class="capture-tile-title"><h3>${esc(item.label || item.object_id || 'Unlabelled object')}</h3><span class="capture-state ${item.splat_url||item.viewer_url?'ready':''}">${item.splat_url||item.viewer_url?'Ready to explore':'Evidence only'}</span></div><p>Separated from ${esc(item.parentTitle)}</p><div class="capture-tile-foot"><span>${esc(item.asset_type || 'gaussian-splat')}</span><span>View object →</span></div></div>
  </button>`).join('');
  viewEl.innerHTML = `<div class="studio-library">
    <header class="studio-heading"><div><p class="page-kicker">Your workspace</p><h2>Object library</h2><p>Object captures and items separated from scenes.</p></div><button class="primary" id="studio-create">+ New object</button></header>
    ${libraryKindTabs('object', sceneRuns.length, objectRuns.length + collectSeparatedObjects(state.runs, ctx).length)}
    <div class="library-controls"><label class="library-search"><span class="sr-only">Search objects</span><input type="search" id="studio-search" placeholder="Search objects…" value="${esc(state.objectQuery || '')}"/></label><button class="ghost" id="studio-trash">Trash</button><button class="ghost" id="studio-refresh" aria-label="Refresh objects">Refresh</button></div>
    <section class="object-library-section"><h3>Object captures</h3>
      <div class="studio-captures">${captures.map(run=>captureTile(run,ctx)).join('')}</div>
      ${captures.length?'':`<div class="studio-empty">${icon}<h3>${objectRuns.length?'No matching object captures':'No object captures yet'}</h3><p>Photograph a single item from every angle, keep it still, and move the camera.</p><button class="soft" id="studio-empty-action">New object</button></div>`}
    </section>
    <section class="object-library-section"><h3>Separated from scenes</h3>
      <div class="studio-captures">${objectCards}</div>
      ${separated.length?'':`<div class="studio-empty">${icon}<h3>No separated objects yet</h3><p>Open a finished scene and use Separate objects to recover individual items. They will appear here.</p></div>`}
    </section>
    <div class="library-note"><span class="local-indicator"></span>Object files stay on this workstation.</div></div>`;
  viewEl.querySelector('#studio-create').onclick=()=>{state.captureDraft={...(state.captureDraft||{}),capture_type:'object'};switchView('create');};
  viewEl.querySelector('#studio-empty-action')?.addEventListener('click', ()=>{state.captureDraft={...(state.captureDraft||{}),capture_type:'object'};switchView('create');});
  viewEl.querySelector('#studio-trash').onclick=()=>showTrash(ctx);
  viewEl.querySelector('#studio-refresh').onclick=()=>loadRuns(true);
  bindLibraryKind(ctx);
  viewEl.querySelectorAll('[data-run]').forEach(el => el.onclick = () => openRun(el.dataset.run));
  viewEl.querySelectorAll('[data-separated]').forEach(el => el.onclick = () => openSeparatedObject?.(el.dataset.separated, el.dataset.objectId));
  viewEl.querySelector('#studio-search').oninput=e=>{const pos=e.target.selectionStart;state.objectQuery=e.target.value;renderObjectLibrary(ctx);const input=viewEl.querySelector('#studio-search');input.focus();try{input.setSelectionRange(pos,pos);}catch{}};
}

export function renderSeparatedObject(item, ctx) {
  const {state, viewEl, switchView, fmt, startObjectMesh} = ctx;
  const evidence = item.evidence?.items || [];
  viewEl.innerHTML = `<div class="studio-detail">
    <header class="studio-heading"><div><button class="back" id="studio-back">← Object library</button><h2>${esc(item.label || item.object_id || 'Object')}</h2><p>From ${esc(item.parentTitle || item.parent)}</p></div>
    <div class="studio-header-actions"><span class="quiet-badge"><span class="local-indicator"></span> Local workspace</span>
      <button class="ghost" id="open-parent">Open parent scene</button></div></header>
    <div class="studio-workspace"><section class="studio-canvas ${item.viewer_url?'is-splat':''}" aria-label="Object workspace">
      ${item.viewer_url
        ? `<iframe class="construction-frame" src="${esc(item.viewer_url)}" title="${esc(item.label || 'Object')}" allow="fullscreen"></iframe>`
        : `<div class="studio-empty">${item.thumb_url?`<img src="${esc(item.thumb_url)}" alt=""/>`:icon}<h3>No interactive splat yet</h3><p>Evidence from the parent scene is listed here. Reconstruct a surface when an observed splat is available.</p></div>`}
    </section>
    <aside class="studio-inspector"><p class="page-kicker">Object record</p><h3>${esc(item.label || 'Separated object')}</h3>
      <dl><div><dt>Parent scene</dt><dd>${esc(item.parentTitle || item.parent)}</dd></div>
      <div><dt>Object id</dt><dd class="mono">${esc(item.object_id || '—')}</dd></div>
      <div><dt>Asset</dt><dd>${esc(item.asset_type || '—')}</dd></div></dl>
      <div class="inspector-section"><h4>Evidence</h4>
        ${evidence.length ? `<div class="studio-object-evidence">${evidence.map(entry => `<a href="${esc(entry.url)}" target="_blank" rel="noopener">${esc(entry.label)} ↗</a>`).join('')}</div>` : '<p>No copied source or mask evidence.</p>'}
        ${item.thumb_kind==='source-crop'?'<small class="muted">Source crop · evidence preview, not a mesh render</small>':''}
      </div>
      <div class="inspector-section"><h4>Files</h4>
        ${item.viewer_url?`<a class="soft" href="${esc(item.viewer_url)}" target="_blank" rel="noopener">Explore observed splat ↗</a>`:''}
        ${item.splat_url?`<a class="download-row" href="${esc(item.splat_url)}" download><span>Observed splat</span><small>↓</small></a>`:''}
        ${item.mesh_url?`<a class="download-row" href="${esc(item.mesh_url)}" download><span>${esc(item.mesh_name || 'Mesh')}</span><small>↓</small></a>`:''}
        ${item.splat_url && item.object_id && !item.mesh_url ? `<button type="button" class="soft" id="object-mesh">Reconstruct surface</button>` : ''}
      </div>
    </aside></div></div>`;
  viewEl.querySelector('#studio-back').onclick=()=>switchView('objects');
  viewEl.querySelector('#open-parent').onclick=()=>{state.studioTab='objects';ctx.openRun(item.parent);};
  const mesh=viewEl.querySelector('#object-mesh');
  if (mesh) mesh.onclick=()=>startObjectMesh?.(item.parent, item.object_id);
}

export function renderStudioDetail(run,ctx) {
  const {state,viewEl,fmt,fmtBytes,switchView,openRun,startObjectSeparation,startObjectMesh,controlRun,recoveryState} = ctx;
  const objectCapture = run.capture_type === 'object';
  const availableStages = objectCapture ? stages.filter(([id]) => id !== 'objects') : stages;
  const tab=availableStages.some(([id]) => id === state.studioTab) ? state.studioTab : 'splat';
  state.studioTab=tab;
  const flow=run.object_workflow || {};
  const meshes=run.object_meshes || {};
  const meshState=meshes.status || {};
  const meshById = new Map((meshes.objects || []).map(m => [m.object_id, m]));
  const recovery = recoveryState ? recoveryState(run) : {};
  const recoveryControls = recovery.pipeline?.schema || recovery.running ? [
    recovery.running ? '<button type="button" class="ghost" id="studio-cancel-run">Stop processing</button>' : '',
    recovery.recoverable ? '<button type="button" class="primary" id="studio-resume-run">Resume build</button>' : '',
    '<button type="button" class="ghost" id="studio-refresh-run">Refresh state</button>',
  ].join(' ') : '';
  const recoveryBanner = recovery.running ? '' : recovery.stale || recovery.worker === 'unknown'
    ? '<div class="live-banner interrupted" role="status">This build needs attention: the worker is no longer running and no terminal state was recorded. Completed stages remain available. Use Resume to continue from verified stages.</div>'
    : recovery.worker === 'failed' || recovery.headline?.interrupted
      ? `<div class="live-banner interrupted" role="status">This build stopped before completion. ${esc(recovery.job?.error || recovery.headline?.worker_error || recovery.pipeline?.error || 'Review the saved log before retrying.')}</div>`
      : '';
  const meshLinks = (mesh, object) => {
    if (!mesh) return '';
    const label = esc(object.label || object.object_id || 'Object');
    const glb = mesh.glb_url || '';
    let glbAsset = '';
    if (glb) {
      const marker = `/files/${encodeURIComponent(run.name)}/`;
      if (glb.startsWith(marker)) {
        try { glbAsset = decodeURIComponent(glb.slice(marker.length)); } catch { glbAsset = ''; }
      }
    }
    const inspect = glb && glbAsset
      ? `<a class="soft" href="/static/mesh-viewer.html?run=${encodeURIComponent(run.name)}&asset=${encodeURIComponent(glbAsset)}&label=${encodeURIComponent(object.label || object.object_id || 'Object')}" target="_blank" rel="noopener">Inspect reconstructed GLB ↗</a>`
      : '';
    const ply = mesh.url ? `<a href="${esc(mesh.url)}" download>Download reconstructed PLY ↓</a>` : '';
    const glbDownload = glb ? `<a href="${esc(glb)}" download>Download GLB ↓</a>` : '';
    const warning = mesh.stale ? '<small class="muted">Previous separation · re-run after source changes</small>' : '';
    return `${inspect}${glbDownload}${ply}${warning}`;
  };
  const meshPanel=`<section class="inspector-section"><h3>Splat → surface mesh</h3><p>Reconstruction uses only object-supported views and reports insufficient support instead of filling unseen surfaces. The observed splat stays available for comparison; texture baking and watertight geometry are not guaranteed.</p><p role="status" id="mesh-feedback">${esc(meshState.error || meshState.message || (meshState.state === 'unknown' ? 'Surface reconstruction state is unknown; inspect the log before retrying.' : 'Select an object below to reconstruct its surface.'))}${meshState.state ? ' · '+esc(meshState.state) : ''}</p><button class="soft" id="btn-object-meshes" ${meshState.state==='running'||flow.running||!flow.outputs?.objects?.some(o=>o.splat_url && o.object_id)?'disabled':''}>${meshState.state==='running'?'Creating meshes…':'Create all candidate surfaces'}</button>${(meshes.objects||[]).map(m=>`<a class="download-row" href="${esc(m.url)}" download><span>${esc(m.label || m.object_id || 'Object')} · observed PLY${m.stale?' · previous separation':''}</span><small>${m.faces != null ? `${fmt(m.faces)} triangles` : 'validated output'} ↓</small></a>`).join('')}<p>Generated meshes enter the archive on the next packaging run.</p></section>`;
  const h=run.headline || {};
  const evaluation=run.stages?.evaluate?.report;
  const measured=Number.isFinite(evaluation?.overall_psnr) && Number.isFinite(evaluation?.overall_ssim);
  const quality=measured?{psnr:evaluation.overall_psnr,ssim:evaluation.overall_ssim}:h;
  const jobMessage=run.capture_job?.running
    ? (!run.stages?.ingest?.done?'Preparing images':!run.stages?.sfm?.done?'Mapping camera positions':!run.stages?.train?.done?'Reconstructing the 3D splat':'Preparing the preservation package')
    : run.capture_job?.stale || h.interrupted ? 'Processing stopped before a terminal result. Review the saved log and resume verified stages.' : '';
  const samples=run.samples || [];
  const priorViewer=viewEl.querySelector('iframe[data-studio-viewer]');
  const viewerUrl=run.viewer_url;
  const objectCount=flow.outputs?.objects?.length || 0;
  const status = tab==='images' ? `${samples.length} preview images` : tab==='splat' ? (run.has_viewer?'Interactive reconstruction':'Model not available yet') : (objectCount?`${objectCount} object candidates`:flow.running?'Separation in progress':flow.configured?'Ready for separation':'Separator not connected');
  const imagePane=`<div class="source-toolbar"><div><h3>Source photographs</h3><p>${h.accepted_images!=null?`${fmt(h.accepted_images)} accepted images. `:''}A selection of the capture material used for this reconstruction.</p></div><span class="quiet-badge">${h.cameras!=null?`${fmt(h.cameras)} camera groups`:'Local media'}</span></div><div class="studio-photo-grid">${samples.map(s=>`<button class="source-photo" data-photo="${esc(s.url)}" data-caption="${esc(s.name)}"><img src="${esc(s.url)}" alt="${esc(s.name)}" loading="lazy"/><span>${esc(s.group.replaceAll('_',' '))}</span></button>`).join('')}</div>${samples.length?'':`<div class="studio-empty">${icon}<h3>No source previews available</h3><p>Previews appear after photographs have been prepared. Existing model files can still be explored in the 3D splat stage.</p></div>`}`;
  const splatPane=`<iframe class="construction-frame" data-studio-viewer="${esc(run.name)}" src="/static/construction.html?embedded=1&hosted=1&run=${encodeURIComponent(run.name)}${state.advanced?'&advanced=1':''}" title="Live construction of ${esc(title(run,ctx))}" allow="fullscreen"></iframe>`;
  const objectRecords = flow.outputs?.objects || [];
  const objectCards = objectRecords.map(o => {
    const mesh = meshById.get(o.object_id);
    const evidence = o.evidence?.items || [];
    const identity = Object.entries(o.evidence?.identity || {})
      .map(([key, value]) => `${key}=${value}`).join(' · ');
    const observations = o.evidence?.observations || [];
    const observedIdentity = Object.entries(observations[0] || {})
      .filter(([key]) => ['image_id', 'camera_id', 'instance_id', 'detection_id', 'source_frame_id'].includes(key))
      .map(([key, value]) => `${key}=${value}`).join(' · ');
    const observationText = observations.length
      ? `${observations.length} registered mask/frame association${observations.length === 1 ? '' : 's'}${observedIdentity ? ` · ${observedIdentity}` : identity ? ` · ${identity}` : ''}`
      : identity;
    const evidenceLinks = evidence.length
      ? `<div class="studio-object-evidence" aria-label="Source evidence">${evidence.map(item => `<a href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.label)} ↗</a>`).join(' ')}</div>`
      : '<small class="muted">No copied source or mask evidence</small>';
    const observed = o.splat_url
      ? `<a class="soft" href="${esc(o.viewer_url || '#')}" target="_blank" rel="noopener">Explore observed splat ↗</a><br/><a href="${esc(o.splat_url)}" download>Download observed splat ↓</a>`
      : '';
    const reconstruct = !mesh && o.splat_url && o.object_id && meshState.state !== 'running'
      ? `<button type="button" class="soft" data-object-mesh="${esc(o.object_id)}">Reconstruct surface</button>`
      : '';
    const previousMesh = mesh ? meshLinks(mesh, o) : '';
    return `<article class="studio-object" data-object-id="${esc(o.object_id || '')}"><div>${o.thumb_url?`<img src="${esc(o.thumb_url)}" alt="Source evidence for ${esc(o.label || o.object_id || 'object')}"/>`:icon}</div><h4>${esc(o.label || o.object_id || 'Unlabelled object')}</h4><small class="muted">${o.thumb_kind==='source-crop'?'Source crop · evidence preview, not a mesh render':'Object evidence from the registered capture'}</small>${observationText?`<small class="muted mono">${esc(observationText)}</small>`:''}${evidenceLinks}${observed}${reconstruct}${previousMesh}${!observed&&!previousMesh?'<span class="muted">No observed 3D asset available</span>':''}</article>`;
  }).join('');
  const separatorReady = flow.ready && flow.source_ready !== false && flow.configured && flow.executable_available !== false && !flow.configuration_error && !flow.running;
  const objectsPane=`<div class="source-toolbar"><div><h3>Objects in this scene</h3><p>Separated items also appear in the Object library. Isolation keeps source evidence with each candidate.</p></div><button type="button" class="ghost" id="open-object-library">Open Object library</button></div>${meshPanel}<div class="source-toolbar"><div><h3>Object isolation & separation</h3><p>Each candidate keeps its source frame and mask evidence. Select a candidate to reconstruct only the supported object surface.</p></div><span class="quiet-badge">${flow.running?'Processing':objectCount?'Results available':flow.configuration_error?'Unavailable':flow.configured?'Connected':'Not connected'}</span></div>${objectCount?`<div class="studio-object-grid">${objectCards}</div>`:`<div class="studio-empty object-empty-state">${icon}<h3>${flow.running?'Separating objects':flow.configuration_error?'Object separator unavailable':flow.configured?'Ready to separate your scene':'Connect the object separator'}</h3><p>${flow.running?'The local sidecar is processing this capture. Validated outputs will appear here automatically.':flow.configuration_error?esc(flow.configuration_error):flow.configured?'Use the reconstruction and registered photographs to recover individual 3D assets.':'This capture has no separated objects yet. A compatible local sidecar must be connected before this step can run.'}</p>${!flow.configured?'<details><summary>Connection details</summary><p>Configure VITRINE_OBJECT_SIDECAR on this workstation and restart the dashboard. The separator runs as a separate local process.</p></details>':''}</div>`}<div class="object-stage-actions"><span id="object-feedback" role="status">${!flow.ready?'A reconstructed model is required.':flow.source_ready===false?'Registered source evidence is incomplete.':flow.configuration_error?esc(flow.configuration_error):flow.running?'Processing continues if you leave this page.':objectCount?'Original reconstruction retained.':'The original reconstruction is retained.'}</span><button class="primary" id="btn-separate-objects" ${!separatorReady?'disabled':''}>${flow.running?'Separating…':objectCount?'Separate again':'Separate objects'}</button></div>${flow.outputs?.composed_scene?`<a href="${esc(flow.outputs.composed_scene.url)}" download>Download composed scene ↓</a>`:''}`;
  const markup=`<div class="studio-detail"><header class="studio-heading"><div><button class="back" id="studio-back">${objectCapture?'← Object library':'← Scene library'}</button><h2>${esc(title(run,ctx))}</h2><p>${esc(status)}</p></div><div class="studio-header-actions"><span class="quiet-badge"><span class="local-indicator"></span> Local workspace</span><button class="soft" id="studio-rename">Rename</button><button class="ghost" id="studio-delete">Delete</button><button class="soft" id="studio-present" aria-pressed="${!!state.presenting}">${state.presenting?'Exit presentation':'Present'}</button>${recoveryControls}</div></header>
    ${recoveryBanner}
    <nav class="studio-tabs ${objectCapture?'tabs-2':''}" aria-label="Capture stages">${availableStages.map(([id,n,label,sub])=>`<button data-stage="${id}"  aria-current="${id===tab?'step':'false'}"><span class="stage-number">${n}</span><span><strong>${label}</strong><small>${id==='images'?(h.accepted_images!=null?`${fmt(h.accepted_images)} images`:'Capture material'):id==='splat'?(run.has_viewer?'Explore & replay':'Live construction'):(flow.running?'Separating objects':objectCount?`${objectCount} candidates`:'Object workspace')}</small></span><span class="stage-arrow">→</span></button>`).join('')}</nav>
    ${jobMessage && !h.running ? `<div class="studio-progress" role="status">${esc(jobMessage)}</div>` : ''}
    ${h.running?`<div class="studio-progress" role="status"><span>Building 3D splat</span><progress max="${h.iterations||100}" value="${h.step||0}"></progress><span>${fmt(h.step)} / ${fmt(h.iterations)} steps${h.eta_minutes!=null?` · ~${fmt(h.eta_minutes)} min remaining`:''}</span></div>`:''}
    <div class="studio-workspace"><section class="studio-canvas ${tab==='splat'?'is-splat':''}" aria-label="${esc(tab)} workspace">${tab==='images'?imagePane:tab==='splat'?splatPane:objectsPane}</section><aside class="studio-inspector"><p class="page-kicker">Capture record</p><h3>Preserved in detail.</h3><dl><div><dt>Capture type</dt><dd>${captureKind(run)}</dd></div><div><dt>Input images</dt><dd>${h.accepted_images!=null?fmt(h.accepted_images):'Not recorded'}</dd></div><div><dt>Registered views</dt><dd>${h.registered_images!=null?fmt(h.registered_images):'Not recorded'}</dd></div><div><dt>Trained splats</dt><dd>${h.n_gaussians!=null?fmt(h.n_gaussians):'Not recorded'}</dd></div><div><dt>Build time</dt><dd>${h.minutes!=null?`${fmt(h.minutes,1)} min`:'Not recorded'}</dd></div></dl><details class="inspector-section"><summary>Advanced · quality record</summary><div class="quality-pair"><div><strong>${quality.psnr!=null?fmt(quality.psnr,2):'—'}</strong><span>PSNR · dB</span></div><div><strong>${quality.ssim!=null?fmt(quality.ssim,3):'—'}</strong><span>SSIM · 0–1</span></div></div><p>${measured?'Evaluated saved PLY.':h.metric_source==='export'?'Recorded export metrics.':'Recorded training metrics.'} The interactive splat is a viewing derivative.</p><span class="record-status">${run.stages?.evaluate?.done?'Evaluation report available':'Separate evaluation report not recorded'}</span></details><div class="inspector-section"><h4>Preservation archive</h4><p>${run.stages?.package?.done?'Archive manifest available. Use checksum verification before deposit.':'Not packaged yet. Originals, poses and checksums belong in the preservation package.'}</p>${run.stages?.package?.done?`<a href="${fileLink(run,'archive/manifest.json')}" target="_blank" rel="noopener">View manifest ↗</a>`:''}</div><div class="inspector-section"><h4>Download files</h4>${run.artefacts?.scene_splat?`<a class="download-row" href="${fileLink(run,'model/scene.splat')}" download><span>Web splat</span><small>${fmtBytes(run.artefacts.scene_splat.bytes)} ↓</small></a>`:''}${run.artefacts?.scene_ply?`<a class="download-row" href="${fileLink(run,'model/scene.ply')}" download><span>Master PLY</span><small>${fmtBytes(run.artefacts.scene_ply.bytes)} ↓</small></a>`:''}</div></aside></div></div><dialog class="photo-dialog"><button class="ghost" aria-label="Close photograph">Close ×</button><img alt=""/><p></p></dialog>`;
  if(priorViewer && tab==='splat' && priorViewer.dataset.studioViewer===run.name){
    // Refresh metadata without detaching the iframe and losing the user's camera.
    const next=document.createElement('div');next.innerHTML=markup;
    for(const selector of ['.studio-heading','.studio-tabs','.studio-inspector']) {
      viewEl.querySelector(selector).replaceWith(next.querySelector(selector));
    }
    const previousBanner = viewEl.querySelector('.live-banner.interrupted');
    const nextBanner = next.querySelector('.live-banner.interrupted');
    if (previousBanner && nextBanner) previousBanner.replaceWith(nextBanner);
    else if (previousBanner) previousBanner.remove();
    else if (nextBanner) viewEl.querySelector('.studio-heading').after(nextBanner);
    viewEl.querySelector('.studio-progress')?.remove();
    const progress=next.querySelector('.studio-progress');
    if(progress)viewEl.querySelector('.studio-tabs').after(progress);
  } else viewEl.innerHTML=markup;
  viewEl.querySelector('#studio-back').onclick=()=>{state.selected=null;switchView(objectCapture?'objects':'runs');};
  viewEl.querySelector('#open-object-library')?.addEventListener('click', () => switchView('objects'));
  viewEl.querySelectorAll('[data-stage]').forEach(el=>el.onclick=()=>{state.studioTab=el.dataset.stage;renderStudioDetail(run,ctx);});
  viewEl.querySelector('#studio-rename').onclick=()=>manageRun(run,ctx,'rename');
  viewEl.querySelector('#studio-delete').onclick=()=>manageRun(run,ctx,'trash');
  viewEl.querySelector('#studio-present').onclick=()=>{state.presenting=!state.presenting;document.body.classList.toggle('presentation-mode',state.presenting);ctx.applySidebar?.();const b=viewEl.querySelector('#studio-present');b.textContent=state.presenting?'Exit presentation':'Present';b.setAttribute('aria-pressed',String(state.presenting));};
  const recover = async action => {
    const button = viewEl.querySelector(`#studio-${action}-run`);
    if (button) button.disabled = true;
    try {
      if (controlRun) await controlRun(run.name, action);
      if (openRun) await openRun(run.name);
    } catch (error) {
      const banner = viewEl.querySelector('.live-banner.interrupted');
      if (banner) banner.textContent = String(error.message || error);
      if (button) button.disabled = false;
    }
  };
  viewEl.querySelector('#studio-resume-run')?.addEventListener('click', () => recover('resume'));
  viewEl.querySelector('#studio-cancel-run')?.addEventListener('click', () => recover('cancel'));
  viewEl.querySelector('#studio-refresh-run')?.addEventListener('click', () => openRun?.(run.name));
  const meshButton=viewEl.querySelector('#btn-object-meshes');
  if(meshButton)meshButton.onclick=async()=>{
    meshButton.disabled=true;
    const feedback=viewEl.querySelector('#mesh-feedback');
    feedback.textContent='Starting surface reconstruction…';
    try {
      const response=await fetch(`/api/runs/${encodeURIComponent(run.name)}/object-meshes`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const result=await response.json();
      if(!response.ok)throw new Error(result.error||'Could not start meshing');
      feedback.textContent='Mesh generation started. Processing continues in the background.';
      await openRun?.(run.name);
    } catch(error) {feedback.textContent=error.message;meshButton.disabled=false;}
  };
  viewEl.querySelectorAll('[data-object-mesh]').forEach(button => {
    button.onclick = () => startObjectMesh?.(run.name, button.dataset.objectMesh);
  });
  const separate=viewEl.querySelector('#btn-separate-objects');if(separate)separate.onclick=()=>startObjectSeparation(run.name);
  const dialog=viewEl.querySelector('dialog');viewEl.querySelectorAll('[data-photo]').forEach(el=>el.onclick=()=>{dialog.querySelector('img').src=el.dataset.photo;dialog.querySelector('img').alt=el.dataset.caption;dialog.querySelector('p').textContent=el.dataset.caption;dialog.showModal();});dialog.querySelector('button').onclick=()=>dialog.close();dialog.onclick=e=>{if(e.target===dialog)dialog.close();};
}
