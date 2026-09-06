import * as THREE from 'three';
import { requestSpaceMice } from './vendor/spacemouse-webhid.module.js';

/** Local six-axis navigation; open only interfaces authorised by the user. */
export function attachSpaceMouse(camera, controls, frame, resetView) {
  const button=document.getElementById('spacemouse'), speed=document.getElementById('spacemouse-speed');
  const status=document.getElementById('spacemouse-status');
  const settings=document.createElement('details');
  settings.className='spacemouse-settings';settings.hidden=true;
  settings.innerHTML='<summary>SpaceMouse controls</summary><label><input type="checkbox" data-upright checked> Keep room upright</label><label><input type="checkbox" data-move> Reverse movement</label><label><input type="checkbox" data-turn> Reverse turning</label><label>Turn speed <input type="range" min="0.2" max="2" step="0.1" value="0.7" aria-label="SpaceMouse turn speed" data-sensitivity></label>';
  document.body.append(settings);
  const upright=settings.querySelector('[data-upright]'),reverseMove=settings.querySelector('[data-move]'),reverseTurn=settings.querySelector('[data-turn]'),sensitivity=settings.querySelector('[data-sensitivity]');
  const roomUp=camera.up.clone().normalize();
  button.disabled=false;
  let devices=[], connecting=false, lastFrame=performance.now(), lastMove=0, packets=0, resetPressed=false;
  const translation=new THREE.Vector3(), rotation=new THREE.Vector3();
  const axis=value=>Math.abs(value)<8?0:Math.max(-1,Math.min(1,value/350));
  const clear=()=>{translation.set(0,0,0);rotation.set(0,0,0);};
  const say=text=>{if(status.textContent!==text)status.textContent=text;status.hidden=false;};
  // Read every motion packet, including unchanged values after focus returns.
  // DataView honours the report's byte offset and length (receiver packets can be slices).
  function report(event) {
    const d=event.data;packets++;
    if(event.reportId===3 && d.byteLength) {
      const pressed=Boolean(d.getUint8(0)&1);
      if(pressed && !resetPressed){clear();resetView();}
      resetPressed=pressed;return;
    }
    if(d.byteLength<6 || ![1,2].includes(event.reportId))return;
    if(event.reportId===1) {
      translation.set(axis(d.getInt16(0,true)),-axis(d.getInt16(4,true)),axis(d.getInt16(2,true)));
      if(d.byteLength>=12)rotation.set(-axis(d.getInt16(6,true)),-axis(d.getInt16(10,true)),-axis(d.getInt16(8,true)));
    } else rotation.set(-axis(d.getInt16(0,true)),-axis(d.getInt16(4,true)),-axis(d.getInt16(2,true)));
    lastMove=performance.now();
  }
  async function disconnect() {
    clear();const previous=devices;devices=[];
    await Promise.all(previous.map(async d=>{d.removeEventListener('inputreport',report);if(d.opened)await d.close().catch(()=>{});}));
    button.textContent='Connect SpaceMouse';button.setAttribute('aria-pressed','false');speed.hidden=true;settings.hidden=true;
  }
  button.onclick=async()=>{
    if(connecting)return;
    if(devices.length){await disconnect();say('SpaceMouse disconnected.');return;}
    if(!navigator.hid){say('Open this viewer in Chrome or Edge to connect a SpaceMouse.');return;}
    connecting=true;
    try {
      const selected=await requestSpaceMice();
      if(!selected.length){say('No device selected. Connect again to choose your SpaceMouse.');return;}
      const granted=await navigator.hid.getDevices();
      const matching=[...new Set([...selected,...granted.filter(d=>selected.some(s=>s.vendorId===d.vendorId && s.productId===d.productId))])];
      // A Universal Receiver exposes several HID interfaces. Do not assume the first carries motion.
      for(const device of matching) {
        try {if(!device.opened)await device.open();device.addEventListener('inputreport',report);devices.push(device);}catch{}
      }
      if(!devices.length)throw Error('No readable interface');
      packets=0;lastMove=0;
      button.textContent='Disconnect SpaceMouse';button.setAttribute('aria-pressed','true');speed.hidden=false;settings.hidden=false;
      say('Connected · waiting for cap movement.');
    } catch(error) {await disconnect();say('Could not open the SpaceMouse. Wake it up and reconnect in Chrome or Edge.');}
    finally {connecting=false;}
  };
  const onDisconnect=event=>{if(devices.includes(event.device)){disconnect();say('SpaceMouse disconnected. Reconnect to continue.');}};
  navigator.hid?.addEventListener('disconnect',onDisconnect);
  const onBlur=()=>clear();
  window.addEventListener('blur',onBlur);document.addEventListener('visibilitychange',onBlur);
  function tick(now) {
    const dt=Math.min((now-lastFrame)/1000,.05);lastFrame=now;
    const moving=devices.length && !document.hidden && now-lastMove<300 && (translation.lengthSq() || rotation.lengthSq());
    if(moving && controls) {
      const distance=Math.max(camera.position.distanceTo(controls.target),frame.radius*.01);
      camera.position.add(translation.clone().multiplyScalar(frame.radius*.12*Number(speed.value)*dt*(reverseMove.checked?-1:1)).applyQuaternion(camera.quaternion));
      const turn=dt*Number(sensitivity.value)*(reverseTurn.checked?-1:1);
      if(upright.checked) {
        // Yaw about the room's original up axis; suppress cap roll for interior exploration.
        camera.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(roomUp,rotation.y*turn));
        const pitched=camera.quaternion.clone().multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),rotation.x*turn));
        if(Math.abs(new THREE.Vector3(0,0,-1).applyQuaternion(pitched).dot(roomUp))<.985)camera.quaternion.copy(pitched);
        camera.up.copy(roomUp);
      } else {
        camera.quaternion.multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(rotation.x*turn,rotation.y*turn,rotation.z*turn,'YXZ'))).normalize();
        camera.up.set(0,1,0).applyQuaternion(camera.quaternion);
      }
      controls.target.copy(camera.position).add(new THREE.Vector3(0,0,-distance).applyQuaternion(camera.quaternion));
      controls.update();
    }
    if(devices.length)say(moving?'Receiving cap movement · navigating':lastMove?'Connected · cap at rest':packets?'Connected · receiver responding, waiting for cap movement.':'Connected · waiting for cap movement.');
    animation=requestAnimationFrame(tick);
  }
  let animation=requestAnimationFrame(tick);
  window.addEventListener('pagehide',()=>{cancelAnimationFrame(animation);navigator.hid?.removeEventListener('disconnect',onDisconnect);disconnect();},{once:true});
}
