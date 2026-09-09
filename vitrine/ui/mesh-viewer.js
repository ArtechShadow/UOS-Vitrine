import * as THREE from 'three';
import {OrbitControls} from './vendor/OrbitControls.js';
import {GLTFLoader} from './vendor/loaders/GLTFLoader.js';
import {RoomEnvironment} from './vendor/environments/RoomEnvironment.js';
const params=new URLSearchParams(location.search),run=params.get('run'),asset=params.get('asset');
const status=document.getElementById('status');
let renderer,controls,model,camera;
try {
  if(!run || !asset || asset.includes('..') || asset.includes('\\') || asset.startsWith('/') || !asset.endsWith('.glb')) throw Error('Choose a local capture GLB.');
  const url=`/files/${encodeURIComponent(run)}/${asset.split('/').map(encodeURIComponent).join('/')}`;
  document.getElementById('title').textContent=params.get('label') || asset.split('/').at(-1);
  document.getElementById('download').href=url;
  renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));
  renderer.setClearColor(0x252a2d);renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1;
  document.getElementById('surface').append(renderer.domElement);
  const scene=new THREE.Scene();
  const pmrem=new THREE.PMREMGenerator(renderer),room=new RoomEnvironment();
  const environment=pmrem.fromScene(room,.04);scene.environment=environment.texture;room.dispose();pmrem.dispose();
  camera=new THREE.PerspectiveCamera(42,1,.001,10000);
  controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;
  const gltf=await new GLTFLoader().loadAsync(url,event=>{if(event.total)status.textContent=`Loading GLB · ${Math.round(event.loaded/event.total*100)}%`;});
  model=gltf.scene;scene.add(model);
  const bounds=new THREE.Box3().setFromObject(model),center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());
  const radius=Math.max(size.length()/2,.001);
  let recordedPosition,recordedTarget;
  model.traverse(node=>{
    const position=node.userData.inspection_camera_position_gltf,target=node.userData.inspection_camera_target_gltf;
    if([position,target].every(value=>Array.isArray(value)&&value.length===3&&value.every(Number.isFinite))){recordedPosition=position;recordedTarget=target;}
  });
  const reset=()=>{camera.position.copy(center).add(new THREE.Vector3(1.6,.9,2).multiplyScalar(radius));controls.target.copy(center);if(recordedPosition){camera.position.fromArray(recordedPosition);controls.target.fromArray(recordedTarget);}camera.near=radius/1000;camera.far=radius*100;camera.updateProjectionMatrix();controls.update();};reset();
  let triangles=0,materials=new Set(),maps=0;
  model.traverse(node=>{if(node.isMesh){triangles+=(node.geometry.index?.count || node.geometry.attributes.position.count)/3;for(const material of Array.isArray(node.material)?node.material:[node.material])materials.add(material);}});
  for(const material of materials)maps+=['map','normalMap','roughnessMap','metalnessMap','aoMap'].filter(k=>material[k]).length;
  status.textContent=`${Math.round(triangles).toLocaleString()} triangles · ${materials.size} materials · ${maps} texture channels loaded`;
  document.getElementById('reset').onclick=reset;
  document.getElementById('wire').onclick=event=>{const value=event.target.getAttribute('aria-pressed')!=='true';event.target.setAttribute('aria-pressed',value);for(const material of materials)material.wireframe=value;};
  document.getElementById('rotate').onclick=event=>{controls.autoRotate=!controls.autoRotate;event.target.setAttribute('aria-pressed',controls.autoRotate);};
  const resize=()=>{renderer.setSize(innerWidth,innerHeight);camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();};resize();window.addEventListener('resize',resize);
  renderer.setAnimationLoop(()=>{if(!document.hidden){controls.update();renderer.render(scene,camera);}});
  window.addEventListener('pagehide',()=>{renderer.setAnimationLoop(null);controls.dispose();environment.dispose();renderer.dispose();});
} catch(error){status.textContent=`Surface could not be opened: ${error.message}`;}
