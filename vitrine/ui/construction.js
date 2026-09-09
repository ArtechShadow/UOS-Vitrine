import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {Viewer, SceneFormat} from '@mkkellogg/gaussian-splats-3d';

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
if (params.has('embedded')) document.body.classList.add('construction-embedded');
const stages = [['ingest','Prepare images'],['sfm','Find camera positions'],['train','Build splat'],['evaluate','Evaluate'],['package','Package']];
document.querySelector('.build-stages').innerHTML = stages.map(([id,title],i)=>`<li data-stage="${id}"><b>0${i+1}</b>${title}</li>`).join('');
const number = value => Number.isFinite(value) ? value.toLocaleString(undefined,{maximumFractionDigits:0}) : '—';
let run = params.get('run'), experiment = params.get('experiment'), following = true, selectedId = null;
let data = null, loadedId = null, loading = false, playback = null, generation = 0, stopped = false;
let renderer, camera, controls, scene, splats, cloud, frustums, bounds, fitted = false, splatCount = 0;
let renderFailed = false, compare = false;

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
    const resize = () => {
      const {width,height} = $('geometry').getBoundingClientRect();
      renderer.setSize(width,height); camera.aspect=width/Math.max(1,height);camera.updateProjectionMatrix();
    };
    new ResizeObserver(resize).observe($('geometry'));resize();
    const theme = () => {renderer.setClearColor(getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim());};
    theme();window.addEventListener('themechange',theme);
    renderer.setAnimationLoop(()=>{
      if (document.hidden || stopped) return;
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
  const box = new THREE.Box3();
  for (let i=0;i<points.length;i+=3) box.expandByPoint(new THREE.Vector3(points[i],points[i+1],points[i+2]));
  bounds=box.getBoundingSphere(new THREE.Sphere());
  if (!fitted) reset();
}
function reset() {
  if (!bounds || !camera) return;
  const radius=Math.max(bounds.radius,.001);
  controls.target.copy(bounds.center);
  camera.position.copy(bounds.center).add(new THREE.Vector3(0,-.3*radius,2.7*radius));
  camera.near=radius/10000;camera.far=radius*100;camera.updateProjectionMatrix();controls.update();fitted=true;
}
function makeSparse(payload) {
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
  if (!data) return;
  const shots=data.snapshots || [];
  if (following) selectedId=shots.at(-1)?.id;
  let index=shots.findIndex(s=>s.id===selectedId);
  if(index<0 && shots.length){index=0;selectedId=shots[0].id;}
  const shot=shots[index];
  document.querySelectorAll('[data-stage]').forEach(el=>{
    const active=el.dataset.stage===data.stage && data.state==='running';
    el.classList.toggle('active',active);el.classList.toggle('done',!!data.done?.[el.dataset.stage]);
    if(active)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');
  });
  $('stage-title').textContent=stages.find(s=>s[0]===data.stage)?.[1] || 'Construction';
  $('state-label').textContent=({running:'Processing locally',complete:'Processing finished',failed:'Needs attention',unknown:'Status unknown · connection may be lost'})[data.state] || 'Local workspace';
  const descriptions={ingest:'Preparing photographs and video frames for reconstruction.',sfm:'Finding overlapping views and recovering camera positions.',train:'Refining the splat from the registered photographs.',evaluate:'Measuring how the reconstruction matches photographed views.',package:'Collecting the model and preservation records.'};
  $('stage-copy').textContent=data.state==='failed' ? (data.error || 'Processing stopped. Inspect the log before retrying.') : data.historical?'This capture predates recorded construction previews. Its completed model is available below.':descriptions[data.stage];
  $('build-progress').hidden=data.state!=='running';
  const count=data.stage==='train'?data.step:data.count;
  if(Number.isFinite(data.total)&&Number.isFinite(count)&&data.total>0){$('build-progress').max=data.total;$('build-progress').value=count;}else $('build-progress').removeAttribute('value');
  const counts=[];
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
  $('follow').setAttribute('aria-pressed',String(following));$('timeline-mode').textContent=following?'Live':playback?'Replay':'Recorded';
  $('compare').disabled=!data.images?.length;
  $('caption').textContent=shot?`${shot.kind!=='sparse'?'Training step '+number(shot.step):number(shot.registered)+' registered views'} · ${new Date((shot.captured||shot.created)*1000).toLocaleTimeString()} · recorded snapshot`: 'No geometry snapshots recorded yet.';
  $('final-viewer').hidden=!data.final_url;if(data.final_url)$('final-viewer').href=data.final_url;
  if(!loadedId){$('empty-title').textContent=data.state==='running'?'Your space is being reconstructed.':data.historical?'Your finished space is ready.':'A space, taking shape.';}
  if(compare||renderFailed)showComparison(true);
  loadSnapshot(shot);
}
function stopReplay(){clearInterval(playback);playback=null;$('replay').textContent='Replay';}
$('follow').onclick=()=>{stopReplay();following=true;paint();};
$('timeline').oninput=()=>{stopReplay();following=false;selectedId=data.snapshots[Number($('timeline').value)]?.id;paint();};
$('replay').onclick=()=>{
  if(playback){stopReplay();paint();return;}
  following=false;selectedId=data.snapshots[0]?.id;$('replay').textContent='Pause replay';paint();
  playback=setInterval(()=>{if(loading)return;const index=data.snapshots.findIndex(s=>s.id===selectedId);if(index>=data.snapshots.length-1){stopReplay();paint();return;}selectedId=data.snapshots[index+1].id;paint();},1500);
};
$('reset').onclick=reset;
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
    if(run){data=await fetchJSON(`/api/runs/${encodeURIComponent(run)}/construction`+(experiment?'?experiment='+encodeURIComponent(experiment):''));paint();}
  }catch(error){$('notice').textContent='Connection interrupted. Your last preview is retained. Reconnecting…';}
  setTimeout(poll,document.hidden?10000:2000);
}
window.addEventListener('pagehide',()=>{stopped=true;generation++;stopReplay();renderer?.setAnimationLoop(null);controls?.dispose();splats?.dispose();renderer?.dispose();});
poll();
