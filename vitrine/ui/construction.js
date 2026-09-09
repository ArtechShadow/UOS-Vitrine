import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {Viewer, SceneFormat} from '@mkkellogg/gaussian-splats-3d';
import {paintEvidence} from './construction-progress.js';

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
if (params.has('embedded')) document.body.classList.add('construction-embedded');
const stages = [['preflight','Check readiness'],['ingest','Prepare images'],['sfm','Find camera positions'],['train','Build splat'],['evaluate','Check quality'],['export','Prepare viewer'],['package','Package'],['viewer','Viewer']];
document.querySelector('.build-stages').innerHTML = stages.map(([id,title],i)=>`<li data-stage="${id}"><button type="button"><b>0${i+1}</b>${title}</button></li>`).join('');
let inspectedStage = null;
document.querySelectorAll('.build-stages [data-stage]').forEach(item => item.querySelector('button').onclick = () => {
  stopReplay(); following = false;
  inspectedStage = item.dataset.stage;
  compare = false; $('comparison').hidden = true;
  if (data && ['sfm','train'].includes(inspectedStage)) {
    stopReplay(); following = false;
    selectedId = (data.snapshots || []).filter(s => inspectedStage === 'sfm' ? s.kind === 'sparse' : s.kind === 'splat').at(-1)?.id;
  }
  paint();
});
const number = value => Number.isFinite(value) ? value.toLocaleString(undefined,{maximumFractionDigits:0}) : '—';
let run = params.get('run'), experiment = params.get('experiment'), following = true, selectedId = null;
let data = null, loadedId = null, loading = false, playback = null, generation = 0, stopped = false;
let renderer, camera, controls, scene, splats, cloud, frustums, bounds, fitted = false, splatCount = 0;
let renderFailed = false, compare = false;
let captureUp = null, captureBack = null;
let completedViewerUrl = null;
let sequentialProgress = null;
let connectionInterrupted = false;
let userNavigated = false;
let captureViews = [], selectedCapture = 0;
const surfaceSection=document.createElement('section');
surfaceSection.hidden=true;document.querySelector('.build-info').append(surfaceSection);
const shownSnapshots = () => (data?.snapshots || []).filter(s => inspectedStage === 'sfm' ? s.kind === 'sparse' : inspectedStage === 'train' ? s.kind === 'splat' || s.kind === 'render' : true);

function initRenderer() {
  if (renderer || renderFailed) return;
  try {
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(50,1,.001,100000);
    camera.up.set(0,-1,0); camera.position.set(0,-1,3);
    renderer = new THREE.WebGLRenderer({antialias:true});
    renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
    $('geometry').append(renderer.domElement);
    controls = new OrbitControls(camera,renderer.domElement);
    controls.enableDamping = true;
    controls.addEventListener('start',()=>{userNavigated=true;});
    const resize = () => {
      const {width,height} = $('geometry').getBoundingClientRect();
      renderer.setSize(width,height); camera.aspect=width/Math.max(1,height);camera.updateProjectionMatrix();
    };
    new ResizeObserver(resize).observe($('geometry'));resize();
    const theme = () => {renderer.setClearColor(getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim());};
    theme();window.addEventListener('themechange',theme);
    renderer.setAnimationLoop(()=>{
      if (document.hidden || stopped || inspectedStage === 'viewer') return;
      controls.update();
      if (splats && splatCount) {splats.update();splats.render();}
      else renderer.render(scene,camera);
    });
    renderer.domElement.addEventListener('webglcontextlost',event=>{
      event.preventDefault();renderFailed=true;
      $('notice').textContent='3D rendering was interrupted. Recorded evaluation images remain available; reload to restore 3D.';
      showComparison(true);
    });
  } catch (error) {
    renderFailed=true;
    $('notice').textContent='Interactive rendering is unavailable. Evaluation images will appear when recorded.';
  }
}
function disposeObject(object) {
  if (!object) return;
  scene.remove(object);
  object.traverse(child=>{child.geometry?.dispose();child.material?.dispose();});
}
function fit(points) {
  if (!points.length) return;
  // A few poorly triangulated distant points must not shrink the room to a dot.
  // This changes framing only: all recorded geometry stays in the scene.
  const axes=[[],[],[]];
  for (let i=0;i<points.length;i+=3) for(let axis=0;axis<3;axis++) axes[axis].push(points[i+axis]);
  axes.forEach(values=>values.sort((a,b)=>a-b));
  const lo=axes.map(values=>values[Math.floor((values.length-1)*.01)]);
  const hi=axes.map(values=>values[Math.ceil((values.length-1)*.99)]);
  const box = new THREE.Box3(new THREE.Vector3(...lo),new THREE.Vector3(...hi));
  bounds=box.getBoundingSphere(new THREE.Sphere());
  if (!fitted || (following && !userNavigated)) reset();
}
function reset() {
  if (!bounds || !camera) return;
  userNavigated=false;
  const radius=Math.max(bounds.radius,.001);
  const recorded = captureViews[selectedCapture];
  const matrix = recorded?.camera_to_world;
  const up = matrix ? new THREE.Vector3(-matrix[0][1],-matrix[1][1],-matrix[2][1]).normalize() : captureUp || new THREE.Vector3(0,-1,0);
  const back = captureBack || new THREE.Vector3(0,0,1);
  // OrbitControls caches its up-axis at construction time.
  if (camera.up.distanceToSquared(up) > 1e-8) {
    controls.dispose(); camera.up.copy(up);
    controls = new OrbitControls(camera,renderer.domElement);
    controls.enableDamping = true;
    controls.addEventListener('start',()=>{userNavigated=true;});
    controls.target.copy(bounds.center);
  }
  if (recorded) {
    camera.position.set(matrix[0][3],matrix[1][3],matrix[2][3]);
    controls.target.copy(camera.position).addScaledVector(new THREE.Vector3(matrix[0][2],matrix[1][2],matrix[2][2]),radius*.25);
    camera.fov=THREE.MathUtils.radToDeg(2*Math.atan(recorded.height/(2*recorded.intrinsics[1][1])));
  } else {
    controls.target.copy(bounds.center);
    camera.position.copy(bounds.center).addScaledVector(back,2.7*radius).addScaledVector(up,.3*radius);
    camera.fov=50;
  }
  camera.near=radius/10000;camera.far=radius*100;camera.updateProjectionMatrix();controls.update();fitted=true;
}
function makeSparse(payload) {
  if (payload.cameras.length > captureViews.length) {
    captureViews = [...payload.cameras].sort((a,b)=>a.name.localeCompare(b.name));
    const selector=$('capture-view');
    const indices=[...new Set(Array.from({length:12},(_,i)=>Math.round(i*(captureViews.length-1)/11)))];
    selector.replaceChildren(...[-1,...indices].map(index=>{
      const option=document.createElement('option');option.value=index;
      option.textContent=index<0?'Outside overview':`Captured view ${index+1} · ${captureViews[index].name.split('/').at(-1)}`;
      return option;
    }));
    selector.hidden=false;selector.value=String(selectedCapture);
  }
  // COLMAP camera coordinates are right/down/forward. Negative column 1
  // therefore estimates capture-up; positions alone cannot establish gravity.
  // Freeze the first usable orientation so live snapshots never roll the view.
  if (!captureUp && payload.cameras.length >= 3) {
    const up = new THREE.Vector3();
    for (const view of payload.cameras) {
      const m = view.camera_to_world;
      up.add(new THREE.Vector3(-m[0][1],-m[1][1],-m[2][1]).normalize());
    }
    if (up.length() / payload.cameras.length > .5) {
      captureUp = up.normalize();
      const m = payload.cameras[0].camera_to_world;
      captureBack = new THREE.Vector3(-m[0][2],-m[1][2],-m[2][2]);
      captureBack.addScaledVector(captureUp,-captureBack.dot(captureUp));
      if (captureBack.lengthSq() < 1e-6) captureBack.set(1,0,0).addScaledVector(captureUp,-captureUp.x);
      if (captureBack.lengthSq() < 1e-6) captureBack.set(0,0,1);
      captureBack.normalize();
      fitted = false;
    }
  }
  const group = new THREE.Group();
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(payload.positions,3));
  geometry.setAttribute('color',new THREE.Float32BufferAttribute(payload.colors,3));
  const points = new THREE.Points(geometry,new THREE.PointsMaterial({size:2,sizeAttenuation:false,vertexColors:true}));
  group.add(points);fit(payload.positions);
  const cameraGroup = new THREE.Group();
  const depth = Math.max(bounds?.radius || 1,.001)*.025;
  for (const view of payload.cameras) {
    const k=view.intrinsics, w=view.width,h=view.height;
    const matrix=new THREE.Matrix4().set(...view.camera_to_world.flat());
    const centre=new THREE.Vector3(0,0,0).applyMatrix4(matrix);
    const corners=[[0,0],[w,0],[w,h],[0,h]].map(([x,y])=>new THREE.Vector3((x-k[0][2])/k[0][0]*depth,(y-k[1][2])/k[1][1]*depth,depth).applyMatrix4(matrix));
    const lines=[];
    corners.forEach((v,i)=>{lines.push(centre,v,v,corners[(i+1)%4]);});
    const geo=new THREE.BufferGeometry().setFromPoints(lines);
    cameraGroup.add(new THREE.LineSegments(geo,new THREE.LineBasicMaterial({color:0xf18a46})));
  }
  cameraGroup.visible=$('cameras').getAttribute('aria-pressed')==='true';
  return {group,cameraGroup};
}
async function fetchJSON(url) {
  const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(15000)});
  if (!response.ok) throw Error(`Request failed (${response.status})`);
  return response.json();
}
async function loadSnapshot(shot) {
  if (!shot || shot.id===loadedId || loading) return;
  if(shot.kind==='render') {
    showComparison(true);loadedId=shot.id;$('build-empty').hidden=true;
    $('preview-label').textContent='Recorded evaluation render';return;
  }
  if(renderFailed)return;
  loading=true;
  const ticket=generation;
  try {
    initRenderer();
    if (renderFailed) {showComparison(true);return;}
    if (shot.kind==='sparse') {
      const payload=await fetchJSON(shot.url);
      if (ticket!==generation) return;
      const next=makeSparse(payload);
      if (splats && splatCount) {await splats.removeSplatScene(0,false);splatCount=0;}
      disposeObject(cloud);disposeObject(frustums);
      cloud=next.group;frustums=next.cameraGroup;scene.add(cloud,frustums);
    } else {
      if (!frustums) {
        const sparse = data.snapshots.filter(s=>s.kind==='sparse' && s.created<=shot.created).at(-1);
        if (sparse) {
          const payload=await fetchJSON(sparse.url);
          const overlay=makeSparse(payload);
          disposeObject(overlay.group);frustums=overlay.cameraGroup;scene.add(frustums);
        }
      }
      const response=await fetch(shot.url,{signal:AbortSignal.timeout(20000)});
      if (!response.ok) throw Error('Snapshot is no longer available');
      const buffer=await response.arrayBuffer();
      if (ticket!==generation) return;
      if (!buffer.byteLength || buffer.byteLength%32) throw Error('Invalid splat preview');
      if (!fitted) {
        const view=new DataView(buffer), positions=[];
        for(let i=0;i<buffer.byteLength;i+=32) positions.push(view.getFloat32(i,true),view.getFloat32(i+4,true),view.getFloat32(i+8,true));
        fit(positions);
      }
      if (!splats) splats=new Viewer({rootElement:$('geometry'),renderer,camera,threeScene:scene,
        selfDrivenMode:false,useBuiltInControls:false,sharedMemoryForWorkers:false,
        gpuAcceleratedSort:false,integerBasedSort:false,sphericalHarmonicsDegree:0});
      const url=URL.createObjectURL(new Blob([buffer]));
      try {await splats.addSplatScene(url,{format:SceneFormat.Splat,showLoadingUI:false,progressiveLoad:false});}
      finally {URL.revokeObjectURL(url);}
      splatCount++;
      if (splatCount>1) {await splats.removeSplatScene(0,false);splatCount--;}
      disposeObject(cloud);cloud=null;
    }
    if (ticket!==generation) return;
    loadedId=shot.id;$('build-empty').hidden=true;
    $('preview-label').textContent=shot.kind==='sparse'?'Registered cameras · sparse geometry':'Reduced-detail splat · up to 250,000 Gaussians';
    $('notice').textContent='';
  } catch (error) {
    $('notice').textContent='Preview could not be loaded. Keeping the previous view and retrying. '+error.message;
    if (!loadedId) showComparison(true);
  } finally {loading=false;}
}
function showComparison(value) {
  if (!data?.images?.length) return;
  compare=value;
  const current=(data.snapshots||[]).find(s=>s.id===selectedId);
  const candidates=data.images.filter(i=>!current || !Number.isFinite(current.step) || i.step<=current.step);
  const image=candidates.at(-1);
  if (!image) {compare=false;$('comparison').hidden=true;return;}
  $('comparison').hidden=!compare;$('compare').setAttribute('aria-pressed',String(compare));
  $('comparison').style.gridTemplateColumns=data.source_url?'1fr 1fr':'1fr';
  $('render-image').src=image.url;
  if(data.source_url)$('source-image').src=data.source_url;
  else $('source-image').removeAttribute('src');
  $('source-image').parentElement.hidden=!data.source_url;
  $('render-caption').textContent=`Recorded render · step ${number(image.step)} · ${image.camera}`;
}
function paint() {
  const controls = document.getElementById('pipeline-controls');
  if (controls && data) {
    document.getElementById('cancel-pipeline').hidden = data.state !== 'running' || !data.pipeline;
    document.getElementById('resume-pipeline').hidden = !data.pipeline || !['failed','cancelled','unknown'].includes(data.state);
    document.getElementById('cancel-pipeline').disabled = !!data.cancel_requested;
  }
  if (!data) return;
  const displayedStage = inspectedStage || data.stage;
  surfaceSection.hidden=!data.postprocessing?.length && !data.surface_assets?.length;
  surfaceSection.replaceChildren();
  if(!surfaceSection.hidden){
    const title=document.createElement('h3');title.textContent='Objects and surfaces';surfaceSection.append(title);
    for(const item of data.postprocessing || []){
      const line=document.createElement('p');line.textContent=`${item.message || item.stage} · ${item.state}`;surfaceSection.append(line);
    }
    for(const asset of data.surface_assets || []){
      const link=document.createElement('a');link.textContent=`Inspect ${asset.label} ↗`;
      link.href=`/static/mesh-viewer.html?run=${encodeURIComponent(run)}&asset=${encodeURIComponent(asset.path)}&label=${encodeURIComponent(asset.label)}`;
      link.target='_blank';link.rel='noopener';link.style.display='block';link.style.marginBottom='12px';surfaceSection.append(link);
    }
  }
  paintEvidence(data, displayedStage);
  document.querySelectorAll('.build-stages [data-stage] button').forEach(button => button.setAttribute('aria-pressed', String(button.parentElement.dataset.stage === displayedStage)));
  const shots=shownSnapshots();
  if (following) selectedId=shots.at(-1)?.id;
  let index=shots.findIndex(s=>s.id===selectedId);
  if(index<0 && shots.length){index=0;selectedId=shots[0].id;}
  const shot=shots[index];
  $('geometry').style.visibility = shot ? 'visible' : 'hidden';
  $('build-empty').hidden = !!shot;
  document.querySelectorAll('[data-stage]').forEach(el=>{
    const active=el.dataset.stage===data.stage && data.state==='running';
    el.classList.toggle('active',active);el.classList.toggle('done',el.dataset.stage==='viewer' ? !!data.final_url : !!data.done?.[el.dataset.stage]);
    if(active)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');
  });
  $('stage-title').textContent=stages.find(s=>s[0]===data.stage)?.[1] || 'Construction';
  $('state-label').textContent=({running:'Processing locally',complete:'Processing finished',failed:'Needs attention',unknown:'Status unknown · connection may be lost'})[data.state] || 'Local workspace';
  const descriptions={ingest:'Preparing photographs and video frames for reconstruction.',sfm:'Finding overlapping views and recovering camera positions.',train:'Refining the splat from the registered photographs.',evaluate:'Measuring how the reconstruction matches photographed views.',package:'Collecting the model and preservation records.'};
  const substeps = {feature_extractor:'Detecting distinctive image details for camera matching.',sequential_matcher:'Matching overlapping video frames.',exhaustive_matcher:'Comparing image pairs to find shared details.',mapper:'Recovering camera positions and triangulating the room. Camera markers and points appear as COLMAP publishes them.',model_converter:'Saving calibrated cameras and points in the archive format.'};
  $('stage-copy').textContent=['failed','cancelled'].includes(data.state) ? (data.error || 'Processing stopped. Completed stages are retained; resume when ready.') : data.historical?'This capture predates recorded construction previews. Its completed model is available below.':substeps[data.substage] || descriptions[data.stage] || ({preflight:'Checking inputs and this computer before reconstruction.',export:'Preparing the interactive viewing file.',cleanup:'Saving a separate cleanup candidate.'})[data.stage];
  $('build-progress').hidden=data.state!=='running';
  const count=data.stage==='train'?data.step:data.count;
  if(Number.isFinite(data.total)&&Number.isFinite(count)&&data.total>0){$('build-progress').max=data.total;$('build-progress').value=count;}else $('build-progress').removeAttribute('value');
  const counts=[];
  if(data.pipeline?.created) counts.push(['Elapsed',number(Math.max(0,((data.pipeline.pid?Date.now()/1000:data.pipeline.updated)-data.pipeline.created)/60))+' min']);
  if(Number.isFinite(count)) counts.push([data.stage==='train'?'Training step':data.unit||'Processed',number(count)+(data.total?' / '+number(data.total):'')]);
  if(Number.isFinite(data.registered))counts.push(['Registered views',number(data.registered)]);
  if(Number.isFinite(data.points))counts.push(['Sparse points',number(data.points)]);
  if(Number.isFinite(data.n_gaussians))counts.push(['Training Gaussians',number(data.n_gaussians)]);
  if(Number.isFinite(data.eta_seconds)&&data.state==='running'&&data.stage==='train')counts.push(['Estimated remaining',number(Math.ceil(data.eta_seconds/60))+' min']);
  $('build-counts').replaceChildren(...counts.flatMap(([key,value])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=value;return[dt,dd];}));
  $('heartbeat').textContent=data.heartbeat?'Last activity '+new Date(data.heartbeat*1000).toLocaleTimeString():'';
  $('technical').textContent=JSON.stringify({substage:data.substage,message:data.message,training:data.training,preview_error:data.preview_error},null,2);
  $('log-link').href=`/api/runs/${encodeURIComponent(run)}/log?which=${data.stage==='sfm'?'sfm':'train'}`;
  $('timeline').max=Math.max(0,shots.length-1);$('timeline').value=Math.max(0,index);$('timeline').disabled=!shots.length;
  $('timeline-count').textContent=shots.length+' snapshots';$('replay').disabled=shots.length<2;
  $('follow').setAttribute('aria-pressed',String(following));$('timeline-mode').textContent=inspectedStage?'Stage review':following?'Live':playback?'Replay':'Recorded';
  $('compare').disabled=!data.images?.length;
  $('caption').textContent=shot?`${shot.kind!=='sparse'?'Training step '+number(shot.step):number(shot.registered)+' registered views'} · ${new Date((shot.captured||shot.created)*1000).toLocaleTimeString()} · recorded snapshot`: 'No geometry snapshots recorded yet.';
  $('final-viewer').hidden=!data.final_url;if(data.final_url)$('final-viewer').href=data.final_url;
  if (displayedStage === 'viewer') {
    $('stage-title').textContent = 'Splat viewer';
    $('stage-copy').textContent = data.final_url ? 'Explore the completed browser splat. Download the full-SH PLY master from the viewer for maximum fidelity.' : 'The interactive viewer becomes available when the browser splat is exported.';
    $('caption').textContent = data.final_url ? 'Completed browser output · not a reduced-count construction snapshot' : 'Waiting for the completed browser splat';
  }
  if(!shot){
    $('empty-title').textContent=displayedStage==='sfm'?'Finding how the photographs connect.':data.state==='running'?'Your space is being reconstructed.':data.historical?'Your finished space is ready.':'A space, taking shape.';
    $('empty-copy').textContent=displayedStage==='sfm' ? (substeps[data.substage] || 'Recorded camera positions and sparse points appear here when available.') : 'Recorded splat previews will appear as training progresses.';
  }
  if(compare||renderFailed)showComparison(true);
  loadSnapshot(shot);
}
function stopReplay(){clearInterval(playback);playback=null;$('replay').textContent='Replay';}
$('follow').onclick=()=>{stopReplay();following=true;inspectedStage=null;paint();};
const pipelineControls = document.createElement('div');
pipelineControls.id = 'pipeline-controls'; pipelineControls.className = 'build-toolbar';
for (const [action,label] of [['cancel','Cancel build'],['resume','Resume build'],['open-folder','Open output folder']]) {
  const button = document.createElement('button'); button.textContent=label; button.id=action+'-pipeline';
  button.hidden=action!=='open-folder';
  button.onclick=async()=>{
    button.disabled=true;
    try {
      const response=await fetch(`/api/runs/${encodeURIComponent(run)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const result=await response.json(); if(!response.ok) throw new Error(result.error);
      $('notice').textContent=result.message || (action==='resume'?'Resuming verified stages…':'');
    } catch(error) {$('notice').textContent=error.message;} finally {button.disabled=false;}
  };
  pipelineControls.append(button);
}
document.querySelector('.build-info').append(pipelineControls);
$('timeline').oninput=()=>{stopReplay();following=false;selectedId=shownSnapshots()[Number($('timeline').value)]?.id;paint();};
$('replay').onclick=()=>{
  if(playback){stopReplay();paint();return;}
  following=false;selectedId=shownSnapshots()[0]?.id;$('replay').textContent='Pause replay';paint();
  playback=setInterval(()=>{if(loading)return;const shots=shownSnapshots();const index=shots.findIndex(s=>s.id===selectedId);if(index>=shots.length-1){stopReplay();paint();return;}selectedId=shots[index+1].id;paint();},1500);
};
$('reset').onclick=reset;
$('capture-view').onchange=()=>{selectedCapture=Number($('capture-view').value);showComparison(false);reset();};
$('cameras').onclick=()=>{const visible=$('cameras').getAttribute('aria-pressed')!=='true';$('cameras').setAttribute('aria-pressed',String(visible));if(frustums)frustums.visible=visible;};
$('compare').onclick=()=>showComparison(!compare);
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('build-screen').requestFullscreen();}catch{$('notice').textContent='Fullscreen is unavailable in this browser.';}};
$('build-job').onchange=()=>{const value=$('build-job').value.split('/');const query=new URLSearchParams({run:value[0]});if(value[1]==='experiments')query.set('experiment',value[2]);location.search=query.toString();};
async function poll(){
  if(stopped)return;
  try {
    if(!params.has('embedded')){
      const listing=await fetchJSON('/api/construction');
      const jobs=listing.jobs||[];
      $('build-job').replaceChildren(...jobs.map(job=>{const option=document.createElement('option');option.value=job.id;option.textContent=job.run+' · '+job.label;return option;}));
      if(!jobs.length){const option=document.createElement('option');option.textContent='No builds yet';option.value='';$('build-job').append(option);}
      $('build-job').disabled=!jobs.length;
      if(!run&&jobs.length){run=jobs[0].run;experiment=jobs[0].id.split('/')[1]==='experiments'?jobs[0].label:null;}
      $('build-job').value=run?(experiment?`${run}/experiments/${experiment}`:`${run}/model`):'';
    }
    if(run){
      data=await fetchJSON(`/api/runs/${encodeURIComponent(run)}/construction`+(experiment?'?experiment='+encodeURIComponent(experiment):''));
      if (connectionInterrupted) {
        $('notice').textContent = '';
        connectionInterrupted = false;
      }
      if (data.substage === 'sequential_matcher') {
        const match = (data.message || '').match(/Processing image \[(\d+)\/(\d+)\]/);
        if (match) sequentialProgress = {count:Number(match[1]),total:Number(match[2])};
        if (sequentialProgress) Object.assign(data, sequentialProgress, {unit:'video frames',sequential_progress:sequentialProgress});
      } else sequentialProgress = null;
      if (experiment && data.state !== 'running' && !data.final_url) {
        const scenePath = `experiments/${experiment}/model/scene.splat`;
        if (!completedViewerUrl) {
          // This local server routes GET, but its inherited HEAD handler does
          // not resolve /files. Cancel the body after checking real headers.
          const response = await fetch(`/files/${encodeURIComponent(run)}/${scenePath.split('/').map(encodeURIComponent).join('/')}`);
          if (response.ok) completedViewerUrl = `/viewer/${encodeURIComponent(run)}?scene=${encodeURIComponent(scenePath)}&label=${encodeURIComponent(experiment)}`;
          await response.body?.cancel();
        }
        data.final_url = completedViewerUrl;
      }
      // File reads also support dashboards started before visual reporting shipped.
      if (!experiment) {
        const root = `/files/${encodeURIComponent(run)}/`;
        const readOptional = async path => {try {const response=await fetch(root+path, {cache:'no-store'});return response.ok ? await response.json() : null;} catch {return null;}};
        if (!data.selection) {
          data.selection = await readOptional('ingest/selection.json');
          for (const record of data.selection?.records || []) if (/^[0-9a-f]{32}\.jpg$/.test(record.thumbnail || '')) record.url = root + 'ingest/selection-thumbnails/' + record.thumbnail;
        }
        if (!data.packaging && data.stage === 'package') data.packaging = await readOptional('construction-package.json');
        if (!('feature_preview' in data) && data.stage === 'sfm') data.feature_preview = await readOptional('sfm/features-preview.json');
        if (!data.evaluation && (data.stage === 'evaluate' || inspectedStage === 'evaluate')) {
          try {const detail = await fetchJSON(`/api/runs/${encodeURIComponent(run)}`); data.evaluation = detail.stages?.evaluate?.report;} catch {}
        }
        if (!data.evaluation_progress && (data.stage === 'evaluate' || inspectedStage === 'evaluate')) data.evaluation_progress = await readOptional('model/evaluation-progress.json');
        data.evaluation_preview_root = root + 'model/evaluation-previews/';
        data.manifest_url = root + 'archive/manifest.json';
      }
      paint();
    }
  }catch(error){connectionInterrupted=true;$('notice').textContent='Connection interrupted. Your last preview is retained. Reconnecting…';}
  setTimeout(poll,document.hidden?10000:2000);
}
window.addEventListener('pagehide',()=>{stopped=true;generation++;stopReplay();renderer?.setAnimationLoop(null);controls?.dispose();splats?.dispose();renderer?.dispose();});
poll();
