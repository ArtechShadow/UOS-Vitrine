"""Assemble the preservation package.

A Gaussian splat is a *rendering*, not evidence. On its own it is an opaque
blob of a million anisotropic blobs: you cannot tell from it what was
photographed, when, with what, by whom, or whether it faithfully represents the
thing it claims to. In ten years the viewer that opens it may not exist.

What makes this a preservation act rather than a nice picture is the sidecar:

- the **original files**, untouched, so the reconstruction can be redone;
- the **camera poses** in COLMAP's plain-text form, so it can be redone
  *identically* without re-solving;
- the **software versions and parameters**, so a difference in a future
  re-run can be attributed;
- **checksums** for everything, so bit rot is detectable rather than silent;
- a **plain-language README**, so a person opening the folder in 2040 with no
  knowledge of this project can understand what they are holding.

Layout deliberately favours obviousness over cleverness — directory names a
human can read, formats that are text where text will do.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import objects as objects_mod

logger = logging.getLogger(__name__)

CHUNK = 1 << 20  # 1 MiB


@dataclass
class PackageResult:
    root: Path
    file_count: int
    total_bytes: int
    manifest: Path

    def describe(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "total_gb": round(self.total_bytes / 2**30, 3),
        }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _package_versions() -> dict[str, str]:
    """Versions of everything that materially affects the result."""
    versions: dict[str, str] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    for name in ("torch", "gsplat", "numpy", "PIL", "plyfile"):
        try:
            module = __import__(name)
            versions[name] = getattr(module, "__version__", "unknown")
        except ImportError:
            versions[name] = "not installed"
    try:
        import torch

        if torch.cuda.is_available():
            versions["cuda"] = torch.version.cuda or "unknown"
            versions["gpu"] = torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001 - diagnostics must never break packaging
        pass
    return versions


def _colmap_version(image: str = "colmap/colmap:latest") -> str:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return f"{image}@{result.stdout.strip()[:19]}"
    except (OSError, subprocess.SubprocessError):
        pass
    return image


def _git_revision(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "not a git repository"


OBJECTS_README_NOTE = """\
## A note on `derivatives/objects/`

This package also contains per-object 3D models under `derivatives/objects/`,
produced by a separate object-reconstruction sidecar. Unlike the scene splat in
`model/`, these are **not** regenerable from `model/scene.ply` alone: they draw
on the registered images, external generative models, prompts and sidecar-
specific processing. Treat them as derived, non-evidentiary interpretations. The
`objects` list in `manifest.json` records each object's label, its `mesh_path`
(relative to `derivatives/objects/`) and a SHA-256 checksum recomputed at
packaging time, alongside the usual per-file checksums.

When present, `composed_scene` is a single glTF scene placing every object in the
reconstructed space. It is a **composed derivative**: it mixes observed geometry/
placement with generated geometry and textures. Per-object and per-material
provenance is carried in the scene's node metadata — do not read it as a survey."""


def _copy_tree(src: Path, dest: Path, *, description: str) -> int:
    if not src.exists():
        logger.warning("skipping %s — %s not found", description, src)
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    if src.is_file():
        shutil.copy2(src, dest / src.name)
        return 1
    for item in sorted(src.rglob("*")):
        if item.is_file():
            target = dest / item.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            count += 1
    return count


README_TEMPLATE = """\
# {title}

This folder is a preservation package for a photogrammetric 3D reconstruction.
It was produced by `vitrine` on {date}.

## What was captured

{subject}

## What is in here

| Folder | Contents |
|---|---|
| `originals/` | The source photographs and video exactly as they came off the camera. Nothing has been resized, re-compressed, or colour-managed. **These are the preservation master.** |
| `sfm/` | Camera positions solved by COLMAP, as plain text. `cameras.txt` describes each lens, `images.txt` gives the position and orientation of every photograph, `points3D.txt` is the sparse point cloud. |
| `model/` | `scene.ply` — the Gaussian splat itself, with full spherical-harmonic colour. This is the master 3D model. |
| `derivatives/` | Compressed and converted copies for viewing and sharing. These can be regenerated from `model/` at any time; the originals cannot. |
| `manifest.json` | Machine-readable record: software versions, every parameter used, quality measurements, and a SHA-256 checksum for every file listed above. |

## Verifying integrity

Every file has a SHA-256 checksum in `manifest.json`. To check nothing has
degraded:

```bash
python3 - <<'EOF'
import hashlib, json, pathlib
m = json.load(open("manifest.json"))
bad = []
for entry in m["files"]:
    p = pathlib.Path(entry["path"])
    if not p.is_file():
        bad.append((entry["path"], "MISSING")); continue
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if h != entry["sha256"]:
        bad.append((entry["path"], "CHANGED"))
print("OK — all files verify" if not bad else f"PROBLEMS: {{bad}}")
EOF
```

## Reproducing the reconstruction

The point of keeping `originals/` and `sfm/` together is that this can be
rebuilt. `manifest.json` records the exact software versions and every
parameter. Given the same inputs and settings the result should match; where a
future run differs, the manifest is what lets you work out why.

## A caveat worth recording

A Gaussian splat reproduces *how the subject looked from the positions the
camera actually occupied*. It is an interpolation, not a measurement. Surfaces
never photographed are invented by the optimiser, and reflective or
transmissive materials — mirrors, screens, glass — are represented as whatever
made the training images look right, which is not the same as what was
physically there. Treat it as a very good photographic record with parallax,
not as survey-grade geometry.

{notes}

---

Quality measured on {n_eval} held-out views that the optimiser never saw:
**PSNR {psnr} dB, SSIM {ssim}**.
"""


def build_package(
    output_root: Path,
    *,
    originals: list[Path],
    sfm_dir: Path,
    model_ply: Path,
    derivatives: list[Path] | None = None,
    database: Path | None = None,
    title: str = "3D reconstruction",
    subject: str = "Not recorded.",
    notes: str = "",
    train_report: dict[str, Any] | None = None,
    ingest_report: dict[str, Any] | None = None,
    sfm_report: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    objects_dir: Path | None = None,
    project_root: Path | None = None,
) -> PackageResult:
    """Assemble the package and write ``manifest.json`` plus ``README.md``.

    When ``objects_dir`` holds an object sidecar's output (an ``objects.json``
    plus per-object assets), its tree is archived under
    ``derivatives/objects/`` and its records are surfaced as an optional
    top-level ``objects`` key. The schema stays
    ``vitrine/preservation-package/1`` — the key is additive and absent on runs
    that produced no objects, so existing packages and readers are unaffected.
    """
    output_root = Path(output_root)
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    counts: dict[str, int] = {}
    counts["originals"] = sum(
        _copy_tree(Path(src), output_root / "originals", description="originals")
        for src in originals
    )
    counts["sfm"] = _copy_tree(Path(sfm_dir), output_root / "sfm", description="sparse model")
    if database and Path(database).is_file():
        shutil.copy2(database, output_root / "sfm" / "database.db")
        counts["sfm"] += 1
    counts["model"] = _copy_tree(Path(model_ply), output_root / "model", description="splat model")
    counts["derivatives"] = sum(
        _copy_tree(Path(d), output_root / "derivatives", description="derivative")
        for d in (derivatives or [])
    )

    # Object sidecar output, if any, is validated at the trust boundary first;
    # only fully-valid output is archived (under derivatives/objects/, so its
    # assets are checksummed with everything else) and recorded. Invalid or
    # absent output leaves the package byte-for-byte identical to a no-object run.
    object_records: list[dict[str, Any]] | None = None
    composed_scene: dict[str, Any] | None = None
    if objects_dir is not None and Path(objects_dir).is_dir():
        try:
            object_records = objects_mod.load_validated_objects(Path(objects_dir))
            composed_scene = objects_mod.load_validated_composed_scene(Path(objects_dir))
        except objects_mod.ObjectManifestError as exc:
            logger.warning("ignoring object sidecar output: %s", exc)
            object_records = None
            composed_scene = None
        else:
            counts["objects"] = objects_mod.safe_copy_tree(
                Path(objects_dir), output_root / "derivatives" / "objects"
            )

    # Checksum everything now in place.
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(output_root.rglob("*")):
        if path.is_file():
            size = path.stat().st_size
            total_bytes += size
            files.append(
                {
                    # Always POSIX separators, so a package authored on Windows
                    # is portable and verifies on any platform.
                    "path": path.relative_to(output_root).as_posix(),
                    "bytes": size,
                    "sha256": sha256(path),
                }
            )

    now = datetime.now(timezone.utc)
    manifest = {
        "schema": "vitrine/preservation-package/1",
        "created_utc": now.isoformat(),
        "title": title,
        "subject": subject,
        "software": {
            "vitrine_revision": _git_revision(project_root or Path(__file__).resolve().parent.parent),
            "colmap": _colmap_version(),
            **_package_versions(),
        },
        "profile": profile or {},
        "ingest": ingest_report or {},
        "sfm": sfm_report or {},
        "training": train_report or {},
        "contents": counts,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
    }
    if object_records is not None:
        manifest["objects"] = object_records
    if composed_scene is not None:
        # Path is relative to the archived derivatives/objects/ tree.
        manifest["composed_scene"] = composed_scene

    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    report = train_report or {}
    readme_notes = notes
    if object_records:
        readme_notes = f"{notes}\n\n{OBJECTS_README_NOTE}".strip()
    (output_root / "README.md").write_text(
        README_TEMPLATE.format(
            title=title,
            date=now.strftime("%d %B %Y"),
            subject=subject,
            notes=readme_notes,
            n_eval=report.get("n_eval_views", "?"),
            psnr=report.get("final_psnr", "?"),
            ssim=report.get("final_ssim", "?"),
        ),
        encoding="utf-8",
    )

    logger.info(
        "package: %d files, %.2f GB → %s",
        len(files), total_bytes / 2**30, output_root,
    )
    return PackageResult(output_root, len(files), total_bytes, manifest_path)


#: Package metadata written *after* the checksum pass, so they are not (and
#: cannot be, for the manifest) listed in their own inventory.
_UNLISTED_METADATA = {"manifest.json", "README.md"}


def verify_package(root: Path) -> tuple[bool, list[str]]:
    """Re-check the package against its manifest. Returns ``(ok, problems)``.

    Verification is exact: every listed file must exist with the recorded size
    and checksum; manifest paths must be safe (relative, no ``..``, no
    duplicates); the on-disk inventory must contain nothing unlisted (bar the
    package's own metadata); and ``file_count``/``total_bytes`` must match.
    """
    root = Path(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return False, [f"no manifest at {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"manifest unreadable: {exc}"]
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        return False, ["manifest has no valid 'files' list"]

    problems: list[str] = []
    listed: set[str] = set()
    total_bytes = 0
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            problems.append("malformed file entry")
            continue
        # Legacy packages authored on Windows store backslash separators
        # (originals\x.jpg). Normalise them to POSIX, then apply the same safety
        # checks — genuine traversal (..\), drive letters (C:\) and absolute
        # paths are still rejected.
        raw = entry["path"]
        rel = raw.replace("\\", "/")
        pp = PurePosixPath(rel)
        if (pp.is_absolute() or re.match(r"^[A-Za-z]:", rel)
                or any(part in ("..", "") for part in pp.parts)):
            problems.append(f"UNSAFE PATH {raw}")
            continue
        if rel in listed:
            problems.append(f"DUPLICATE {rel}")
            continue
        listed.add(rel)
        path = root / rel
        if not path.is_file() or path.is_symlink():
            problems.append(f"MISSING {rel}")
            continue
        size = path.stat().st_size
        total_bytes += size
        if entry.get("bytes") != size:
            problems.append(f"SIZE {rel}")
        if sha256(path) != entry.get("sha256"):
            problems.append(f"CHANGED {rel}")

    # Exact inventory: any regular file on disk that is not listed (and is not
    # package metadata) is an intrusion or a packaging bug.
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(root).as_posix()
            if rel not in listed and rel not in _UNLISTED_METADATA:
                problems.append(f"UNLISTED {rel}")

    if manifest.get("file_count") != len(listed):
        problems.append(f"file_count {manifest.get('file_count')} != {len(listed)} listed")
    if manifest.get("total_bytes") != total_bytes:
        problems.append(f"total_bytes {manifest.get('total_bytes')} != {total_bytes} on disk")

    return not problems, problems
