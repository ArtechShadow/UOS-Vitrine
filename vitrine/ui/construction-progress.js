// Visual evidence from the pipeline, never simulated progress.
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
let filter = 'all', page = 0, lastKey = '', latest, selectedStage;
let followFrames = true, lastFollowed = null;
const panel = el('section', null, 'process-evidence');
const viewerPanel = el('section', null, 'build-viewer-panel');
viewerPanel.hidden = true;
viewerPanel.setAttribute('aria-label', 'Completed splat viewer');
document.getElementById('build-screen').append(viewerPanel);
let viewerUrl = null;
let showFeaturePoints = true;
panel.hidden = true;
panel.setAttribute('aria-label', 'Processing evidence');
document.getElementById('build-screen').append(panel);
const header = el('header'), title = el('h2'), status = el('p');
status.setAttribute('role', 'status');
header.append(title, status);
const filters = el('div', null, 'evidence-filters');
const grid = el('div', null, 'evidence-grid');
const footer = el('footer'), previous = el('button', 'Previous'), next = el('button', 'Next'), pageLabel = el('span');
previous.type = next.type = 'button';
previous.onclick = () => {followFrames = false; page--; lastKey = ''; paintEvidence(latest, selectedStage);};
next.onclick = () => {followFrames = false; page++; lastKey = ''; paintEvidence(latest, selectedStage);};
footer.append(previous, pageLabel, next);
panel.append(header, filters, grid, footer);

function card(record, activeLabel = '') {
  const item = el('article', null, 'evidence-card ' + record.status);
  if (activeLabel) {
    item.classList.add('is-current');
    item.setAttribute('aria-current', 'step');
    item.append(el('div', activeLabel, 'evidence-current-label'));
  }
  if (record.url) {
    const image = el('img'); image.src = record.url; image.alt = record.file; image.loading = 'lazy';
    image.onerror = () => image.replaceWith(el('div', 'Preview unavailable', 'evidence-placeholder'));
    item.append(image);
  } else item.append(el('div', 'Preview unavailable', 'evidence-placeholder'));
  const text = el('div', null, 'evidence-card-copy');
  text.append(el('strong', ({kept:'✓ Kept', rejected:'− Rejected', pending:record.video ? 'Extracted · awaiting selection' : 'Checking image'})[record.status] || record.status));
  text.append(el('p', record.file), el('small', record.group), el('p', record.reason));
  item.append(text);
  return item;
}

export function paintEvidence(data, stage) {
  latest = data; selectedStage = stage;
  viewerPanel.hidden = stage !== 'viewer';
  if (stage === 'viewer') {
    panel.hidden = true;
    const url = data.final_url || '';
    if (viewerUrl !== url || !viewerPanel.childElementCount) {
      viewerUrl = url;
      viewerPanel.replaceChildren();
      if (url) {
        const frame = el('iframe');
        frame.title = 'Interactive completed splat';
        frame.src = url;
        frame.allowFullscreen = true;
        viewerPanel.append(frame);
      } else {
        const message = el('div', null, 'build-viewer-wait');
        message.append(el('h2', 'Your splat viewer will open here'),
          el('p', 'The full browser splat is not available yet. Follow the build, then return to explore the completed output.'));
        viewerPanel.append(message);
      }
    }
    return;
  }
  panel.hidden = !['ingest', 'evaluate', 'package'].includes(stage) && !(stage === 'sfm' && (data.substage === 'feature_extractor' || !(data.snapshots || []).some(s => s.kind === 'sparse')));
  panel.classList.toggle('feature-detection', stage === 'sfm' && data.substage === 'feature_extractor');
  if (panel.hidden) return;
  const selection = data.selection;
  if (stage === 'ingest') {
    const extracting = selection?.phase === 'extracting' && !selection.complete;
    title.textContent = selection?.observation ? 'Captured image preparation' : extracting ? 'Extracting video frames' : 'Image selection';
    status.textContent = selection
      ? `${selection.source_frames ?? selection.extracted ?? 0} frames extracted · ${selection.scored == null ? recordsReady(selection) : `${selection.scored} / ${selection.total} scored`} · ${selection.kept} kept · ${selection.rejected} rejected${selection.complete ? ' · Complete' : data.state === 'running' && data.stage === 'ingest' ? ' · In progress' : ' · Last recorded decisions'}`
      : 'Image previews appear when the next capture is prepared. Historical captures may not have this record.';
    filters.hidden = footer.hidden = !selection;
    const records = selection?.records || [];
    const live = data.state === 'running' && data.stage === 'ingest' && !selection?.complete;
    const current = !live ? null : extracting
      ? records.filter(record => record.video).at(-1)
      : records.filter(record => record.file === selection?.current).at(-1);
    const matching = records.filter(r => filter === 'all' || (filter === 'video' ? r.video : r.status === filter));
    const currentIndex = current ? matching.indexOf(current) : -1;
    if (followFrames && currentIndex >= 0) page = Math.floor(currentIndex / 48);
    const pages = Math.max(1, Math.ceil(matching.length / 48)); page = Math.min(page, pages - 1);
    const key = JSON.stringify([stage, live, selection?.phase, selection?.scored, current?.id, records.map(r => [r.id, r.status]), filter, page, followFrames]);
    if (key === lastKey) return;
    lastKey = key;
    filters.replaceChildren(...[['all','All images'],['video','Video frames'],['kept','Kept'],['rejected','Rejected']].map(([id,label]) => {
      const button = el('button', label); button.type = 'button'; button.setAttribute('aria-pressed', String(id === filter));
      button.onclick = () => {filter = id; page = 0; lastKey = ''; lastFollowed = null; paintEvidence(latest, selectedStage);};
      return button;
    }));
    const follow = el('button', followFrames ? 'Following frames' : 'Follow frames');
    follow.type = 'button'; follow.setAttribute('aria-pressed', String(followFrames));
    follow.onclick = () => {followFrames = !followFrames; lastFollowed = null; lastKey = ''; paintEvidence(latest, selectedStage);};
    filters.append(follow);
    grid.replaceChildren(...matching.slice(page * 48, (page + 1) * 48).map(record => card(record,
      record === current ? extracting ? 'Latest extracted frame' : 'Latest checked image' : '')));
    if (followFrames && currentIndex >= 0 && current.id !== lastFollowed) {
      lastFollowed = current.id;
      const activeCard = grid.querySelector('[aria-current="step"]');
      requestAnimationFrame(() => {
        if (!activeCard?.isConnected || panel.hidden) return;
        const target = activeCard.getBoundingClientRect();
        const viewport = grid.getBoundingClientRect();
        grid.scrollTo({top: grid.scrollTop + target.top - viewport.top - (grid.clientHeight - target.height) / 2,
          behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
      });
    }
    if (!matching.length) grid.append(el('p', selection?.current ? `Checking ${selection.current}. Keep/reject decisions follow comparison within its camera group.` : extracting ? 'Reading the video. Frames will appear as they are written.' : 'No images in this category yet.', 'evidence-empty'));
    pageLabel.textContent = `Page ${page + 1} of ${pages} · ${matching.length} images`;
    previous.disabled = page === 0; next.disabled = page + 1 >= pages;
    return;
  }
  lastKey = ''; filters.hidden = footer.hidden = true;
  grid.replaceChildren();
  if (stage === 'sfm') {
    title.textContent = data.substage?.includes('matcher') ? 'Finding overlapping views' : data.substage === 'mapper' ? 'Recovering camera positions' : 'Detecting image features';
    const features = data.feature_preview;
    if (data.substage === 'mapper') {
      const mapping = data.mapper_preview;
      title.textContent = 'Recovering camera positions';
      status.textContent = mapping
        ? `${mapping.registered} views registered before the latest placement attempt · ${mapping.refining ? 'Refining camera alignment' : 'Placing the next photograph'}`
        : 'COLMAP is choosing the initial views for the reconstruction.';
      if (mapping) {
        const wrap = el('div', null, 'feature-evidence mapper-evidence');
        const image = el('img'); image.src = mapping.url; image.alt = mapping.name;
        wrap.append(el('h3', 'Latest photograph being placed'), image, el('p', mapping.name));
        if (mapping.shared_points != null) wrap.append(el('p', `${mapping.shared_points.toLocaleString()} shared 3D points out of ${mapping.visible_points.toLocaleString()} reported points help COLMAP estimate this camera’s position.`));
        const steps = el('div', null, 'mapper-steps');
        for (const [label, active] of [['Find shared points', !mapping.refining], ['Estimate camera pose', !mapping.refining], ['Refine the reconstruction', mapping.refining]]) {
          const step = el('span', label, active ? 'is-current' : ''); steps.append(step);
        }
        wrap.append(steps, el('p', 'Live placement activity. The interactive 3D camera layout appears when a saved reconstruction is available.'));
        grid.append(wrap);
      }
      return;
    }
    status.textContent = features ? `${features.processed} images analysed · ${features.features.toLocaleString()} features in this image${features.points.length ? ` · showing ${features.points.length} recorded feature locations` : ' · latest image reported by COLMAP'}` : 'Waiting for COLMAP to publish image features. Camera positions appear after matching.';
    if (data.substage === 'sequential_matcher') {
      const sequence = data.sequential_progress;
      status.textContent = sequence ? `Video frame ${sequence.count} of ${sequence.total}: finding nearby overlapping views and reusing completed matches.` : 'Matching nearby video frames; completed image matches are retained.';
      const retained = (data.selection?.records || []).filter(record => record.status === 'kept');
      if (sequence && retained.length) {
        const current = Math.min(sequence.count - 1, retained.length - 1);
        for (const record of retained.slice(Math.max(0,current-1), Math.min(retained.length,current+2))) {
          grid.append(card({...record,reason:'Retained capture frame near the current video comparison.'}));
        }
      }
    } else if (features?.matching && data.substage?.includes('matcher')) {
      const match = features.matching;
      status.textContent = `Comparing image block ${match.row}, ${match.column} in a ${match.rows} × ${match.columns} grid. Each block tests groups of photographs for shared details.`;
      const matrix = el('div', null, 'matching-grid');
      matrix.style.gridTemplateColumns = `repeat(${Math.min(match.columns,16)},1fr)`;
      if (match.rows * match.columns <= 256) for(let row=1;row<=match.rows;row++) for(let col=1;col<=match.columns;col++) {
        const cell=el('div', `${row} · ${col}`, row===match.row&&col===match.column?'is-current':'');
        if(row===match.row&&col===match.column)cell.setAttribute('aria-label',`Current comparison block ${row}, ${col}`);
        matrix.append(cell);
      }
      grid.append(matrix);
    }
    const pair = data.matching_preview;
    if (pair?.images?.length === 2 && data.substage?.includes('matcher')) {
      const wrap = el('div', null, 'feature-evidence matching-pair');
      const heading = el('h3', `${pair.verified_pairs.toLocaleString()} overlapping pairs verified`);
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', '0 0 1020 400');
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', 'Verified matching features between two captured photographs');
      const layouts = pair.images.map((image, index) => {
        const scale = Math.min(500 / image.width, 400 / image.height);
        const x = index * 520 + (500 - image.width * scale) / 2;
        const y = (400 - image.height * scale) / 2;
        const node = document.createElementNS(svg.namespaceURI, 'image');
        for (const [key, value] of Object.entries({href:image.url, x, y, width:image.width * scale, height:image.height * scale})) node.setAttribute(key, value);
        svg.append(node);
        return {x, y, scale};
      });
      for (const [x1,y1,x2,y2] of pair.correspondences || []) {
        const a = layouts[0], b = layouts[1];
        const line = document.createElementNS(svg.namespaceURI, 'line');
        for (const [key, value] of Object.entries({x1:a.x+x1*a.scale, y1:a.y+y1*a.scale, x2:b.x+x2*b.scale, y2:b.y+y2*b.scale, stroke:'#64e6c4', 'stroke-width':1, 'stroke-opacity':0.55})) line.setAttribute(key,value);
        svg.append(line);
      }
      wrap.append(heading, svg, el('p', pair.images.map(image => image.name).join(' ↔ ')),
        el('p', `${pair.inliers.toLocaleString()} verified matching points · showing ${pair.correspondences.length}. Saved comparison; updates as COLMAP commits matches.`));
      grid.prepend(wrap);
    } else if (features?.image) {
      const wrap = el('div', null, 'feature-evidence feature-keypoints');
      const points = features.points || [];
      filters.hidden = false;
      const toggle = el('button', showFeaturePoints ? 'Hide feature overlay' : 'Show feature overlay');
      toggle.type = 'button';
      toggle.disabled = !points.length;
      toggle.setAttribute('aria-pressed', String(showFeaturePoints));
      toggle.onclick = () => {showFeaturePoints = !showFeaturePoints; paintEvidence(latest, selectedStage);};
      filters.replaceChildren(toggle);
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', `0 0 ${features.width} ${features.height}`);
      svg.setAttribute('role','img'); svg.setAttribute('aria-label', 'Detected image features on ' + features.image);
      const image = document.createElementNS(svg.namespaceURI, 'image'); image.setAttribute('href',features.url); image.setAttribute('width',features.width); image.setAttribute('height',features.height); svg.append(image);
      if (showFeaturePoints) for (const [x,y] of points) {const dot=document.createElementNS(svg.namespaceURI,'circle'); dot.setAttribute('cx',x);dot.setAttribute('cy',y);dot.setAttribute('r',Math.max(features.width/650,2));dot.setAttribute('fill','#ffb347');dot.setAttribute('stroke','#24140a');dot.setAttribute('stroke-width',Math.max(features.width/1600,1));svg.append(dot);}
      wrap.append(svg,el('p',features.image),el('p',points.length ? `Orange dots mark detected features. Showing ${points.length.toLocaleString()} of ${features.features.toLocaleString()} saved keypoints; updates as each image is processed.` : 'Waiting for COLMAP to save keypoint locations for the overlay.'));
      grid.append(wrap);
    }
    return;
  }
  if (stage === 'evaluate') {
    title.textContent = 'Compare the reconstruction';
    const metrics = data.evaluation;
    status.textContent = metrics ? `Saved-model evaluation · PSNR ${metrics.overall_psnr?.toFixed(2)} dB · SSIM ${metrics.overall_ssim?.toFixed(3)}` : 'Recorded training renders are visual checkpoints. A separate saved-model evaluation has not been recorded.';
    const evaluated = data.evaluation_progress;
    if (evaluated?.records?.length) {
      status.textContent = `${evaluated.count} / ${evaluated.total} held-out views evaluated${metrics ? ` · PSNR ${metrics.overall_psnr?.toFixed(2)} dB · SSIM ${metrics.overall_ssim?.toFixed(3)}` : ' · running measurements'}`;
      for (const view of evaluated.records) {
        for (const [key,label] of [['source','Source photograph'],['render','Evaluated reconstruction']]) {
          if (/^[0-9a-f]{32}-(source|render)\.jpg$/.test(view[key])) grid.append(card({url:data.evaluation_preview_root+view[key],file:view.camera,status:label,reason:`PSNR ${view.psnr.toFixed(2)} dB · SSIM ${view.ssim.toFixed(3)}`}));
        }
      }
      return;
    }
    if (data.source_url) grid.append(card({url:data.source_url,file:'Source photograph',status:'Reference',reason:'Photographed evidence for the recorded view.'}));
    for (const image of (data.images || []).slice(-12)) grid.append(card({url:image.url,file:`Step ${image.step ?? '—'} · ${image.camera || 'Recorded view'}`,status:'Reconstruction',reason:'Actual recorded render'}));
    if (!grid.childElementCount) grid.append(el('p', 'Comparison renders will appear when recorded. No evaluation images are available yet.', 'evidence-empty'));
  } else {
    title.textContent = 'Building the preservation archive';
    const archive = data.packaging;
    status.textContent = archive?.complete ? 'Manifest written. Checksums recorded; independent verification is a separate step.' : archive?.current ? `${archive.phase} · ${archive.current}` : 'Originals, camera poses, model files and checksums are collected here when packaging runs.';
    for (const [label, value] of [['Files copied',archive?.copied],['Files checksummed',archive?.checksummed],['Bytes inventoried',archive?.bytes]]) {
      const item = el('article', null, 'evidence-stat'); item.append(el('strong', value == null ? '—' : value.toLocaleString()), el('p', label)); grid.append(item);
    }
    if (archive?.complete) {const link = el('a', 'Open preservation manifest ↗'); link.href = data.manifest_url; link.target = '_blank'; link.rel = 'noopener'; grid.append(link);}
  }
}

function recordsReady(selection) { return `${selection.records?.length || 0} previews ready · decisions observed from output files`; }
