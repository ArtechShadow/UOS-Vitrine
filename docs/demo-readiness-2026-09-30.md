# Vitrine — demo readiness for 30 September 2026

**Status: AMBER — prepared-scene rehearsal ready; final demo sign-off pending.**

Audit date: **6 September 2026**. Target: **30 September 2026** (24 days away).
Scope: the current local Windows / RTX 5090 workspace, based on Git `856dce1`
with substantial uncommitted changes. This report replaces the initial assessment;
older observations remain in the linked investigation records.

The Images → 3D splat → Objects journey can demonstrate genuine preservation
work and measured reconstruction improvement. It cannot yet honestly promise a
fully accepted, live capture-to-clean-object workflow or game-engine delivery.
Use prepared scenes for the main presentation and label object separation as
experimental. “Ready to explore” means a viewing artifact exists, not that the
capture has passed quality, archive or object-isolation acceptance.

## Readiness by area

| Area | Status | Evidence and practical limit |
|---|---|---|
| Capture library and journey | Ready for rehearsal | 19 recognised runs visible through the live API; Images / Splat / Objects workspace, search, filtering and presentation controls implemented. |
| Rename, Delete and Restore | Checked | Rename preserves archive metadata; Delete requires confirmation and moves the run to recoverable Trash. HTTP lifecycle tested on a copy of real capture metadata, with byte-identical restoration. Browser forms and cancellation checked in the preceding session. Existing runs were not deleted. |
| Prepared 3D viewer | Ready for rehearsal | Master and video viewing artifacts available; source-camera viewpoints, reset and bundled renderer present. Final venue framing and display performance still need rehearsal. |
| SpaceMouse | Acceptance pending | User confirmed cap movement moves the scene. User then reported controls felt wrong. Slower movement, horizon lock and direction/turn-speed controls were added; those refinements have not been accepted by the user yet. |
| Video-only quality | Measured | Saved PLY: 31.006875 dB PSNR / 0.94697483 SSIM, 25 held-out views. One capture and seed; correlated video views. Browser derivative is not the scored PLY. |
| Master preservation package | Checksum pass | Archive verification rerun during this audit: all files verify against the manifest. Master still lacks separate canonical evaluation. This does not establish that every later sidecar output has been packaged. |
| Video preservation package | Missing | Live API reports evaluation complete, package absent. Do not call this run deposit-ready. |
| Object isolation | Not accepted | Six real SAM2.1 Gaussian candidates exist. Visual review found fragmentation and an incorrect source-crop/3D association. No accepted clean-object or generated GLB demo on this machine. |
| New capture through upload UI | Not fully rehearsed | File selection/drop and form flow checked previously; a complete new upload → processing → evaluation → package journey and failure recovery remain unverified. |
| Offline / fresh startup | Partially checked | Viewer libraries and fonts are local. Initial installation/model downloads and the separate sidecar environment are external prerequisites. Fresh browser with external networking blocked and venue cold start remain acceptance checks. |
| Stock workstation presets | Not validated for demo use | Draft, standard and archive nominal crop ratios are below 0.5. The successful custom HQ recipe does not validate those presets. The CLI default is archive. |
| Game-engine handoff | Outside current sign-off | This audit did not validate Unreal import, scene assembly or end-user engine interaction. These belong to the separate downstream workflow. |
| Release reproducibility | Pending | Demo changes are not a frozen commit/release; local models, captures and external sidecar installation are not supplied by a fresh checkout. |

## Quality evidence to present

| Evidence | Master | Video-only quality test |
|---|---|---|
| Capture | Mixed photographs / video / camera groups | IMG_6318.MOV only |
| Registered views / cameras | 736 / 5 | 200 / 1 |
| Sparse points | 404,570 | 108,865 |
| Trained Gaussian count | 2,000,000 | 1,632,975 |
| Reported PSNR / SSIM | 22.906 / 0.7781 | 31.006875 / 0.94697483 |
| Metric source | Stored export metrics | Separate saved-PLY evaluation |
| Recorded training time | 8.7 minutes | 4.3 minutes |
| Canonical evaluation / archive | Missing / present and verified | Present / missing |

These rows are different captures and evaluation sets, not an A/B quality ranking.
The controlled video comparison is appearance compensation ON versus OFF with
identical poses, split, seed and settings: **+4.440112 dB PSNR and +0.00574261
SSIM** for OFF. It won 24/25 held-out views on PSNR and 18/25 on SSIM.
That supports the new appearance-off default, not a universal quality guarantee.

Video ingest took 194.9 seconds and SfM 10.06 minutes before training; model
loading, export and evaluation add time. Do not describe the complete pipeline
as a four-minute reconstruction. Fine newspaper text and some peripheral
surfaces remain soft. Cleanup may improve presentation, but is not yet applied
or measured on a reviewed derivative.

## Checks performed in this audit

- Read README, CLI parser, profile definitions, dependency requirements and the
  current UI, quality and sidecar review records.
- Queried the active local API: 19 recognised runs; confirmed master/video
  counts, metric sources, evaluation/package states and six master candidates.
- Reran `python -m vitrine verify runs/nested-cinema-04-master/archive` in the
  project Python 3.11 environment: **all files verify against the manifest**.
- Ran real-server smoke checks: **2 passed, 1 skipped**. The missing-sidecar
  rejection check intentionally skipped because a sidecar is configured;
  this did not launch another separation run.
- Checked actual CLI help and argument placement. `run` executes ingest, SfM,
  training and packaging, but does not execute the separate `evaluate` command.
- Checked README local links and Markdown structure after editing.

This was a documentation/status audit. No new training, visual quality score,
venue rehearsal or full regression suite is claimed. Earlier browser acceptance
and real capture experiments are explicitly attributed to the linked records.

## Recommended demonstration — proposed 8-minute structure

Adjust the timing once the actual presentation slot is confirmed.

| Time | Show | Message |
|---|---|---|
| 0:00–1:00 | Master in the capture library | Preserve a temporary installation as a record of a moment. |
| 1:00–2:00 | Images and capture record | Real source material and recovered cameras underpin the result. |
| 2:00–4:00 | Saved room viewpoints, then explore | Show the scene clearly; use standard mouse controls if SpaceMouse tuning is not accepted. |
| 4:00–5:00 | Video-only experiment and matched evidence | Explain the measured improvement and distinguish training time from total time. |
| 5:00–6:00 | Master archive record | Originals, poses and checksums make the result auditable. |
| 6:00–7:00 | Optional Objects candidate review | Show research progress and a real limitation; do not claim clean isolation. |
| 7:00–8:00 | Close on preservation outcome | Explain what is preserved and what the next validation work must establish. |

Do not make new reconstruction, model downloads or successful separation a
condition for completing the main talk. Keep prepared artifacts and a reviewed
walkthrough recording available. The recording is a required fallback, not an
asset verified as complete by this audit.

## Remaining acceptance plan

Proposed dates, not booked commitments or scheduled automation:

| Due | Action | Acceptance evidence |
|---|---|---|
| 11 September | Accept camera / SpaceMouse behaviour; test offline cold loading | Clear repeatable opening views; comfortable physical navigation; no required external requests on the prepared workspace. |
| 18 September | Complete chosen capture's evidence and rehearse upload recovery | Saved-PLY evaluation and verified archive for the exact scene being described as complete; successful real upload and a recoverable failure if shown live. |
| 18 September | Decide whether Objects remains in the talk | Inspect each featured candidate against source views; omit clean-isolation claims unless identity and geometry pass. |
| 23 September | Freeze the demo version and prepare fallback | Reviewed commit/version, exact dependencies, local assets/models and recorded walkthrough identified. |
| 28 September | Two timed cold-start rehearsals on venue display | Both complete within the agreed slot; standard mouse fallback and archive links work. |
| 30 September | Run the accepted presentation scope | Use the frozen artifacts; do not add an unrehearsed dependency to the live path. |

**Go / no-go rule:** approve the prepared-scene demo only after the cold-start,
framing, navigation, fallback and version-freeze checks pass. Approve live new
processing and clean object isolation separately; neither is currently signed off.

## README audit changes

- Corrected the global CLI option order and the omitted separate evaluation step.
- Replaced unsupported Python “3.11+” wording with the validated Python 3.11
  setup; the dashboard's `cgi` import prevents use with Python 3.13+ as-is.
- Documented the current library, recoverable Trash, SpaceMouse and launcher.
- Distinguished the locally reproduced SAM2 candidates from other-machine
  TRELLIS.2 / ReconViaGen research and composed-scene examples.
- Removed the claim that the current carve is a clean isolated object.
- Added saved-PLY video metrics, historical baseline caveats and stage timings.
- Removed unqualified preset timing/recommendation claims and documented the
  gap between stock profiles and the measured custom recipe.
- Labelled README screenshots as an earlier UI revision and linked current
  evidence. Branding and the original preservation purpose were retained.

## Evidence records

- [UI and management review](demo-ui-review.md)
- [Video-only quality experiment](demo-video-quality-20260906.md)
- [Object-sidecar review and observed failures](demo-object-sidecar-review.md)
- [Separate SAM2 setup](sam2-object-sidecar-setup.md)
- [Master reconstruction record](nested-cinema-04-master.md)
- [Research closeout — separate environment](../report/closeout-report.pdf)

Private planning material informed the preservation and downstream-workflow
scope. Funding, partner strategy and personal details are not reproduced here.
