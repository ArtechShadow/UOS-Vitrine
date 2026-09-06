const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function request(url, body) {
  const response=await fetch(url, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw Error(result.error || 'The request could not be completed.');
  return result;
}
function dialog(content) {
  const el=document.createElement('dialog');el.className='manage-dialog';el.innerHTML=content;
  document.body.append(el);el.addEventListener('close',()=>el.remove(),{once:true});
  el.querySelector('[data-cancel]').onclick=()=>el.close();el.showModal();return el;
}
export function manageRun(run,ctx,action) {
  const rename=action==='rename', name=run.title || ctx.displayName(run.name);
  const el=dialog(`<form><h3>${rename?'Rename capture':'Delete capture'}</h3>${rename?`<label>Capture name<input name="title" required maxlength="160" value="${escape(name)}"/></label><p>Changes the library name. The preservation record keeps its original title.</p>`:`<p>Move <strong>${escape(name)}</strong> to Trash?</p><p class="muted">Files: ${escape(run.path)}<br/>You can restore this capture from Trash.</p>`}<p role="alert"></p><div class="manage-actions"><button type="button" class="soft" data-cancel>Cancel</button><button class="primary" type="submit">${rename?'Save name':'Move to Trash'}</button></div></form>`);
  el.querySelector('form').onsubmit=async event=>{
    event.preventDefault();const submit=el.querySelector('[type=submit]');submit.disabled=true;
    try {
      await request(`/api/runs/${encodeURIComponent(run.name)}/${rename?'rename':'trash'}`,rename?{title:new FormData(event.target).get('title')}:{confirm_name:run.name});
      el.close();
      if(rename) {await ctx.loadRuns(false);await ctx.openRun(run.name);}
      else {ctx.state.selected=null;ctx.switchView('runs');await ctx.loadRuns(true);}
    } catch(error) {el.querySelector('[role=alert]').textContent=error.message;submit.disabled=false;}
  };
}
export async function showTrash(ctx) {
  const el=dialog('<h3>Trash</h3><p>Removed captures stay here until you restore them.</p><div data-list>Loading…</div><p role="alert"></p><div class="manage-actions"><button class="soft" data-cancel>Close</button></div>');
  try {
    const data=await request('/api/trash');
    el.querySelector('[data-list]').innerHTML=data.runs.length?data.runs.map(r=>`<div class="trash-row"><span>${escape(r.title)}</span><button class="soft" data-restore="${escape(r.id)}">Restore</button></div>`).join(''):'Trash is empty.';
    el.querySelectorAll('[data-restore]').forEach(button=>button.onclick=async()=>{
      button.disabled=true;
      try {await request(`/api/trash/${encodeURIComponent(button.dataset.restore)}/restore`,{});button.parentElement.remove();await ctx.loadRuns(true);if(!el.querySelector('[data-restore]'))el.querySelector('[data-list]').textContent='Trash is empty.';}
      catch(error){el.querySelector('[role=alert]').textContent=error.message;button.disabled=false;}
    });
  } catch(error){el.querySelector('[role=alert]').textContent=error.message;el.querySelector('[data-list]').textContent='';}
}
