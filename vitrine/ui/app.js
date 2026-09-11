/** Vitrine local archive UI — simple (academic) or advanced (technical) view. */
import { renderStudioLibrary, renderStudioDetail, renderObjectLibrary, renderSeparatedObject, collectSeparatedObjects } from './studio.js';

const $ = (sel, el = document) => el.querySelector(sel);
const viewEl = $("#view");
const flashEl = $("#flash");
const healthEl = $("#health");
const modeBannerEl = $("#mode-banner");
const btnAdvanced = $("#btn-advanced");

const MODE_KEY = "vitrine.ui.advanced";
const SIDEBAR_KEY = "vitrine.ui.sidebar";

const state = {
  view: "runs",
  libraryKind: "scene",
  runs: [],
  selected: null,
  selectedObject: null,
  doctor: null,
  profiles: null,
  advanced: false,
  sidebarCollapsed: false,
  captureFiles: [],
};

let liveTimer = null;

/** Stage copy: simple (academic) vs advanced (pipeline IDs). */
const STAGE = {
  ingest: {
    simple: {
      title: "Prepare photographs",
      short: "Photographs",
      done: "Photographs selected and organised",
      todo: "Waiting for photographs",
    },
    advanced: {
      title: "ingest",
      short: "ingest",
      done: "ingest.json present",
      todo: "not run yet",
    },
  },
  sfm: {
    simple: {
      title: "Map camera positions",
      short: "Cameras",
      done: "Camera positions reconstructed",
      todo: "Not mapped yet",
    },
    advanced: {
      title: "sfm",
      short: "sfm",
      done: "sparse model present",
      todo: "not run yet",
    },
  },
  train: {
    simple: {
      title: "Build 3D model",
      short: "3D model",
      done: "Three-dimensional model complete",
      todo: "Not built yet",
    },
    advanced: {
      title: "train",
      short: "train",
      done: "train.json / scene.ply",
      todo: "not run yet",
    },
  },
  evaluate: {
    simple: {
      title: "Check quality",
      short: "Quality",
      done: "Quality checks recorded",
      todo: "Not checked yet",
    },
    advanced: {
      title: "evaluate",
      short: "evaluate",
      done: "evaluation.json present",
      todo: "not run yet",
    },
  },
  package: {
    simple: {
      title: "Create archive package",
      short: "Archive",
      done: "Preservation package ready",
      todo: "Not packaged yet",
    },
    advanced: {
      title: "package",
      short: "package",
      done: "archive/manifest.json",
      todo: "not run yet",
    },
  },
  view: {
    simple: {
      title: "Interactive viewer",
      short: "Viewer",
      done: "Ready to explore in 3D",
      todo: "Viewer not ready",
    },
    advanced: {
      title: "view",
      short: "view",
      done: "scene.splat ready",
      todo: "not run yet",
    },
  },
};

const ARTEFACT = {
  scene_ply: {
    simple: { role: "Master 3D model", note: "Full-quality archival model (PLY)" },
    advanced: { role: "scene_ply", note: "model/scene.ply — full SH master" },
  },
  scene_splat: {
    simple: { role: "Interactive model", note: "Fast format for the web viewer" },
    advanced: { role: "scene_splat", note: "model/scene.splat — web delivery" },
  },
  checkpoint: {
    simple: { role: "Training checkpoint", note: "Snapshot saved mid-process" },
    advanced: { role: "checkpoint", note: "model/checkpoint_10000.ply" },
  },
  train_json: {
    simple: { role: "Model record", note: "Settings and quality scores" },
    advanced: { role: "train_json", note: "model/train.json" },
  },
  ingest_json: {
    simple: { role: "Photograph inventory", note: "What was accepted and rejected" },
    advanced: { role: "ingest_json", note: "ingest/ingest.json" },
  },
  sfm_json: {
    simple: { role: "Camera survey", note: "Positions and reconstruction stats" },
    advanced: { role: "sfm_json", note: "sfm/sfm.json" },
  },
  sfm_log: {
    simple: { role: "Processing log", note: "Technical log (for specialists)" },
    advanced: { role: "sfm_log", note: "logs / colmap.log" },
  },
  train_log: {
    simple: { role: "Build log", note: "Technical log (for specialists)" },
    advanced: { role: "train_log", note: "logs / train log" },
  },
};

const QUALITY_BLURB = {
  draft: "A quick preview — useful while testing a capture, not for deposit.",
  standard: "Legacy profile. Review its crop coverage warning before a quality-critical run.",
  archive: "Experimental on workstations: validate crop coverage and reconstruction quality before use. Archive packaging is available independently of build quality.",
};

/** Pipeline goals — what each stage is for (simple + advanced wording). */
const PIPELINE_GOALS = {
  ingest: {
    simple: {
      goal: "Select clear photographs",
      why: "Keep sharp, usable frames; set aside blurry ones.",
    },
    advanced: {
      goal: "Ingest + sharpness filter",
      why: "Laplacian select · multi-camera groups · ingest.json",
    },
  },
  sfm: {
    simple: {
      goal: "Map where each photo was taken",
      why: "Reconstruct camera positions so the space holds together.",
    },
    advanced: {
      goal: "Structure-from-motion (COLMAP)",
      why: "Sparse model · multi-camera intrinsics · sparse_text",
    },
  },
  train: {
    simple: {
      goal: "Build the 3D model",
      why: "Learn a walkable reconstruction from the photographs.",
    },
    advanced: {
      goal: "3DGS train (gsplat MCMC)",
      why: "cap_max · crops · SSIM objective · scene.ply / .splat",
    },
  },
  evaluate: {
    simple: {
      goal: "Check quality against photographs",
      why: "Score how closely the model matches held-out views.",
    },
    advanced: {
      goal: "Held-out eval",
      why: "PSNR / SSIM · evaluation.json",
    },
  },
  package: {
    simple: {
      goal: "Create the archive package",
      why: "Originals, poses, model, checksums — deposit-ready.",
    },
    advanced: {
      goal: "Preservation package",
      why: "archive/ · manifest.json · SHA-256 · README",
    },
  },
  view: {
    simple: {
      goal: "Explore in 3D",
      why: "Walk through the space in the browser, locally.",
    },
    advanced: {
      goal: "Interactive viewer",
      why: "scene.splat · GaussianSplats3D / point fallback",
    },
  },
};

const STAGE_ORDER = ["ingest", "sfm", "train", "evaluate", "package", "view"];

function pipelineGoal(name) {
  const g = PIPELINE_GOALS[name];
  if (!g) return { goal: name, why: "" };
  return isAdv() ? g.advanced : g.simple;
}

const FOOTER = {
  simple:
    "Vitrine is a local digital-preservation tool for heritage spaces and installations. Everything stays on this computer — nothing is sent to the cloud.",
  advanced:
    "Local inspection UI for artefacts under runs/. Heavy stages still run from the CLI: python -m vitrine ingest|sfm|train|package",
};

const TAGLINE = {
  simple:
    "Preserve a place in three dimensions — photographs become an archive you can walk through.",
  advanced:
    "Inspect stages, PSNR/SSIM, artefacts, and the splat viewer for runs already on disk.",
};

/* ---------- mode ---------- */

function isAdv() {
  return !!state.advanced;
}

function stageMeta(name) {
  const s = STAGE[name];
  if (!s) {
    return { title: name, short: name, done: "complete", todo: "not run yet" };
  }
  return isAdv() ? s.advanced : s.simple;
}

function artefactMeta(key) {
  const a = ARTEFACT[key];
  if (!a) return { role: key, note: "" };
  return isAdv() ? a.advanced : a.simple;
}

function displayName(name) {
  return isAdv() ? String(name) : prettyName(name);
}

function profileLabel(profile) {
  if (!profile) return "";
  return isAdv() ? String(profile) : friendlyProfile(profile);
}

/** Format the saved splat creation/export time. Returns { date, time, full } or null. */
function formatArchiveWhen(run) {
  const ms = run?.splat_created_mtime != null ? Number(run.splat_created_mtime) * 1000 : null;
  if (ms == null || Number.isNaN(ms)) return null;
  const d = new Date(ms);
  const date = d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const time = d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  return { date, time, full: `${date} · ${time}`, ms };
}

function archiveWhenHtml(run, { compact = false } = {}) {
  const when = formatArchiveWhen(run);
  if (!when) return "";
  const label = "Splat created";
  if (compact) {
    return `<div class="archive-when" title="${escapeHtml(when.full)}">
      <span class="archive-when-label">${label}</span>
      <time datetime="${escapeHtml(new Date(when.ms).toISOString())}">${escapeHtml(when.date)}</time>
      <span class="archive-when-sep" aria-hidden="true">·</span>
      <span class="archive-when-time mono">${escapeHtml(when.time)}</span>
    </div>`;
  }
  return `<div class="archive-when" title="${escapeHtml(when.full)}">
    <span class="archive-when-label">${label}</span>
    <time datetime="${escapeHtml(new Date(when.ms).toISOString())}">${escapeHtml(when.date)}</time>
    <span class="archive-when-sep" aria-hidden="true">at</span>
    <span class="archive-when-time mono">${escapeHtml(when.time)}</span>
  </div>`;
}

function loadMode() {
  try {
    return localStorage.getItem(MODE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveMode(on) {
  try {
    localStorage.setItem(MODE_KEY, on ? "1" : "0");
  } catch {
    /* private mode / blocked storage */
  }
}

function applyModeChrome() {
  document.body.classList.toggle("mode-advanced", state.advanced);
  if (btnAdvanced) {
    btnAdvanced.setAttribute("aria-checked", state.advanced ? "true" : "false");
  }
  if (modeBannerEl) {
    modeBannerEl.classList.toggle("hidden", !state.advanced);
  }

  const hint = $("#mode-toggle-hint");
  if (hint) {
    hint.textContent = state.advanced
      ? "Pipeline metrics, stage IDs, paths, and logs"
      : "Show pipeline metrics, stage IDs, and logs";
  }

  document.querySelectorAll("#nav [data-simple][data-advanced]").forEach((el) => {
    el.textContent = state.advanced ? el.dataset.advanced : el.dataset.simple;
  });

  const tag = $("#brand-tagline");
  if (tag) tag.textContent = state.advanced ? TAGLINE.advanced : TAGLINE.simple;

  const foot = $("#footer-copy");
  if (foot) {
    if (state.advanced) {
      foot.innerHTML =
        'Local inspection UI for artefacts under <code>runs/</code>. Heavy stages still run from the CLI: <code>python -m vitrine ingest|sfm|train|package</code>';
    } else {
      foot.textContent = FOOTER.simple;
    }
  }
  applySidebar();
}

function loadSidebar() {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}

function saveSidebar(collapsed) {
  try {
    localStorage.setItem(SIDEBAR_KEY, collapsed ? "collapsed" : "open");
  } catch {
    /* private mode / blocked storage */
  }
}

function applySidebar() {
  const collapsed = !!state.sidebarCollapsed;
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  const btn = $("#btn-sidebar");
  if (btn) {
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    btn.title = collapsed ? "Open menu" : "Collapse menu";
    const label = btn.querySelector(".sr-only");
    if (label) label.textContent = collapsed ? "Open menu" : "Collapse menu";
  }
}

function setAdvanced(on) {
  state.advanced = !!on;
  saveMode(state.advanced);
  applyModeChrome();
  if (!state.advanced && (state.view === "doctor" || state.view === "profiles")) {
    switchView(state.libraryKind === "object" ? "objects" : "runs");
    if (state.doctor) renderHealthStrip(state.doctor);
    return;
  }
  if (state.view === "runs") renderRunsList();
  else if (state.view === "objects") renderObjectLibrary(studioContext());
  else if (state.view === "object" && state.selectedObject) renderSeparatedObject(state.selectedObject, studioContext());
  else if (state.view === "run" && state.selected) renderRunDetail(state.selected);
  else if (state.view === "doctor") renderDoctor();
  else if (state.view === "profiles") renderProfiles();
  else if (state.view === "create") renderCreate();
  else if (state.view === "construction") loadConstructionFrame();
  if (state.doctor) renderHealthStrip(state.doctor);
}

function bindModeToggle() {
  btnAdvanced?.addEventListener("click", () => setAdvanced(!state.advanced));
  $("#btn-mode-banner-off")?.addEventListener("click", () => setAdvanced(false));
}

function bindSidebar() {
  $("#btn-sidebar")?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const collapsed = document.body.classList.contains("sidebar-collapsed");
    state.sidebarCollapsed = !collapsed;
    saveSidebar(state.sidebarCollapsed);
    applySidebar();
  });
}

function doctorGit(d) {
  const git = d?.git || {};
  return git.branch || d?.git_branch || git.name || null;
}

function renderBuildStamp(d) {
  const stamp = $("#build-stamp");
  if (!stamp) return;
  const version = d?.version ? `Vitrine ${d.version}` : "Vitrine";
  const git = d?.git || {};
  const branch = doctorGit(d);
  const commit = git.commit || d?.git_commit;
  const extra = branch && branch !== "HEAD" ? branch : commit;
  stamp.textContent = extra ? `${version} · ${extra}` : version;
  stamp.title = extra ? `${version} · ${extra}${commit && extra !== commit ? ` (${commit})` : ""}` : version;
}

/* ---------- utils ---------- */

function stopLivePoll() {
  if (liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmt(n, digits = 0) {
  if (n == null || Number.isNaN(n)) return "—";
  if (typeof n === "number") {
    return n.toLocaleString(undefined, {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits > 0 ? Math.min(digits, 2) : 0,
    });
  }
  return String(n);
}

function fmtBytes(b) {
  if (b == null) return "—";
  if (b < 1024) return `${b} B`;
  if (b < 2 ** 20) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 2 ** 30) return `${(b / 2 ** 20).toFixed(1)} MB`;
  return `${(b / 2 ** 30).toFixed(2)} GB`;
}

function prettyName(name) {
  return String(name)
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function fidelityLabel(psnr) {
  if (psnr == null) return null;
  if (psnr >= 30) return "Excellent match to the photographs";
  if (psnr >= 25) return "Strong match to the photographs";
  if (psnr >= 20) return "Good overall resemblance";
  return "Lower detail — may look soft up close";
}

function progressBadge(run) {
  if (run.headline?.running) {
    return `<span class="badge live">${isAdv() ? "● training" : "Building now"}</span>`;
  }
  if (run.headline?.interrupted || run.stages?.train?.interrupted) {
    return `<span class="badge interrupted">${isAdv() ? "interrupted" : "Stopped mid-build"}</span>`;
  }
  const { done, total } = run.progress || { done: 0, total: 6 };
  if (done >= total) {
    return `<span class="badge done">${isAdv() ? `${done}/${total} stages` : "Complete"}</span>`;
  }
  if (done > 0) {
    return `<span class="badge partial">${isAdv() ? `${done}/${total} stages` : `${done} of ${total} steps`}</span>`;
  }
  // Checkpoints alone (no train.json / progress) still count as partial work.
  if (run.stages?.train?.has_checkpoint) {
    return `<span class="badge partial">${isAdv() ? "checkpoint only" : "Partial train"}</span>`;
  }
  return `<span class="badge todo">${isAdv() ? "empty" : "Not started"}</span>`;
}

function stagePills(run) {
  const order = run.progress?.order || Object.keys(STAGE);
  return order
    .map((name) => {
      const meta = stageMeta(name);
      const s = run.stages?.[name];
      let cls = "off";
      if (s?.running) cls = "running";
      else if (s?.interrupted) cls = "interrupted";
      else if (s?.done) cls = "on";
      return `<span class="stage-pill ${cls}">${escapeHtml(meta.short)}</span>`;
    })
    .join("");
}

function friendlyProfile(profile) {
  const s = String(profile);
  const parts = s.split(/[-_/]/).filter(Boolean);
  if (parts.length >= 2) {
    const quality = parts[parts.length - 1];
    const tier = parts.slice(0, -1).join(" ");
    return `${quality.charAt(0).toUpperCase() + quality.slice(1)} · ${tier}`;
  }
  return s;
}

async function api(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j.error) msg = j.error;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json();
}

async function postApi(path, body = {}) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* keep the HTTP status as the useful error */
  }
  if (!res.ok) throw new Error(payload.error || `${res.status} ${res.statusText}`);
  return payload;
}

function recoveryState(run) {
  const headline = run?.headline || {};
  const job = run?.capture_job || {};
  const pipeline = run?.pipeline || {};
  const worker = headline.worker_state || job.state || pipeline.state;
  const running = !!(headline.running || job.running);
  const stale = !!(job.stale || job.recovery_required || headline.worker_stale);
  const recoverable = !!pipeline.schema && !running && (
    stale || headline.interrupted || ["failed", "cancelled", "unknown"].includes(worker)
  );
  return { headline, job, pipeline, worker, running, stale, recoverable };
}

function recoveryControlsHtml(run, adv = true) {
  const state = recoveryState(run);
  if (!state.pipeline.schema && !state.running) return "";
  const controls = [];
  if (state.running) {
    controls.push(`<button type="button" class="ghost" id="btn-cancel-run">${adv ? "Request cancel" : "Stop processing"}</button>`);
  }
  if (state.recoverable) {
    controls.push(`<button type="button" class="primary" id="btn-resume-run">${adv ? "Resume pipeline" : "Resume build"}</button>`);
  }
  controls.push(`<button type="button" class="ghost" id="btn-refresh-run">${adv ? "Refresh state" : "Refresh"}</button>`);
  return controls.join(" ");
}

function recoveryBannerHtml(run, adv = true) {
  const state = recoveryState(run);
  if (state.running) return "";
  if (state.stale || ["unknown"].includes(state.worker)) {
    return `<div class="live-banner interrupted" role="status">
      ${adv ? "Pipeline worker state is unknown" : "This build needs attention"} — the worker is no longer running and no terminal state was recorded. Completed stages remain on disk.
      ${state.recoverable ? "Use Resume to continue from verified stages." : "Inspect the log and continue from the command line."}
    </div>`;
  }
  if (state.worker === "failed" || state.headline.interrupted) {
    return `<div class="live-banner interrupted" role="status">
      ${adv ? "Pipeline stopped before completion" : "This build stopped before completion"}. ${escapeHtml(state.job.error || state.headline.worker_error || state.pipeline.error || "Completed stages remain available.")}
      ${state.recoverable ? "Use Resume to retry verified stages." : "Review the saved log before retrying."}
    </div>`;
  }
  return "";
}

function showFlash(err) {
  if (!err) {
    flashEl.classList.add("hidden");
    flashEl.textContent = "";
    return;
  }
  flashEl.textContent = String(err.message || err);
  flashEl.classList.remove("hidden");
}

/* ---------- health strip ---------- */

function renderHealthStrip(d) {
  const ok = d.ok;
  if (isAdv()) {
    const gpu = d.gpu?.available
      ? `${d.gpu.name} · ${d.gpu.vram_gb} GB`
      : "no GPU";
    healthEl.innerHTML = `
      <span class="label">Environment</span>
      <div class="${ok ? "ok" : "bad"}">${ok ? "Ready" : "Issues found"}</div>
      <div class="meta">${escapeHtml(gpu)}</div>
      <div class="meta">tier ${escapeHtml(String(d.tier))} · v${escapeHtml(String(d.version))}</div>
    `;
    renderBuildStamp(d);
  } else {
    const gpu = d.gpu?.available
      ? d.gpu.name
      : "Graphics hardware not detected";
    healthEl.innerHTML = `
      <span class="label">Workstation status</span>
      <div class="${ok ? "ok" : "bad"}">${ok ? "Ready" : "Needs attention"}</div>
      <div class="meta">${escapeHtml(gpu)}</div>
    `;
    renderBuildStamp(d);
  }
  healthEl.style.cursor = isAdv() ? "pointer" : "";
  healthEl.title = isAdv() ? "Open workstation checks" : "";
  healthEl.onclick = isAdv() ? () => switchView("doctor") : null;
}

async function loadGitIdentity() {
  const fromDoctor = state.doctor?.git;
  if (fromDoctor?.branch || fromDoctor?.commit) return fromDoctor;
  try {
    const health = await api("/api/health");
    if (health?.git?.branch || health?.git?.commit) return health.git;
  } catch {
    /* older dashboard process */
  }
  try {
    const res = await fetch("/static/git-identity.json", { cache: "no-store" });
    if (res.ok) {
      const git = await res.json();
      if (git?.branch || git?.commit) return git;
    }
  } catch {
    /* optional static fallback */
  }
  return fromDoctor || {};
}

async function loadHealth() {
  try {
    const d = await api("/api/doctor");
    state.doctor = d;
    d.git = { ...(d.git || {}), ...(await loadGitIdentity()) };
    renderHealthStrip(d);
    // Mission board shows workstation readiness — refresh if we're on the library.
    if (state.view === "runs") renderRunsList();
  } catch (err) {
    healthEl.innerHTML = `
      <span class="label">${isAdv() ? "Environment" : "Workstation status"}</span>
      <div class="bad">${isAdv() ? "Doctor failed" : "Could not check"}</div>
      <div class="meta">${escapeHtml(err.message)}</div>
    `;
  }
}

/* ---------- library overview: partners + goals + status ---------- */

function summariseLibrary(runs) {
  let complete = 0;
  let inProgress = 0;
  let empty = 0;
  let training = 0;
  const stageHits = Object.fromEntries(STAGE_ORDER.map((s) => [s, 0]));

  for (const run of runs) {
    const { done = 0, total = 6 } = run.progress || {};
    if (run.headline?.running) {
      training += 1;
      inProgress += 1;
    } else if (run.headline?.interrupted || run.stages?.train?.interrupted) {
      // Stale progress.json — not live, but not empty either.
      inProgress += 1;
    } else if (done >= total) {
      complete += 1;
    } else if (done > 0 || run.stages?.train?.has_checkpoint) {
      inProgress += 1;
    } else {
      empty += 1;
    }
    for (const name of STAGE_ORDER) {
      if (run.stages?.[name]?.done) stageHits[name] += 1;
    }
  }

  return {
    total: runs.length,
    complete,
    inProgress,
    empty,
    training,
    stageHits,
  };
}

function renderMissionBoard(runs) {
  const adv = isAdv();
  const s = summariseLibrary(runs);
  const wsReady = state.doctor?.ok;
  const wsLabel = state.doctor
    ? wsReady
      ? adv
        ? "Doctor: ready"
        : "Workstation ready"
      : adv
        ? "Doctor: issues"
        : "Workstation needs attention"
    : adv
      ? "Doctor: …"
      : "Checking workstation…";
  const wsClass = state.doctor ? (wsReady ? "ok" : "bad") : "todo";

  const goalRows = STAGE_ORDER.map((name, idx) => {
    const meta = stageMeta(name);
    const goal = pipelineGoal(name);
    const hits = s.stageHits[name] || 0;
    let statusCls = "todo";
    let statusText = adv ? "0 runs" : "Not started anywhere";
    if (s.total === 0) {
      statusText = adv ? "—" : "No archives yet";
    } else if (hits === s.total) {
      statusCls = "done";
      statusText = adv ? `${hits}/${s.total} runs` : `Done on all ${s.total}`;
    } else if (hits > 0) {
      statusCls = "partial";
      statusText = adv ? `${hits}/${s.total} runs` : `Done on ${hits} of ${s.total}`;
    }
    // Live training highlights train stage
    if (name === "train" && s.training > 0) {
      statusCls = "running";
      statusText = adv
        ? `${s.training} training · ${hits}/${s.total} done`
        : `${s.training} building now`;
    }
    return `
      <div class="goal-row ${statusCls}">
        <div class="goal-idx" aria-hidden="true">${idx + 1}</div>
        <div class="goal-body">
          <div class="goal-title">
            <strong>${escapeHtml(meta.title)}</strong>
            <span class="goal-status ${statusCls}">${escapeHtml(statusText)}</span>
          </div>
          <div class="goal-why">${escapeHtml(goal.why)}</div>
          <div class="goal-aim"><span class="goal-aim-label">${adv ? "goal" : "Aim"}</span> ${escapeHtml(goal.goal)}</div>
        </div>
      </div>`;
  }).join("");

  return `
    <section class="mission-board" aria-label="Pipeline goals and status">
      <div class="mission-top">
        <div class="mission-intro">
          <p class="page-kicker">${adv ? "pipeline" : "Project"}</p>
          <h3 class="mission-heading">
            ${adv ? "Goals &amp; stage coverage" : "Pipeline goals &amp; status"}
          </h3>
          <p class="mission-lead">
            ${
              adv
                ? "Stage intent plus how many runs under <code>runs/</code> have completed each step."
                : "What this pipeline is designed to achieve, and how far the archives in this library have progressed."
            }
          </p>
        </div>
      </div>

      <div class="mission-stats">
        <div class="mission-stat">
          <div class="mission-stat-value">${fmt(s.total)}</div>
          <div class="mission-stat-label">${adv ? "runs" : "Archives"}</div>
        </div>
        <div class="mission-stat">
          <div class="mission-stat-value ok">${fmt(s.complete)}</div>
          <div class="mission-stat-label">${adv ? "complete" : "Complete"}</div>
        </div>
        <div class="mission-stat">
          <div class="mission-stat-value ${s.inProgress ? "warn" : ""}">${fmt(s.inProgress)}</div>
          <div class="mission-stat-label">${adv ? "in progress" : "In progress"}</div>
        </div>
        <div class="mission-stat">
          <div class="mission-stat-value ${s.training ? "warn" : ""}">${fmt(s.training)}</div>
          <div class="mission-stat-label">${adv ? "training now" : "Building now"}</div>
        </div>
        <div class="mission-stat">
          <div class="mission-stat-value ${wsClass === "ok" ? "ok" : wsClass === "bad" ? "bad" : ""}">${wsClass === "ok" ? "✓" : wsClass === "bad" ? "!" : "…"}</div>
          <div class="mission-stat-label">${escapeHtml(wsLabel)}</div>
        </div>
      </div>

      <div class="goal-list">
        ${goalRows}
      </div>

      <p class="mission-credit">
        ${
          adv
            ? "UOS Vitrine (preservation) · XR Lab / University of Salford · partner stack: DreamLab AI"
            : "A University of Salford XR Lab project, developed in partnership with DreamLab AI — fully local, no cloud required."
        }
      </p>
    </section>`;
}

/* ---------- create a splat ---------- */

function renderCreate() {
  const adv = isAdv();
  (state.capturePreviewUrls || []).forEach(url => URL.revokeObjectURL(url));
  state.capturePreviewUrls = [];
  viewEl.innerHTML = `
    <div class="create-shell">
      <header class="create-intro">
        <p class="page-kicker">01 / Images</p>
        <h2 id="capture-heading">Create a splat</h2>
        <p id="capture-description">Add overlapping photographs and video of one place. Give the capture a name, then build its 3D splat.</p>
      </header>
      <div class="studio-process" aria-label="Capture workflow"><div><span>01</span><strong>Add images</strong><small>Photographs and video</small></div><div><span>02</span><strong>Build a 3D splat</strong><small>Map cameras and reconstruct</small></div><div><span>03</span><strong>Explore & preserve</strong><small>Review the splat and its archive</small></div></div>

      <form id="capture-form" class="capture-form">
          <fieldset class="capture-kind"><legend>What are you capturing?</legend>
            <label><input type="radio" name="capture_type" value="scene" checked/><span><strong>Scene</strong><small>A room, installation or place</small></span></label>
            <label><input type="radio" name="capture_type" value="object"/><span><strong>Object</strong><small>A single item, photographed from every angle</small></span></label>
          </fieldset>
        <div class="capture-fields">
          <div class="capture-section-heading"><h3>Capture details</h3><p>Name your space and choose how to build it.</p></div>
          <label>
            <span>Capture title</span>
            <input id="capture-title" name="title" required maxlength="120" placeholder="e.g. Nested Cinema — final installation"/>
          </label>
          <label>
            <span>Description <small class="optional-label">Optional</small></span>
            <textarea id="capture-subject" name="subject" rows="3" placeholder="A short description for the preservation record"></textarea>
          </label>
          <label class="capture-quality-field ${adv ? "" : "hidden"}">
            <span>Build quality</span>
            <select id="capture-quality" name="quality">
              <option value="demo" selected>Demo — measured capture recipe</option>
              <option value="draft">Draft — quickest camera and coverage check</option>
              <option value="standard">Standard — legacy reconstruction profile</option>
              <option value="archive">Archive — experimental on workstations</option>
            </select>
            <small class="muted">Build time depends on the capture. Archive quality needs validation on this workstation.</small>
          </label>
          <div class="capture-note" id="capture-note-media">
            <strong>A little overlap goes a long way</strong>
            <span>Move around the space, keep details in focus, and capture objects from several angles. Processing stays on this computer.</span>
          </div>
          <div class="capture-note hidden" id="capture-note-session">
            <strong>Keep stills and video in their folders</strong>
            <span>A session zip from the iPhone app preserves camera groups, locked settings, and screens/mirrors decisions. Loose files cannot do that.</span>
          </div>
        </div>

        <section class="capture-media" aria-label="Capture media">
          <div class="capture-section-heading"><h3>Add your media</h3><p>Use overlapping views of the same space.</p></div>
          <div class="capture-source-mode" role="tablist" aria-label="Source type">
            <button type="button" role="tab" id="source-mode-media" aria-selected="true">Photographs and video</button>
            <button type="button" role="tab" id="source-mode-session" class="is-locked" aria-selected="false" aria-disabled="true" disabled title="Vitrine App import is coming soon"><span>Vitrine App</span><span class="coming-soon">Coming soon</span></button>
          </div>
        <div class="capture-tray" id="capture-tray">
          <input id="capture-files" name="files" type="file" multiple
            accept=".arw,image/x-sony-arw,image/jpeg,image/png,image/webp,image/tiff,image/heic,image/heif,video/mp4,video/quicktime,video/x-m4v,video/x-msvideo,video/x-matroska"/>
          <input id="capture-session" name="session" type="file" accept=".zip,application/zip" disabled/>
          <div class="capture-tray-mark" aria-hidden="true">
            <svg width="44" height="44" viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="10" width="28" height="28" rx="5"/><path d="m9 31 8-8 7 7 4-4 7 7"/><circle cx="27" cy="19" r="2.5"/><path d="M15 6h20a5 5 0 0 1 5 5v16" opacity=".4"/><circle class="capture-upload-badge" cx="36" cy="36" r="10"/><path d="M36 41V31m-4 4 4-4 4 4"/></svg>
          </div>
          <strong id="capture-tray-title">Drop photographs or video here</strong>
          <span id="capture-tray-copy">or choose files from this computer</span>
          <button type="button" class="soft" id="btn-choose-media">Choose media</button>
          <small id="capture-tray-hint">JPG, PNG, HEIC, TIFF, WebP, Sony ARW · MP4, MOV, M4V, AVI, MKV</small>
        </div>

        <div id="capture-selection" class="capture-selection hidden" aria-live="polite"></div>
        <p class="capture-local-note">Your files and processing stay on this computer.</p>
        </section>
        <div id="capture-progress" class="capture-progress hidden" aria-live="polite">
          <div><span id="capture-progress-label">Copying media…</span><b id="capture-progress-value">0%</b></div>
          <progress id="capture-progress-bar" max="100" value="0"></progress>
        </div>
        <div id="capture-result" class="capture-result hidden" role="status"></div>

        <div class="capture-actions">
          <p>${adv ? "Starts vitrine run: ingest → sfm → train → package." : "You can leave this page once processing starts; progress will appear in the Library."}</p>
          <button type="submit" class="primary" id="btn-create-splat" disabled>Create splat</button>
        </div>
      </form>
    </div>`;

  const input = $("#capture-files");
  const sessionInput = $("#capture-session");
  const tray = $("#capture-tray");
  const choose = $("#btn-choose-media");
  const form = $("#capture-form");
  const submit = $("#btn-create-splat");
  const setSourceMode = (mode, reset = true) => {
    // Vitrine App session import is locked until that client ships.
    state.captureSourceMode = mode === "session" ? "media" : mode;
    $("#source-mode-media").setAttribute("aria-selected", "true");
    $("#source-mode-session").setAttribute("aria-selected", "false");
    $("#capture-note-media").classList.remove("hidden");
    $("#capture-note-session").classList.add("hidden");
    $("#capture-tray-title").textContent = "Drop photographs or video here";
    $("#capture-tray-copy").textContent = "or choose files from this computer";
    $("#capture-tray-hint").textContent = "JPG, PNG, HEIC, TIFF, WebP, Sony ARW · MP4, MOV, M4V, AVI, MKV";
    choose.textContent = "Choose media";
    if (reset) {
      input.value = "";
      sessionInput.value = "";
      setFiles([]);
    }
  };

  const setFiles = (files) => {
    const session = state.captureSourceMode === "session";
    const incoming = Array.from(files || []);
    state.captureFiles = session
      ? incoming.filter((file) => /\.zip$/i.test(file.name)).slice(0, 1)
      : incoming;
    const selection = $("#capture-selection");
    if (!state.captureFiles.length) {
      selection.classList.add("hidden");
      submit.disabled = true;
      return;
    }
    const total = state.captureFiles.reduce((sum, file) => sum + file.size, 0);
    const images = state.captureFiles.filter((file) => (file.type.startsWith("image/") || /\.(arw|heic|heif|tiff?|bmp|jpe?g|png|webp)$/i.test(file.name))).length;
    const videos = state.captureFiles.filter((file) => file.type.startsWith("video/")).length;
    (state.capturePreviewUrls || []).forEach(url => URL.revokeObjectURL(url));
    state.capturePreviewUrls = [];
    const thumbnails = state.captureFiles.slice(0,6).map(file => {
      if (!/\.(jpe?g|png|webp)$/i.test(file.name)) return `<span class="selected-file-type">${escapeHtml(file.name.split('.').pop().toUpperCase())}</span>`;
      const url=URL.createObjectURL(file);state.capturePreviewUrls.push(url);
      return `<img src="${url}" alt="${escapeHtml(file.name)}"/>`;
    }).join('');
    selection.innerHTML = session
      ? `<div><strong>Capture session ready</strong><span>${fmtBytes(total)}</span></div><p>${escapeHtml(state.captureFiles[0].name)}</p><button type="button" class="ghost" id="clear-media">Clear selection</button>`
      : `<div><strong>${fmt(state.captureFiles.length)} file${state.captureFiles.length===1?'':'s'} ready</strong><span>${fmtBytes(total)}</span></div>
      <p>${images ? `${fmt(images)} photograph${images === 1 ? "" : "s"}` : ""}${images && videos ? " · " : ""}${videos ? `${fmt(videos)} video${videos === 1 ? "" : "s"}` : ""}</p><div class="selected-thumbnails">${thumbnails}</div><button type="button" class="ghost" id="clear-media">Clear selection</button>`;
    $('#clear-media').onclick=()=>{input.value='';sessionInput.value='';setFiles([]);};
    selection.classList.remove("hidden");
    submit.disabled = false;
  };

  $("#source-mode-media").addEventListener("click", () => setSourceMode("media"));
  choose.addEventListener("click", () => input.click());
  input.addEventListener("change", () => setFiles(input.files));
  ["dragenter", "dragover"].forEach((name) => tray.addEventListener(name, (event) => {
    event.preventDefault();
    tray.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((name) => tray.addEventListener(name, (event) => {
    event.preventDefault();
    tray.classList.remove("dragging");
  }));
  tray.addEventListener("drop", (event) => setFiles(event.dataTransfer.files));

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.captureUploading || !state.captureFiles.length || !form.reportValidity()) return;
    state.captureUploading = true;
    const data = new FormData();
    data.append("title", $("#capture-title").value);
    data.append("subject", $("#capture-subject").value);
    data.append("quality", adv ? ($("#capture-quality")?.value || "demo") : "demo");
    data.append("capture_type", form.elements.capture_type.value);
    state.captureFiles.forEach((file) => data.append("files", file, file.name));

    const progress = $("#capture-progress");
    const bar = $("#capture-progress-bar");
    const value = $("#capture-progress-value");
    const result = $("#capture-result");
    progress.classList.remove("hidden");
    result.classList.add("hidden");
    submit.disabled = true;
    submit.textContent = "Starting…";

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/captures");
    xhr.upload.addEventListener("progress", (upload) => {
      if (!upload.lengthComputable) return;
      const pct = Math.round((100 * upload.loaded) / upload.total);
      bar.value = pct;
      value.textContent = `${pct}%`;
    });
    xhr.addEventListener("load", async () => {
      let payload = {};
      try { payload = JSON.parse(xhr.responseText || "{}"); } catch { /* response handled below */ }
      if (xhr.status < 200 || xhr.status >= 300) {
        state.captureUploading = false;
        result.innerHTML = `<strong>Could not start this capture</strong><span>${escapeHtml(payload.error || `Server returned ${xhr.status}`)}</span>`;
        result.className = "capture-result error";
        submit.disabled = false;
        submit.textContent = "Create splat";
        return;
      }
      bar.value = 100;
      state.captureUploading = false;
      state.captureFiles = [];
      state.captureDraft = {};
      value.textContent = "100%";
      $("#capture-progress-label").textContent = "Media copied";
      const warnings = (payload.session && payload.session.warnings) || [];
      const warningHtml = warnings.length
        ? `<p class="capture-session-warnings">${warnings.map(item => escapeHtml(item)).join(" · ")}</p>`
        : "";
      result.innerHTML = `<strong>Processing has started</strong><span>${escapeHtml(payload.title)} is now preparing photographs. Open its workspace to follow the build.</span>${warningHtml}<button type="button" class="soft" id="btn-open-new-run">Open capture →</button>`;
      result.className = "capture-result success";
      submit.textContent = "Started";
      await loadRuns(false);
      state.libraryKind = form.elements.capture_type.value === "object" ? "object" : "scene";
      await openRun(payload.name);
    });
    xhr.addEventListener("error", () => {
      state.captureUploading = false;
      result.innerHTML = "<strong>Connection interrupted</strong><span>Check the Library before retrying: the server may already have started this capture.</span>";
      result.className = "capture-result error";
      submit.disabled = false;
      submit.textContent = "Create splat";
    });
    xhr.send(data);
  });
  const draft = state.captureDraft || {};
  $('#capture-title').value = draft.title || '';
  $('#capture-subject').value = draft.subject || '';
  if ($('#capture-quality')) $('#capture-quality').value = draft.quality || 'demo';
  form.elements.capture_type.value = draft.capture_type === 'object' ? 'object' : (state.libraryKind === 'object' ? 'object' : 'scene');
  const updateCaptureKind = () => {
    const object = form.elements.capture_type.value === 'object';
    $('#capture-heading').textContent = object ? 'Preserve an object, from every angle.' : 'Create a splat';
    $('#capture-description').textContent = object ? 'Add overlapping photographs or a video orbit of one stationary item. Watch camera positions and its 3D splat emerge as it builds.' : 'Add overlapping photographs and video of one place. Give the capture a name, then build its 3D splat.';
    $('#capture-title').placeholder = object ? 'e.g. Vintage radio — collection record' : 'e.g. Nested Cinema — final installation';
    $('#capture-note-media').innerHTML = object
      ? '<strong>Keep the object still. Move the camera.</strong><span>Walk around the object with overlapping views at low, middle and high angles. Include the top and visible details, keep focus and lighting consistent, and leave the background stationary. Do not rotate the object on a turntable or flip it between shots.</span><span>This creates an object-focused splat. Background geometry can remain; automatic isolation is a separate sidecar feature. Transparent, reflective or featureless surfaces may not reconstruct reliably.</span>'
      : '<strong>A little overlap goes a long way</strong><span>Move around the space, keep details in focus, and capture it from several angles. Processing stays on this computer.</span>';
  };
  form.querySelectorAll('[name="capture_type"]').forEach(input => input.addEventListener('change', updateCaptureKind));
  updateCaptureKind();
  setSourceMode(state.captureSourceMode || "media", false);
  form.addEventListener('input',()=>{state.captureDraft={capture_type:form.elements.capture_type.value,title:$('#capture-title').value,subject:$('#capture-subject').value,quality:$('#capture-quality')?.value || 'demo'};});
  if (state.captureFiles.length) setFiles(state.captureFiles);
}

/* ---------- library / runs list ---------- */

function teamsPanelHtml() {
  return `
    <section class="teams-panel library-teams" aria-label="Teams and partners">
      <p class="teams-label">Teams &amp; partners</p>
      <div class="teams-grid">
        <a class="team-card team-vitrine" href="/" title="Vitrine">
          <span class="team-logo team-logo-app"><img data-brand-asset="mark" src="/static/brand/vitrine-mark-${document.documentElement.dataset.theme || 'dark'}.svg" alt=""/></span>
          <span class="team-meta">
            <strong>Vitrine</strong>
            <em>Preservation pipeline</em>
          </span>
        </a>
        <a class="team-card team-xr" href="https://www.salford.ac.uk/" target="_blank" rel="noopener" title="XR Lab">
          <span class="team-logo"><img src="/static/brand/xr-lab.png" alt=""/></span>
          <span class="team-meta">
            <strong>XR Lab</strong>
            <em>Host studio</em>
          </span>
        </a>
        <a class="team-card team-uos" href="https://www.salford.ac.uk/" target="_blank" rel="noopener" title="University of Salford">
          <span class="team-logo plate-light"><img src="/static/brand/university-of-salford.png" alt=""/></span>
          <span class="team-meta">
            <strong>University of Salford</strong>
            <em>Institution</em>
          </span>
        </a>
        <a class="team-card team-dreamlab" href="https://thedreamlab.uk" target="_blank" rel="noopener" title="DreamLab AI">
          <span class="team-logo"><img src="/static/brand/dreamlab-ai.png" alt=""/></span>
          <span class="team-meta">
            <strong>DreamLab AI</strong>
            <em>Industry partner</em>
          </span>
        </a>
      </div>
    </section>`;
}

function renderRunsList() {
  if (!isAdv()) return renderStudioLibrary(studioContext());
  const runs = state.runs;
  const adv = isAdv();

  viewEl.innerHTML = `
    <div class="page-header">
      <div>
        <p class="page-kicker">${adv ? "runs/" : "Digital preservation"}</p>
        <h2>${adv ? "Runs" : "Library"}</h2>
        <p class="sub">
          ${
            adv
              ? "Pipeline outputs under <code>runs/</code> — inspect stages, metrics, and the splat viewer."
              : "Spaces and installations captured for the archive. Open any entry to review quality, explore the 3D model, and download files."
          }
        </p>
      </div>
      <div class="toolbar">
        <button type="button" id="btn-library-create" class="primary">Create a splat</button><button type="button" id="btn-refresh" class="ghost">${adv ? "Refresh" : "Refresh list"}</button>
      </div>
    </div>

    ${adv ? renderMissionBoard(runs) : ""}

    ${adv ? teamsPanelHtml() : ""}

    <div class="section-heading">
      <h3>${adv ? "Run list" : "Archives in this library"}</h3>
      <p>${
        adv
          ? "Click a run to inspect stages, artefacts, and the splat viewer."
          : "Open an entry to explore the model and review preservation progress."
      }</p>
    </div>

    ${
      runs.length === 0
        ? adv
          ? `<div class="card empty">
              <h3>No runs yet</h3>
              <p>Create one with:</p>
              <div class="hint-box">
                <code>python -m vitrine run --run-dir runs/my-capture --source source</code>
              </div>
            </div>`
          : `<div class="card empty">
              <h3>No archives yet</h3>
              <p>
                Add photographs or video of one space to start your first capture.
                Its progress and completed model will appear here.
              </p>
              <div class="hint-box">
                Choose Create a splat above to select media from this computer.

              </div>
            </div>`
        : `<div class="grid cols-2" id="run-list"></div>`
    }
  `;
  $("#btn-refresh")?.addEventListener("click", () => loadRuns(true));
  $("#btn-library-create")?.addEventListener("click", () => switchView("create"));

  const list = $("#run-list");
  if (!list) return;

  for (const run of runs) {
    const h = run.headline || {};
    const card = document.createElement("article");
    card.className = "card clickable run-card";
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute(
      "aria-label",
      `Open ${adv ? "run" : "archive"} ${displayName(run.name)}`
    );

    const fidelity = !adv ? fidelityLabel(h.psnr) : null;
    card.innerHTML = `
      <div class="title">
        <div>
          <strong>${escapeHtml(displayName(run.name))}</strong>
          ${archiveWhenHtml(run)}
          ${
            adv
              ? ""
              : `<div class="title-meta">${escapeHtml(run.name)}</div>`
          }
          ${
            adv && run.path
              ? `<div class="title-meta mono">${escapeHtml(run.path)}</div>`
              : ""
          }
        </div>
        ${progressBadge(run)}
      </div>
      <div class="pipeline">${stagePills(run)}</div>
      <div class="stats-row">
        ${
          h.psnr != null
            ? adv
              ? `<span>PSNR <b>${fmt(h.psnr, 2)} dB</b></span>`
              : `<span><span class="tip" data-tip="Peak Signal-to-Noise Ratio — how closely rendered views match held-out photographs. Higher is better.">Image fidelity</span> <b>${fmt(h.psnr, 1)}</b></span>`
            : ""
        }
        ${
          h.ssim != null
            ? adv
              ? `<span>SSIM <b>${fmt(h.ssim, 3)}</b></span>`
              : `<span><span class="tip" data-tip="Structural Similarity — how well edges and textures are preserved. Closer to 1.0 is better.">Structure</span> <b>${fmt(h.ssim, 3)}</b></span>`
            : ""
        }
        ${
          h.n_gaussians != null
            ? `<span>${adv ? "Gaussians" : "Model detail"} <b>${fmt(h.n_gaussians)}</b></span>`
            : ""
        }
        ${
          h.accepted_images != null
            ? `<span>${adv ? "Images" : "Photographs"} <b>${fmt(h.accepted_images)}</b></span>`
            : ""
        }
        ${
          adv && h.registered_images != null
            ? `<span>Registered <b>${fmt(h.registered_images)}</b></span>`
            : ""
        }
        ${
          h.profile
            ? `<span class="badge profile">${escapeHtml(profileLabel(h.profile))}</span>`
            : ""
        }
      </div>
      ${fidelity ? `<p class="metric-hint" style="margin:0.75rem 0 0">${escapeHtml(fidelity)}</p>` : ""}
    `;

    const open = () => openRun(run.name);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });
    list.appendChild(card);
  }
}

async function openRun(name) {
  state.studioTab = "splat";
  state.view = "run";
  document.body.classList.add("run-workspace-active");
  stopLivePoll();
  viewEl.innerHTML = `<div class="loading">${isAdv() ? `Loading ${escapeHtml(name)}…` : "Opening archive…"}</div>`;
  try {
    const detail = await api(`/api/runs/${encodeURIComponent(name)}`);
    state.selected = detail;
    state.libraryKind = detail.capture_type === "object" ? "object" : "scene";
    setNav(state.libraryKind === "object" ? "objects" : "runs");
    applySidebar();
    renderRunDetail(detail);
    showFlash(null);
    if (detail.headline?.running || detail.object_workflow?.running || detail.capture_job?.running || ["running","unknown"].includes(detail.object_meshes?.status?.state)) {
      loadLog(name, detail.object_workflow?.running ? "objects" : "train");
      startLivePoll(name);
    }
  } catch (err) {
    showFlash(err);
    state.view = "runs";
    renderRunsList();
  }
}

async function controlRun(name, action) {
  const label = action === "cancel" ? "Cancellation" : action === "resume" ? "Resume" : "Refresh";
  try {
    const result = await postApi(`/api/runs/${encodeURIComponent(name)}/${action}`);
    if (action === "cancel") {
      showFlash(null);
    }
    return result;
  } catch (error) {
    showFlash(new Error(`${label} failed: ${error.message}`));
    throw error;
  }
}

async function runRecoveryAction(name, action) {
  const button = document.querySelector(`#btn-${action}-run`);
  if (button) button.disabled = true;
  try {
    await controlRun(name, action);
    await openRun(name);
  } catch {
    if (button) button.disabled = false;
  }
}

function startLivePoll(name) {
  stopLivePoll();
  liveTimer = setInterval(async () => {
    if (state.view !== "run" || state.selected?.name !== name) {
      stopLivePoll();
      return;
    }
    try {
      const detail = await api(`/api/runs/${encodeURIComponent(name)}`);
      state.selected = detail;
      renderRunDetail(detail);
      if (detail.headline?.running) loadLog(name, "train");
      if (!detail.headline?.running && !detail.object_workflow?.running && !detail.capture_job?.running && !["running","unknown"].includes(detail.object_meshes?.status?.state)) stopLivePoll();
    } catch {
      /* transient */
    }
  }, 4000);
}

/* ---------- run detail ---------- */

function objectSeparationHtml(run, adv) {
  const flow = run.object_workflow || {};
  const outputs = flow.outputs;
  const meshOutputs = run.object_meshes || {};
  const meshById = new Map((meshOutputs.objects || []).map((mesh) => [mesh.object_id, mesh]));
  if (!adv && !flow.configured && !outputs && !flow.running) return "";
  const inputs = (flow.inputs || []).map((item) => `
    <a class="object-input" href="${item.url}" download>
      <span>${escapeHtml(item.role)}</span>
      <strong>${escapeHtml(item.name)}</strong>
      <em>${fmtBytes(item.bytes)}</em>
    </a>`).join("");
  const objectCards = (outputs?.objects || []).map((item) => {
    const mesh = meshById.get(item.object_id);
    const evidence = item.evidence?.items || [];
    const identity = item.evidence?.identity || {};
    const observations = item.evidence?.observations || [];
    const identityText = Object.entries(identity)
      .map(([key, value]) => `${key}=${value}`)
      .join(" · ");
    const observedIdentity = Object.entries(observations[0] || {})
      .filter(([key]) => ["image_id", "camera_id", "instance_id", "detection_id", "source_frame_id"].includes(key))
      .map(([key, value]) => `${key}=${value}`)
      .join(" · ");
    const observationText = observations.length
      ? `${observations.length} registered mask/frame association${observations.length === 1 ? "" : "s"}${observedIdentity ? ` · ${observedIdentity}` : identityText ? ` · ${identityText}` : ""}`
      : identityText;
    const evidenceHtml = evidence.length
      ? `<div class="object-evidence" aria-label="Source evidence">${evidence.map((entry) => `<a href="${escapeHtml(entry.url)}" target="_blank" rel="noopener">${escapeHtml(entry.label)} ↗</a>`).join(" ")}</div>`
      : `<span class="object-evidence-missing">No copied source or mask evidence</span>`;
    const observedCandidate = item.asset_type === "gaussian-splat" || !!item.splat_url;
    const meshMarker = `/files/${encodeURIComponent(run.name)}/`;
    let meshAsset = "";
    if (mesh?.glb_url?.startsWith(meshMarker)) {
      try { meshAsset = decodeURIComponent(mesh.glb_url.slice(meshMarker.length)); } catch { meshAsset = ""; }
    }
    const meshLink = meshAsset
      ? `<a href="/static/mesh-viewer.html?run=${encodeURIComponent(run.name)}&asset=${encodeURIComponent(meshAsset)}&label=${encodeURIComponent(item.label || item.object_id || "Object")}" target="_blank" rel="noopener">Inspect reconstructed GLB ↗</a><a href="${escapeHtml(mesh.glb_url)}" download>Download GLB ↓</a>`
      : mesh?.url
        ? `<a href="${escapeHtml(mesh.url)}" download>Download reconstructed PLY ↓</a>`
        : "";
    return `
    <article class="object-result">
      <div class="object-thumb ${item.thumb_url ? "" : "object-thumb-empty"}">
        ${item.thumb_url
          ? `<img src="${escapeHtml(item.thumb_url)}" alt="Source evidence for ${escapeHtml(item.label || "object")}" loading="lazy"/>`
          : `<span aria-hidden="true">◇</span>`}
      </div>
      <div class="object-result-copy">
        <strong>${escapeHtml(item.label || item.object_id || "Unlabelled object")}</strong>
        <span>${item.confidence != null ? `${fmt(item.confidence * 100, 0)}% carve consistency` : "Validated output"}${item.coverage != null ? ` · ${fmt(item.coverage * 100, 0)}% coverage` : ""}</span>
        ${item.thumb_kind === "source-crop" ? `<small class="object-source-note">Source crop · evidence preview, not a mesh render</small>` : ""}
        ${observationText ? `<small class="object-source-note mono">${escapeHtml(observationText)}</small>` : ""}
        ${evidenceHtml}
        ${item.splat_url ? `<a href="${escapeHtml(item.viewer_url || "#")}" target="_blank" rel="noopener">Inspect observed splat ↗</a>` : ""}
        ${observedCandidate && !meshLink ? `<button type="button" class="soft" data-object-mesh="${escapeHtml(item.object_id || "")}" ${meshOutputs.status?.state === "running" ? "disabled" : ""}>Reconstruct surface</button>` : ""}
        ${meshLink}
      </div>
    </article>`;
  }).join("");
  const sidecarReady = flow.configured && flow.executable_available !== false && !flow.configuration_error;
  const status = flow.running ? "Separating objects…" : outputs
    ? `${fmt(outputs.count)} object${outputs.count === 1 ? "" : "s"} separated`
    : !flow.configured ? "Sidecar not connected" : !sidecarReady ? "Sidecar unavailable" : !flow.source_ready && flow.missing_inputs?.length ? "Inputs incomplete" : flow.ready ? "Ready to separate" : "3D model required";
  const disabled = !flow.ready || flow.source_ready === false || !sidecarReady || flow.running;
  const reason = flow.configuration_error
    ? flow.configuration_error
    : !flow.configured
      ? "Set VITRINE_OBJECT_SIDECAR before starting the dashboard to connect the external object model."
      : flow.source_ready === false
        ? `Required source evidence is missing: ${(flow.missing_inputs || []).join(", ")}.`
      : "The external sidecar reads this run in place and publishes only validated, checksummed 3D outputs.";
  return `
    <section class="card object-workbench section-gap" aria-labelledby="object-separation-title">
      <div class="object-workbench-head">
        <div>
          <p class="page-kicker">${adv ? "Optional sidecar stage" : "From room to collection"}</p>
          <h3 id="object-separation-title" class="serif">Object separation</h3>
          <p>${escapeHtml(reason)}</p>
        </div>
        <div class="object-action">
          <span class="object-status ${flow.running ? "running" : outputs ? "done" : ""}">${escapeHtml(status)}</span>
          <button type="button" class="primary" id="btn-separate-objects" ${disabled ? "disabled" : ""}>
            ${outputs ? "Separate again" : "Separate objects"}
          </button>
        </div>
      </div>
      <div class="object-flow" aria-label="Object separation data flow">
        <div class="object-flow-stage">
          <span class="object-flow-label">Input reconstruction</span>
          <div class="object-inputs">${inputs || "<span class=\"faint\">No 3D output yet</span>"}</div>
        </div>
        <span class="object-flow-arrow" aria-hidden="true">→</span>
        <div class="object-flow-stage object-flow-results">
          <span class="object-flow-label">Separated 3D assets</span>
          ${objectCards ? `<div class="object-results">${objectCards}</div>` : `<p class="object-empty">Run separation to recover individual models from the preserved scene.</p>`}
        </div>
      </div>
      ${outputs?.composed_scene ? `<div class="composed-scene-link"><span>Composed scene</span><a href="${outputs.composed_scene.url}" download>Download placed GLB</a><em>Observed scene + generated object derivatives</em></div>` : ""}
      ${flow.running ? `<div class="object-progress" role="status"><i></i><span>The sidecar is processing locally. This view updates every four seconds.</span></div>` : ""}
      <div class="object-feedback" id="object-feedback" aria-live="polite"></div>
      ${meshOutputs.status?.state === "running" ? `<div class="object-progress" role="status"><i></i><span>Surface reconstruction is running locally. The selected object remains available as an observed splat.</span></div>` : ""}
      ${meshOutputs.status?.state === "unknown" ? `<p class="object-feedback" role="status">Surface reconstruction state is unknown; inspect the log before retrying.</p>` : ""}
    </section>`;
}

function renderRunDetail(run) {
  if (!isAdv()) return renderStudioDetail(run, studioContext());
  const adv = isAdv();
  const h = run.headline || {};
  const stages = run.stages || {};
  const art = run.artefacts || {};
  const order = run.progress?.order || Object.keys(stages);

  const stageDetail = (name) => {
    const s = stages[name] || {};
    const r = s.report || {};
    const meta = stageMeta(name);

    if (name === "ingest" && s.done) {
      if (adv) {
        const groups = (r.groups || [])
          .map((g) => `${g.name}: ${g.count} @ ${g.width}×${g.height}`)
          .join(" · ");
        return `accepted ${fmt(r.accepted)} · rejected ${fmt(r.rejected)}${groups ? " · " + groups : ""}`;
      }
      const groups = (r.groups || [])
        .map((g) => `${g.name}: ${g.count} photographs`)
        .join(" · ");
      return `Kept ${fmt(r.accepted)} photographs`
        + (r.rejected ? ` · set aside ${fmt(r.rejected)} (blurry or unsuitable)` : "")
        + (groups ? ` · ${groups}` : "");
    }
    if (name === "sfm" && s.done) {
      if (adv) {
        return `${fmt(r.registered_images)} images · ${fmt(r.cameras)} camera(s) · ${fmt(r.points)} points`
          + (r.used_gpu != null ? ` · GPU ${r.used_gpu ? "yes" : "no"}` : "");
      }
      return `${fmt(r.registered_images)} photographs placed in space`
        + (r.cameras != null ? ` · ${fmt(r.cameras)} camera setup(s)` : "")
        + (r.points != null ? ` · ${fmt(r.points)} reference points` : "");
    }
    if (name === "train" && s.running) {
      if (adv) {
        return `${escapeHtml(r.profile || "training")} · step ${fmt(r.step)}/${fmt(r.iterations)} · `
          + `${fmt(r.n_gaussians)} Gaussians · loss ${fmt(r.loss, 4)} · SSIM ${fmt(r.ssim, 3)}`
          + (r.eta_minutes != null ? ` · ETA ${fmt(r.eta_minutes, 0)} min` : "");
      }
      const pct = r.iterations
        ? Math.min(100, Math.round((100 * (r.step || 0)) / r.iterations))
        : null;
      return `Building… step ${fmt(r.step)} of ${fmt(r.iterations)}`
        + (pct != null ? ` (${pct}%)` : "")
        + (r.eta_minutes != null ? ` · about ${fmt(r.eta_minutes, 0)} min remaining` : "");
    }
    if (name === "train" && s.interrupted) {
      const pct = r.iterations
        ? Math.min(100, Math.round((100 * (r.step || 0)) / r.iterations))
        : null;
      if (adv) {
        return `interrupted at step ${fmt(r.step)}/${fmt(r.iterations)}`
          + (pct != null ? ` (${pct}%)` : "")
          + (r.n_gaussians != null ? ` · ${fmt(r.n_gaussians)} Gaussians` : "")
          + (s.progress_age_seconds != null
            ? ` · progress.json age ${fmt(Math.round(s.progress_age_seconds / 60), 0)} min`
            : "")
          + " · no train.json";
      }
      return `Stopped mid-build at step ${fmt(r.step)} of ${fmt(r.iterations)}`
        + (pct != null ? ` (${pct}%)` : "")
        + " — not finished, not currently running";
    }
    if (name === "train" && s.done) {
      if (adv) {
        return `${escapeHtml(r.profile || "trained")} · ${fmt(r.iterations)} iters · `
          + `${fmt(r.n_gaussians)} Gaussians · ${fmt(r.minutes, 1)} min · peak ${fmt(r.peak_vram_gb, 2)} GB`;
      }
      return `Finished in ${fmt(r.minutes, 1)} minutes`
        + (r.n_gaussians != null ? ` · ${fmt(r.n_gaussians)} detail elements` : "")
        + (r.profile ? ` · ${friendlyProfile(r.profile)}` : "");
    }
    if (name === "evaluate" && s.done) {
      return adv ? "evaluation.json present" : "Held-out photograph comparison saved";
    }
    if (name === "package" && s.done) {
      return adv
        ? "archive/manifest.json present"
        : "Preservation package with originals, poses, model, and manifest";
    }
    if (name === "view" && s.done) {
      if (adv) return s.has_splat ? "scene.splat ready" : "viewer folder present";
      return s.has_splat ? "Interactive 3D model available below" : "Viewer folder present";
    }
    return s.done ? (meta.done || "Complete") : (meta.todo || "Not run yet");
  };

  const journey = order
    .map((name, idx) => {
      const done = stages[name]?.done;
      const running = stages[name]?.running;
      const interrupted = stages[name]?.interrupted;
      const statusClass = done
        ? "done"
        : running
          ? "running"
          : interrupted
            ? "interrupted"
            : "todo";
      const statusText = adv
        ? done
          ? "done"
          : running
            ? "running…"
            : interrupted
              ? "interrupted"
              : "—"
        : done
          ? "Done"
          : running
            ? "In progress"
            : interrupted
              ? "Stopped"
              : "Pending";
      const meta = stageMeta(name);
      const goal = pipelineGoal(name);
      const marker = done ? "✓" : running ? "…" : interrupted ? "!" : String(idx + 1);
      return `
        <div class="journey-step ${statusClass}">
          <div class="journey-marker" aria-hidden="true">${marker}</div>
          <div class="journey-body">
            <div class="name">${escapeHtml(meta.title)}</div>
            <div class="goal-line">
              <span class="goal-aim-label">${adv ? "goal" : "Aim"}</span>
              ${escapeHtml(goal.goal)}
            </div>
            <div class="detail">${stageDetail(name)}</div>
          </div>
          <div class="journey-status ${statusClass}">${statusText}</div>
        </div>`;
    })
    .join("");

  const fileRows = Object.entries(art)
    .filter(([, info]) => info)
    .map(([key, info]) => {
      const meta = artefactMeta(key);
      const fallback = {
        scene_ply: `/files/${run.name}/model/scene.ply`,
        scene_splat: `/files/${run.name}/model/scene.splat`,
        checkpoint: `/files/${run.name}/model/checkpoint_10000.ply`,
        train_json: `/files/${run.name}/model/train.json`,
        ingest_json: `/files/${run.name}/ingest/ingest.json`,
        sfm_json: `/files/${run.name}/sfm/sfm.json`,
      };
      const href = fallback[key] || null;
      return `
        <tr>
          <td class="role">
            ${escapeHtml(meta.role)}
            ${meta.note ? `<span class="file-note">${escapeHtml(meta.note)}</span>` : ""}
          </td>
          <td>${href ? `<a href="${href}" download>${escapeHtml(info.name)}</a>` : escapeHtml(info.name)}</td>
          <td>${fmtBytes(info.bytes)}</td>
        </tr>`;
    })
    .join("");

  const samples = (run.samples || [])
    .map(
      (s) => `
      <figure>
        <img src="${s.url}" alt="${escapeHtml(s.name)}" loading="lazy"/>
        <figcaption>${escapeHtml(s.group)} · ${escapeHtml(s.name)}</figcaption>
      </figure>`
    )
    .join("");

  const history = stages.train?.report?.history || [];
  const fidelity = !adv ? fidelityLabel(h.psnr) : null;

  const liveBanner = h.running
    ? adv
      ? `<div class="live-banner" role="status">
           <span class="live-dot" aria-hidden="true"></span>
           Training live — step ${fmt(h.step)}/${fmt(stages.train?.report?.iterations)}
           ${h.eta_minutes != null ? ` · ETA ${fmt(h.eta_minutes, 0)} min` : ""}
           · refreshes every 4s
         </div>`
      : `<div class="live-banner" role="status">
           <span class="live-dot" aria-hidden="true"></span>
           Building the 3D model — step ${fmt(h.step)} of ${fmt(stages.train?.report?.iterations)}
           ${h.eta_minutes != null ? ` · about ${fmt(h.eta_minutes, 0)} minutes remaining` : ""}
           · this page updates automatically
         </div>`
    : h.interrupted && !recoveryState(run).stale
      ? adv
        ? `<div class="live-banner interrupted" role="status">
             Training interrupted at step ${fmt(h.step)}/${fmt(stages.train?.report?.iterations)}
             — progress.json is stale (no live process). Resume with
             <span class="mono">vitrine train</span> or ignore this run.
           </div>`
        : `<div class="live-banner interrupted" role="status">
             This build stopped partway (step ${fmt(h.step)} of ${fmt(stages.train?.report?.iterations)})
             and is not running now. The 3D model was not finished.
           </div>`
      : "";
  const recoveryBanner = recoveryBannerHtml(run, adv);

  viewEl.innerHTML = `
    ${liveBanner}
    ${recoveryBanner}

    <div class="page-header">
      <div>
        <button type="button" class="back" id="btn-back">${adv ? "← Runs" : "← Back to library"}</button>
        <h2 style="margin-top:0.45rem">${escapeHtml(displayName(run.name))}</h2>
        ${archiveWhenHtml(run)}
        <p class="sub">
          ${
            adv
              ? `<span class="mono">${escapeHtml(run.path || run.name)}</span>`
              : `Archive folder: <span class="mono faint">${escapeHtml(run.name)}</span>`
          }
        </p>
      </div>
      <div class="toolbar">
        ${
          run.has_viewer
            ? `<a class="btn-primary" style="display:inline-flex;align-items:center;padding:0.55rem 1rem;border-radius:10px;background:var(--orange);color:#140c05;font-weight:600;text-decoration:none;box-shadow:0 2px 12px var(--orange-glow)" href="${run.viewer_url}" target="_blank" rel="noopener">${adv ? "Open splat viewer ↗" : "Explore in 3D ↗"}</a>`
            : ""
        }
        ${
          adv
            ? `<button type="button" id="btn-log-train" class="ghost">Train log</button>
               <button type="button" id="btn-log-sfm" class="ghost">SfM log</button>
               ${recoveryControlsHtml(run, adv)}`
            : ""
        }
      </div>
    </div>

    <div class="grid cols-4" style="margin-bottom:1.15rem">
      <div class="card">
        <h3>${
          adv
            ? "PSNR"
            : `<span class="tip" data-tip="Peak Signal-to-Noise Ratio (PSNR). Higher means the 3D views match the original photographs more closely. Values around 25–35 dB are typical for good captures.">Image fidelity</span>`
        }</h3>
        <div class="metric">${h.psnr != null ? fmt(h.psnr, adv ? 2 : 1) : "—"}</div>
        <div class="metric-label">${
          h.psnr != null
            ? adv
              ? "dB held-out"
              : "dB · higher is better"
            : adv
              ? "—"
              : "Not measured yet"
        }</div>
        ${fidelity ? `<span class="metric-hint">${escapeHtml(fidelity)}</span>` : ""}
      </div>
      <div class="card">
        <h3>${
          adv
            ? "SSIM"
            : `<span class="tip" data-tip="Structural Similarity Index (SSIM). Measures how well textures, edges, and structure match the photographs. 1.0 is a perfect match.">Structure score</span>`
        }</h3>
        <div class="metric">${h.ssim != null ? fmt(h.ssim, 3) : "—"}</div>
        <div class="metric-label">${
          h.ssim != null
            ? adv
              ? "held-out"
              : "0–1 · closer to 1 is better"
            : adv
              ? "—"
              : "Not measured yet"
        }</div>
      </div>
      <div class="card">
        <h3>${adv ? "Gaussians" : "Model detail"}</h3>
        <div class="metric sm">${fmt(h.n_gaussians)}</div>
        <div class="metric-label">${
          h.profile
            ? escapeHtml(profileLabel(h.profile))
            : adv
              ? "cap"
              : "Elements in the 3D model"
        }</div>
      </div>
      <div class="card">
        <h3>${adv ? "Train time" : "Time to build"}</h3>
        <div class="metric sm">${h.minutes != null ? fmt(h.minutes, 1) + " min" : "—"}</div>
        <div class="metric-label">
          ${
            adv
              ? `peak VRAM ${h.peak_vram_gb != null ? fmt(h.peak_vram_gb, 2) + " GB" : "—"}`
              : h.accepted_images != null
                ? `${fmt(h.accepted_images)} photographs used`
                : "Duration of the build step"
          }
        </div>
      </div>
      <div class="card">
        <h3>${
          adv
            ? "Energy"
            : `<span class="tip" data-tip="Estimated electricity used while building the 3D model, sampled from live GPU power draw, at your configured £/kWh rate (VITRINE_ELECTRICITY_RATE_GBP).">Electricity cost</span>`
        }</h3>
        <div class="metric sm">${h.cost_gbp != null ? "£" + fmt(h.cost_gbp, 2) : "—"}</div>
        <div class="metric-label">${h.energy_kwh != null
          ? `${fmt(h.energy_kwh, 2)} kWh${h.electricity_rate_gbp_per_kwh != null ? ` · ${fmt(h.electricity_rate_gbp_per_kwh * 100, 2)}p/kWh` : ""}`
          : adv ? "—" : "Not measured yet"}</div>
      </div>
    </div>

    ${
      run.has_viewer
        ? `<div class="card viewer-card section-gap">
            <div class="viewer-head">
              <div>
                <h3>${adv ? "Splat preview · scene.splat" : "Walk through the space"}</h3>
                <p>${adv ? "GaussianSplats3D / point fallback" : "Drag to look around · scroll to move closer · right-drag to pan"}</p>
              </div>
              <a href="${run.viewer_url}" target="_blank" rel="noopener" class="soft" style="text-decoration:none;display:inline-flex;padding:0.45rem 0.85rem;border-radius:8px">${adv ? "Fullscreen ↗" : "Open full screen ↗"}</a>
            </div>
            <iframe
              class="viewer-frame"
              src="${run.viewer_url}?v=${(run.artefacts?.scene_splat?.mtime) || Date.now()}"
              title="${adv ? "Splat viewer" : "Three-dimensional model of " + escapeHtml(prettyName(run.name))}"
              allow="fullscreen"
            ></iframe>
            ${
              adv
                ? ""
                : `<div class="viewer-hint">
                    <span>Interactive 3D reconstruction</span>
                    <span>Based on your original photographs</span>
                    <span>Local only — nothing leaves this computer</span>
                  </div>`
            }
          </div>`
        : `<div class="card section-gap empty" style="padding:2rem">
            <h3>${adv ? "No scene.splat" : "3D viewer not ready yet"}</h3>
            <p>${adv ? "Train (or export) to produce model/scene.splat." : "Once the model has been built, you will be able to explore the space here."}</p>
          </div>`
    }

    ${objectSeparationHtml(run, adv)}

    <div class="grid cols-2 section-gap">
      <div class="card">
        <h3 class="serif">${adv ? "Pipeline goals &amp; status" : "Goals &amp; progress"}</h3>
        <div class="journey">${journey}</div>
      </div>
      <div class="card">
        <h3 class="serif">${adv ? "Training history" : "Quality over time"}</h3>
        ${
          history.length
            ? `${
                adv
                  ? ""
                  : `<p class="muted" style="margin:0 0 0.5rem;font-size:0.88rem">
                       How closely the model matched the photographs as it was refined.
                     </p>`
              }
               <div class="chart-wrap"><canvas id="hist-chart"></canvas></div>
               <div class="chart-legend">
                 <span class="psnr"><i></i>${adv ? "PSNR" : "Image fidelity"}</span>
                 <span class="ssim"><i></i>${adv ? "SSIM" : "Structure score"}</span>
               </div>
               ${
                 adv
                   ? `<div class="stats-row" style="margin-top:0.75rem">
                        <span>steps <b>${history.map((x) => x.step).join(" · ")}</b></span>
                      </div>`
                   : ""
               }`
            : `<div class="empty" style="padding:1.5rem 0.5rem">
                 <p>${adv ? "No history in train.json" : "Quality measurements appear here after the model has been built."}</p>
               </div>`
        }
      </div>
    </div>

    <div class="grid cols-2 section-gap">
      <div class="card">
        <h3 class="serif">${adv ? "Artefacts" : "Archive files"}</h3>
        ${
          fileRows
            ? `<table class="data">
                <thead><tr><th>${adv ? "Role" : "What it is"}</th><th>File</th><th>Size</th></tr></thead>
                <tbody>${fileRows}</tbody>
              </table>`
            : `<div class="empty" style="padding:1rem"><p>${adv ? "None yet" : "No files yet."}</p></div>`
        }
      </div>
      <div class="card">
        <h3 class="serif">${adv ? "Ingest samples" : "Source photographs"}</h3>
        ${
          samples
            ? `<div class="samples">${samples}</div>`
            : `<div class="empty" style="padding:1rem"><p>${adv ? "No staged images" : "No sample photographs available."}</p></div>`
        }
      </div>
    </div>

    ${
      adv
        ? `<div class="card section-gap" id="log-card">
            <h3 class="serif">Log tail</h3>
            <div class="toolbar" style="margin-bottom:0.65rem">
              <button type="button" id="btn-log-train-panel" class="ghost">Train log</button>
              <button type="button" id="btn-log-sfm-panel" class="ghost">SfM log</button>
            </div>
            <div class="log-view" id="log-view">Select Train log or SfM log.</div>
            <p class="faint" style="margin:0.75rem 0 0;font-size:0.8rem">
              path: <span class="mono">${escapeHtml(run.path || run.name)}</span>
            </p>
          </div>`
        : `<details class="advanced-panel section-gap">
            <summary>Technical details for specialists</summary>
            <div class="advanced-body">
              <p class="muted" style="margin:0.85rem 0 0;font-size:0.88rem">
                Processing logs and raw path information. Or turn on <strong>Advanced view</strong> in the sidebar for the full technical UI.
              </p>
              <div class="toolbar">
                <button type="button" id="btn-log-train" class="ghost">Build log</button>
                <button type="button" id="btn-log-sfm" class="ghost">Camera-mapping log</button>
              </div>
              <div class="log-view" id="log-view">Choose a log above to inspect the technical record.</div>
              <p class="faint" style="margin:0.75rem 0 0;font-size:0.8rem">
                Path on this machine: <span class="mono">${escapeHtml(run.path || run.name)}</span>
              </p>
            </div>
          </details>`
    }
  `;

  if (run.has_viewer) {
    const viewer = viewEl.querySelector(":scope > .viewer-card");
    if (viewer) {
      const live = viewEl.querySelector(":scope > .live-banner");
      const workspace = document.createElement("div");
      const infoColumn = document.createElement("section");
      const viewerColumn = document.createElement("aside");
      workspace.className = "run-detail-workspace";
      infoColumn.className = "run-info-column";
      viewerColumn.className = "run-viewer-column";
      viewerColumn.setAttribute("aria-label", "Interactive splat viewer");
      Array.from(viewEl.children).forEach((child) => {
        if (child !== live && child !== viewer) infoColumn.appendChild(child);
      });
      viewerColumn.appendChild(viewer);
      workspace.append(infoColumn, viewerColumn);
      viewEl.appendChild(workspace);
    }
  }

  $("#btn-back")?.addEventListener("click", () => {
    stopLivePoll();
    document.body.classList.remove("run-workspace-active");
    state.selected = null;
    switchView(state.libraryKind === "object" ? "objects" : "runs");
  });
  $("#btn-log-train")?.addEventListener("click", () => loadLog(run.name, "train"));
  $("#btn-log-sfm")?.addEventListener("click", () => loadLog(run.name, "sfm"));
  $("#btn-log-train-panel")?.addEventListener("click", () => loadLog(run.name, "train"));
  $("#btn-log-sfm-panel")?.addEventListener("click", () => loadLog(run.name, "sfm"));
  $("#btn-resume-run")?.addEventListener("click", () => runRecoveryAction(run.name, "resume"));
  $("#btn-cancel-run")?.addEventListener("click", () => runRecoveryAction(run.name, "cancel"));
  $("#btn-refresh-run")?.addEventListener("click", () => openRun(run.name));
  $("#btn-separate-objects")?.addEventListener("click", () => startObjectSeparation(run.name));
  viewEl.querySelectorAll("[data-object-mesh]").forEach((button) => {
    button.addEventListener("click", () => startObjectMesh(run.name, button.dataset.objectMesh));
  });

  if (history.length) drawHistoryChart($("#hist-chart"), history);
}

async function startObjectSeparation(name) {
  const button = $("#btn-separate-objects");
  const feedback = $("#object-feedback");
  if (button) button.disabled = true;
  if (feedback) feedback.textContent = "Starting local object separation…";
  try {
    const res = await fetch(`/api/runs/${encodeURIComponent(name)}/objects`, { method: "POST" });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.error || `Request failed (${res.status})`);
    if (feedback) feedback.textContent = "Object separation started.";
    const detail = await api(`/api/runs/${encodeURIComponent(name)}`);
    state.selected = detail;
    if (!isAdv()) state.studioTab = "objects";
    renderRunDetail(detail);
    loadLog(name, "objects");
    startLivePoll(name);
  } catch (err) {
    if (feedback) feedback.textContent = String(err.message || err);
    if (button) button.disabled = false;
  }
}

async function startObjectMesh(name, objectId) {
  const button = Array.from(document.querySelectorAll("[data-object-mesh]"))
    .find((candidate) => candidate.dataset.objectMesh === objectId);
  const feedback = document.querySelector("#object-mesh-feedback") || document.querySelector("#mesh-feedback");
  if (button) button.disabled = true;
  if (feedback) feedback.textContent = `Starting surface reconstruction for ${objectId}…`;
  try {
    const payload = await postApi(`/api/runs/${encodeURIComponent(name)}/object-meshes`, { object_id: objectId });
    if (feedback) feedback.textContent = `Surface reconstruction started for ${payload.object_id || objectId}.`;
    const detail = await api(`/api/runs/${encodeURIComponent(name)}`);
    state.selected = detail;
    if (!isAdv()) state.studioTab = "objects";
    renderRunDetail(detail);
    startLivePoll(name);
  } catch (error) {
    if (feedback) feedback.textContent = String(error.message || error);
    if (button) button.disabled = false;
  }
}

async function loadLog(name, which) {
  const el = $("#log-view");
  if (!el) return;
  el.textContent = "Loading…";
  try {
    const data = await api(`/api/runs/${encodeURIComponent(name)}/log?which=${which}`);
    el.textContent = data.tail || "(empty)";
    el.scrollTop = el.scrollHeight;
  } catch (err) {
    el.textContent = String(err.message || err);
  }
}

function drawHistoryChart(canvas, history) {
  if (!canvas) return;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = Math.floor(rect.width * dpr);
  canvas.height = Math.floor(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const w = rect.width;
  const h = rect.height;
  const pad = { t: 16, r: 12, b: 28, l: 36 };
  const plotW = w - pad.l - pad.r;
  const plotH = h - pad.t - pad.b;

  const steps = history.map((x) => x.step);
  const psnrs = history.map((x) => x.psnr);
  const minStep = Math.min(...steps);
  const maxStep = Math.max(...steps);
  const minP = Math.min(...psnrs);
  const maxP = Math.max(...psnrs);
  const pRange = Math.max(maxP - minP, 0.5);

  const xAt = (step) => pad.l + ((step - minStep) / Math.max(maxStep - minStep, 1)) * plotW;
  const yP = (v) => pad.t + plotH - ((v - minP) / pRange) * plotH;
  const yS = (v) => pad.t + plotH - v * plotH;

  ctx.strokeStyle = "#2c2c34";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (plotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + plotW, y);
    ctx.stroke();
  }

  ctx.strokeStyle = "#ff6a00";
  ctx.lineWidth = 2.25;
  ctx.lineJoin = "round";
  ctx.beginPath();
  history.forEach((pt, i) => {
    const x = xAt(pt.step);
    const y = yP(pt.psnr);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.strokeStyle = "#3ecf8e";
  ctx.lineWidth = 2.25;
  ctx.beginPath();
  history.forEach((pt, i) => {
    const x = xAt(pt.step);
    const y = yS(pt.ssim);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  history.forEach((pt) => {
    ctx.fillStyle = "#ff6a00";
    ctx.beginPath();
    ctx.arc(xAt(pt.step), yP(pt.psnr), 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#3ecf8e";
    ctx.beginPath();
    ctx.arc(xAt(pt.step), yS(pt.ssim), 4, 0, Math.PI * 2);
    ctx.fill();
  });

  ctx.fillStyle = "#6e6a62";
  ctx.font = "11px DM Sans, system-ui, sans-serif";
  ctx.fillText(String(minStep), pad.l, h - 8);
  ctx.fillText(String(maxStep), pad.l + plotW - 28, h - 8);
}

/* ---------- doctor ---------- */

function renderDoctor() {
  const d = state.doctor;
  const adv = isAdv();
  if (!d) {
    viewEl.innerHTML = `<div class="loading">${adv ? "Running doctor…" : "Checking this workstation…"}</div>`;
    return;
  }
  const cuda = d.cuda || {};
  const tools = d.tools || {};
  const gpu = d.gpu || {};

  const row = (k, v, ok) => `
    <div class="kv">
      <div class="k">${escapeHtml(k)}</div>
      <div class="v" style="${ok === false ? "color:var(--red)" : ok === true ? "color:var(--green)" : ""}">${v ?? "—"}</div>
    </div>`;

  if (adv) {
    viewEl.innerHTML = `
      <div class="page-header">
        <div>
          <p class="page-kicker">Environment</p>
          <h2>Doctor</h2>
          <p class="sub">Same checks as <code>python -m vitrine doctor</code> — can this machine run the pipeline?</p>
        </div>
        <div class="toolbar">
          <button type="button" id="btn-refresh-doctor" class="ghost">Re-check</button>
        </div>
      </div>
      <div class="card" style="margin-bottom:1rem">
        <div style="display:flex;align-items:center;gap:0.75rem;flex-wrap:wrap">
          <span class="badge ${d.ok ? "done" : "todo"}" style="${d.ok ? "" : "background:var(--red-dim);color:var(--red)"}">
            ${d.ok ? "All good" : "Problems found"}
          </span>
          <span class="muted">vitrine ${escapeHtml(d.version)} · tier <b>${escapeHtml(d.tier)}</b></span>
        </div>
      </div>
      <div class="doctor-grid">
        ${row("GPU", gpu.available ? `${escapeHtml(gpu.name)} (${gpu.vram_gb} GB, sm ${escapeHtml(String(gpu.capability))})` : escapeHtml(gpu.error || "not available"), !!gpu.available)}
        ${row("CUDA toolkit", escapeHtml(cuda.cuda_root || "NOT FOUND"), !!cuda.cuda_root)}
        ${row("Host compiler", escapeHtml(cuda.host_compiler || "NOT FOUND"), !!cuda.host_compiler)}
        ${row("Arch list", escapeHtml(cuda.arch_list || "—"))}
        ${row("gsplat prebuilt", cuda.gsplat_prebuilt ? "yes" : "no (will compile on first use)", cuda.gsplat_prebuilt !== false)}
        ${row("docker", escapeHtml(tools.docker || "NOT FOUND"), !!tools.docker)}
        ${row("docker GPU", tools.docker_gpu == null ? "—" : tools.docker_gpu ? "yes" : "no — COLMAP falls back to CPU", tools.docker_gpu === true)}
        ${row("ffmpeg", escapeHtml(tools.ffmpeg || "NOT FOUND"), !!tools.ffmpeg)}
      </div>
      ${
        d.profile_preview
          ? `<div class="card" style="margin-top:1rem">
              <h3>Default profile preview (${escapeHtml(d.profile_preview.name)})</h3>
              <div class="stats-row">
                <span>source <b>${fmt(d.profile_preview.source_long_edge)}</b></span>
                <span>crop <b>${fmt(d.profile_preview.crop)}</b></span>
                <span>cap <b>${fmt(d.profile_preview.cap_max)}</b></span>
                <span>iters <b>${fmt(d.profile_preview.iterations)}</b></span>
                <span>~min <b>${fmt(d.profile_preview.estimated_minutes, 0)}</b></span>
              </div>
            </div>`
          : ""
      }
    `;
  } else {
    viewEl.innerHTML = `
      <div class="page-header">
        <div>
          <p class="page-kicker">Setup</p>
          <h2>Workstation</h2>
          <p class="sub">
            A simple readiness check for this computer.
            Green means Vitrine can process photographs into a preservation archive here.
          </p>
        </div>
        <div class="toolbar">
          <button type="button" id="btn-refresh-doctor" class="ghost">Check again</button>
        </div>
      </div>

      <div class="status-banner ${d.ok ? "ok" : "bad"}">
        <div>
          <p class="title">${d.ok ? "This workstation is ready" : "Something needs attention"}</p>
          <p class="desc">
            ${
              d.ok
                ? "Graphics hardware, supporting tools, and the processing environment look good."
                : "One or more requirements are missing. A technician can use the details below to fix them — or turn on Advanced view."
            }
          </p>
        </div>
      </div>

      <div class="card" style="margin-bottom:1.15rem">
        <h3 class="serif">What we checked</h3>
        <div class="doctor-grid" style="margin-top:0.25rem">
          ${row(
            "Graphics hardware",
            gpu.available
              ? `${escapeHtml(gpu.name)} (${fmt(gpu.vram_gb, 1)} GB memory)`
              : escapeHtml(gpu.error || "Not available"),
            !!gpu.available
          )}
          ${row("Processing toolkit", cuda.cuda_root ? "Found" : "Not found", !!cuda.cuda_root)}
          ${row("Compiler for GPU code", cuda.host_compiler ? "Found" : "Not found", !!cuda.host_compiler)}
          ${row("Container tools (Docker)", !tools.docker ? "Not found" : tools.docker_engine ? "Running" : "Installed — start Docker Desktop / finish WSL setup", !!tools.docker_engine)}
          ${row(
            "GPU inside containers",
            tools.docker_gpu == null
              ? "—"
              : tools.docker_gpu
                ? "Available (faster camera mapping)"
                : "Not available — slower CPU fallback",
            tools.docker_gpu === true ? true : tools.docker_gpu === false ? false : null
          )}
          ${row("Video tools (ffmpeg)", tools.ffmpeg ? "Installed" : "Not found", !!tools.ffmpeg)}
        </div>
      </div>

      ${
        d.profile_preview
          ? `<div class="card">
              <h3 class="serif">Suggested default quality</h3>
              <p class="muted" style="margin:0 0 0.85rem;font-size:0.9rem">
                Based on this machine, the usual starting point is
                <b style="color:var(--orange-soft)">${escapeHtml(friendlyProfile(d.profile_preview.name))}</b>.
              </p>
              <div class="stats-row">
                <span>Photograph size <b>${fmt(d.profile_preview.source_long_edge)} px</b></span>
                <span>Detail budget <b>${fmt(d.profile_preview.cap_max)}</b></span>
                <span>Build steps <b>${fmt(d.profile_preview.iterations)}</b></span>
                <span>Typical time <b>~${fmt(d.profile_preview.estimated_minutes, 0)} min</b></span>
              </div>
            </div>`
          : ""
      }

      <details class="advanced-panel section-gap">
        <summary>Technical environment details</summary>
        <div class="advanced-body">
          <div class="doctor-grid" style="margin-top:1rem">
            ${row("Vitrine version", d.version)}
            ${row("Hardware tier", d.tier)}
            ${row("CUDA root", cuda.cuda_root || "—")}
            ${row("Host compiler path", cuda.host_compiler || "—")}
            ${row("GPU architecture list", cuda.arch_list || "—")}
            ${row("Prebuilt GPU kernels", cuda.gsplat_prebuilt ? "Yes" : "No (compiles on first use)")}
          </div>
        </div>
      </details>
    `;
  }

  $("#btn-refresh-doctor")?.addEventListener("click", async () => {
    await loadHealth();
    renderDoctor();
  });
}

/* ---------- profiles ---------- */

function renderProfiles() {
  const data = state.profiles;
  const adv = isAdv();
  if (!data) {
    viewEl.innerHTML = `<div class="loading">${adv ? "Loading profiles…" : "Loading quality options…"}</div>`;
    return;
  }

  const detected = data.detected_tier || "";
  const profiles = data.profiles || [];

  const byQuality = {};
  for (const p of profiles) {
    const q = p.quality || "standard";
    if (!byQuality[q]) byQuality[q] = [];
    byQuality[q].push(p);
  }

  const qualityOrder = ["demo", "draft", "standard", "archive"].filter((q) => byQuality[q]);
  for (const q of Object.keys(byQuality)) {
    if (!qualityOrder.includes(q)) qualityOrder.push(q);
  }

  const cards = qualityOrder
    .map((q) => {
      const match = byQuality[q].find((p) => p.tier === detected) || byQuality[q][0];
      const isRec = match.tier === detected && q === "demo";
      const title = q.charAt(0).toUpperCase() + q.slice(1);
      return `
        <article class="card quality-card ${isRec ? "recommended" : ""}">
          ${isRec ? `<span class="ribbon">${adv ? "default" : "Suggested"}</span>` : ""}
          <h3 class="q-title">${escapeHtml(title)}</h3>
          <div class="q-tier">${adv ? `tier ${escapeHtml(match.tier)}` : `Tuned for ${escapeHtml(match.tier)} hardware`}</div>
          <p class="q-blurb">${escapeHtml(
            adv
              ? `${match.name} · cap ${fmt(match.cap_max)} · crop ${fmt(match.crop)} · ~${fmt(match.estimated_minutes, 0)} min`
              : QUALITY_BLURB[q] || "Processing profile for this quality level."
          )}</p>
          ${(match.warnings || []).map(warning => `<p class="muted">${escapeHtml(warning)}</p>`).join("")}
          <div class="quality-stats">
            <div>
              <span class="k">${adv ? "~min" : "Typical time"}</span>
              <span class="v">~${fmt(match.estimated_minutes, 0)} min</span>
            </div>
            <div>
              <span class="k">${adv ? "cap_max" : "Detail budget"}</span>
              <span class="v">${fmt(match.cap_max)}</span>
            </div>
            <div>
              <span class="k">${adv ? "source_long_edge" : "Source size"}</span>
              <span class="v">${fmt(match.source_long_edge)} px</span>
            </div>
            <div>
              <span class="k">${adv ? "iterations" : "Build steps"}</span>
              <span class="v">${fmt(match.iterations)}</span>
            </div>
          </div>
        </article>`;
    })
    .join("");

  const tableRows = profiles
    .map(
      (p) => `
      <tr>
        <td><strong>${escapeHtml(adv ? p.name : friendlyProfile(p.name))}</strong></td>
        <td>${escapeHtml(p.tier)}</td>
        <td>${escapeHtml(p.quality)}</td>
        <td>${fmt(p.source_long_edge)}</td>
        ${adv ? `<td>${fmt(p.crop)}</td>` : ""}
        <td>${fmt(p.cap_max)}</td>
        <td>${fmt(p.iterations)}</td>
        <td>~${fmt(p.estimated_minutes, 0)}</td>
      </tr>`
    )
    .join("");

  viewEl.innerHTML = `
    <div class="page-header">
      <div>
        <p class="page-kicker">${adv ? "profiles.py" : "Choosing a level"}</p>
        <h2>${adv ? "Profiles" : "Quality guide"}</h2>
        <p class="sub">
          ${
            adv
              ? "Laptop / workstation × draft / standard / archive — numbers from measured benchmarks."
              : "How detailed should the preservation model be? Higher quality takes longer and uses more computer time — but keeps finer surface detail for the archive."
          }
        </p>
      </div>
    </div>

    <div class="card" style="margin-bottom:1.25rem">
      <p style="margin:0;color:var(--muted);font-size:0.95rem">
        ${adv ? "Detected tier on this machine:" : "This computer is classified as"}
        <b style="color:var(--orange-soft)">${escapeHtml(detected || "unknown")}</b>
        ${adv ? "" : " hardware. The cards below show what each quality level means in plain terms; times are estimates for a typical room capture."}
      </p>
    </div>

    <div class="grid cols-3" style="margin-bottom:1.25rem">
      ${cards}
    </div>

    ${
      adv
        ? `<div class="card">
            <h3>Full matrix</h3>
            <table class="data">
              <thead>
                <tr>
                  <th>Profile</th><th>Tier</th><th>Quality</th>
                  <th>Source</th><th>Crop</th><th>Cap</th><th>Iters</th><th>~min</th>
                </tr>
              </thead>
              <tbody>${tableRows}</tbody>
            </table>
          </div>`
        : `<details class="advanced-panel">
            <summary>Full comparison table</summary>
            <div class="advanced-body">
              <table class="data" style="margin-top:1rem">
                <thead>
                  <tr>
                    <th>Profile</th>
                    <th>Hardware</th>
                    <th>Quality</th>
                    <th>Source</th>
                    <th>Detail budget</th>
                    <th>Steps</th>
                    <th>~Minutes</th>
                  </tr>
                </thead>
                <tbody>${tableRows}</tbody>
              </table>
            </div>
          </details>`
    }
  `;
}

/* ---------- navigation ---------- */

function setNav(active) {
  document.querySelectorAll("#nav button").forEach((btn) => {
    const viewMatch = active != null && btn.dataset.view === active;
    const libraryMatch = !btn.dataset.library || btn.dataset.library === (state.libraryKind || "scene");
    btn.classList.toggle("active", viewMatch && (btn.dataset.view !== "runs" || libraryMatch));
  });
}

function constructionFrameSrc(extra = {}) {
  const params = new URLSearchParams({ hosted: "1" });
  if (isAdv()) params.set("advanced", "1");
  for (const [key, value] of Object.entries(extra)) {
    if (value != null && value !== "") params.set(key, value);
  }
  return `/static/construction.html?${params}`;
}

function loadConstructionFrame() {
  viewEl.innerHTML = `<iframe class="construction-frame" src="${constructionFrameSrc()}" title="Live reconstruction workspace" allow="fullscreen"></iframe>`;
}

function liveStageLabel(run) {
  const stages = run?.stages || {};
  if (!stages.ingest?.done) return "Preparing photographs";
  if (!stages.sfm?.done) return "Finding cameras";
  if (!stages.train?.done) return "Building the 3D model";
  if (!stages.evaluate?.done) return "Checking quality";
  return "Finishing archive";
}

function updateLiveJobChip(job) {
  const nav = $("#nav-construction");
  if (!nav) return;
  const run = job
    ? (state.runs || []).find((item) => item.name === job.run)
    : (state.runs || []).find((item) => item.capture_job?.running || item.headline?.running);
  const live = !!(job || run);
  nav.classList.toggle("is-live", live);
  const dot = nav.querySelector(".nav-live-dot");
  if (dot) dot.hidden = !live;
  const stage = $("#nav-construction-stage");
  if (stage) {
    stage.textContent = live
      ? (run ? liveStageLabel(run) : "Processing locally")
      : "Watch your capture take shape";
  }
  nav.title = live
    ? `${run?.title || displayName(job?.run || run?.name || "Capture")} · live construction`
    : "Live construction";
}

async function refreshLiveJobFromApi() {
  try {
    const listing = await api("/api/construction");
    const job = (listing.jobs || []).find((item) => item.state === "running");
    updateLiveJobChip(job || null);
  } catch {
    updateLiveJobChip();
  }
}

async function openSeparatedObject(runName, objectId) {
  const run = state.runs.find((item) => item.name === runName);
  let records = collectSeparatedObjects(run ? [run] : state.runs, studioContext());
  if (!records.length) {
    try {
      const detail = await api(`/api/runs/${encodeURIComponent(runName)}`);
      records = collectSeparatedObjects([detail], studioContext());
    } catch (err) {
      showFlash(err);
      return;
    }
  }
  const item = records.find((entry) => entry.object_id === objectId) || records[0];
  if (!item) {
    showFlash(new Error("That object is no longer available."));
    return;
  }
  state.view = "object";
  state.libraryKind = "object";
  state.selectedObject = item;
  document.body.classList.remove("run-workspace-active", "construction-active");
  setNav("objects");
  applySidebar();
  renderSeparatedObject(item, studioContext());
}

async function switchView(name) {
  if (state.captureUploading) { showFlash('Media is still being copied. Wait until processing starts before leaving this page.'); return; }
  state.presenting = false;
  document.body.classList.remove('presentation-mode');
  document.body.classList.toggle('construction-active', name === 'construction');
  stopLivePoll();
  document.body.classList.remove("run-workspace-active");
  state.selectedObject = null;
  state.view = name;
  if (name === "runs") state.libraryKind = "scene";
  if (name === "objects") state.libraryKind = "object";
  setNav(name);
  applySidebar();
  showFlash(null);
  if (name === "create") {
    renderCreate();
  } else if (name === "runs") {
    await loadRuns(false);
    renderRunsList();
  } else if (name === "objects") {
    await loadRuns(false);
    renderObjectLibrary(studioContext());
  } else if (name === "construction") {
    loadConstructionFrame();
  } else if (name === "doctor") {
    if (!state.doctor) await loadHealth();
    renderDoctor();
  } else if (name === "profiles") {
    if (!state.profiles) {
      try {
        state.profiles = await api("/api/profiles");
      } catch (err) {
        showFlash(err);
        return;
      }
    }
    renderProfiles();
  }
}

async function loadRuns(forceRender) {
  try {
    const data = await api("/api/runs");
    state.runs = data.runs || [];
    showFlash(null);
    updateLiveJobChip();
    if (forceRender && state.view === "objects") renderObjectLibrary(studioContext());
    else if (forceRender || state.view === "runs") renderRunsList();
  } catch (err) {
    showFlash(err);
  }
}

function bindNav() {
  document.querySelectorAll("#nav button").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.library) state.libraryKind = btn.dataset.library;
      switchView(btn.dataset.view);
    });
  });
}

/* ---------- boot ---------- */

function studioContext() {
  return {
    state,
    viewEl,
    displayName,
    fmt,
    fmtBytes,
    openRun,
    switchView,
    loadRuns,
    startObjectSeparation,
    startObjectMesh,
    controlRun,
    recoveryState,
    openSeparatedObject,
    applySidebar,
  };
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && state.presenting) {
    state.presenting = false;
    document.body.classList.remove('presentation-mode');
    applySidebar();
    const button = $('#studio-present');
    if (button) { button.textContent = 'Present'; button.setAttribute('aria-pressed', 'false'); }
  }
});
window.addEventListener('beforeunload', (event) => {
  if(state.captureUploading){event.preventDefault();event.returnValue='';}
});

state.advanced = loadMode();
state.sidebarCollapsed = loadSidebar();
bindModeToggle();
bindSidebar();
applyModeChrome();
bindNav();
loadHealth();
const initialView = new URLSearchParams(location.search).get("view");
const initialKind = new URLSearchParams(location.search).get("library");
if (initialKind === "object") state.libraryKind = "object";
switchView(["create", "construction", "objects"].includes(initialView) ? initialView : (state.libraryKind === "object" ? "objects" : "runs"));
setInterval(() => { if (!document.hidden) refreshLiveJobFromApi(); }, 8000);
