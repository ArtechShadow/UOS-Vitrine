# Capture session — Vitrine Capture → ingest

The mobile app does not reconstruct. It writes a **capture session**: original
photographs and video, plus a `capture.json` that records how they were shot.
`vitrine ingest` consumes that folder. LiDAR / ARKit data is guidance only and
never enters training.

```text
Vitrine Capture  →  session folder or .zip  →  vitrine ingest  →  SfM / train / package
```

Dashboard job metadata at `runs/<name>/capture.json` is a different file. The
session document is stored as `runs/<name>/capture-session.json`.

---

## Layout

```text
<session-id>/
  capture.json
  stills/
    wide/                 # one physical camera, one folder
    ultrawide/            # only if the operator enabled a second lens
  video/
  sidecar/                # optional; ignored by ingest
    poses.jsonl
    coverage.json
    rejected/
```

Rules:

- Stills and video stay in separate trees. Mixing them in one folder is a hard error.
- Each stills subdirectory is one camera group (`ingest.classify_sources` uses the folder name).
- Originals are byte-identical. No transcode, resize, or EXIF strip.
- `sidecar/` is never copied into `source/`.
- A flattened dump of images next to `capture.json` is a hard error — it would collapse camera groups.

---

## `capture.json`

Schema id: `vitrine/capture-session/1`

| Field | Required | Notes |
|---|---|---|
| `schema` | yes | exactly `vitrine/capture-session/1` |
| `session_id` | yes | UUID |
| `title` | yes | human name |
| `venue`, `subject` | no | preservation copy |
| `created_at`, `closed_at` | no | ISO-8601 |
| `device.model` | yes | e.g. `iPhone 15 Plus` |
| `device.os`, `device.lidar_available`, `device.cameras` | no | |
| `locked_settings.ae` / `awb` / `af` | yes | must be `true` |
| `locked_settings.flash` | yes | `off` |
| `locked_settings.zoom` | yes | `1.0` |
| `screens_policy` | yes | `off` \| `paused` \| `playing` |
| `mirrors_policy` | yes | `covered` \| `accepted` \| `mask-later` |
| `room_size` | no | `small` \| `room` \| `large` (`room` ≈ Nested Cinema 4×5 m) |
| `passes[]` | yes | `orbit_chest`, `orbit_knee`, `loop_close`, `detail`, `video` |
| `checklist` | yes | booleans listed below |
| `coverage.stills_count` | yes | must equal the number of stills in `files` |
| `coverage.stills_target` | no | suggestion, not a stop condition |
| `files[]` | yes | every still and video, with SHA-256 |

Checklist keys: `three_or_more_views`, `two_heights`, `loop_closed`, `detail_pass`, `corners`, `floor_edges`, `exposure_locked`.

Each `files[]` entry:

```json
{
  "path": "stills/wide/IMG_0001.HEIC",
  "bytes": 4123981,
  "sha256": "…64 lowercase hex…",
  "role": "still",
  "camera_group": "wide"
}
```

`role` is `still` or `video`. Paths are POSIX, relative to the session root, no `..`.

---

## Hard errors vs warnings

**Reject the session** (phone must not export; ingest must not start):

- missing / invalid `capture.json`
- no stills
- unlocked AE, AWB or AF
- flash on, or zoom ≠ 1
- stills and video in the same folder
- flattened layout
- hash or size mismatch
- listed file missing, or unstated media under `stills/` / `video/`
- missing screens or mirrors policy
- zip path traversal

**Warn, then ingest anyway:**

- loop not closed, one height, no detail pass, missing corners
- still count below `stills_target`
- stills without EXIF
- no video glue pass

72 stills is a starting target for a ~4×5 m room, not a gate.

---

## Commands

```bash
python -m vitrine capture-session validate path/to/session
python -m vitrine capture-session validate path/to/session.zip
python -m vitrine ingest --session path/to/session --run-dir runs/my-capture
python -m vitrine run --session path/to/session --run-dir runs/my-capture --quality draft
```

The dashboard Create screen accepts the same `.zip` as **Import capture session**. Loose photograph/video upload is unchanged and still flattens into one `source/` folder.

After import, `package` copies `capture-session.json` into the preservation archive so screens/mirrors decisions survive as evidence.
