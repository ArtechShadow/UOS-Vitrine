"""Convert verified external carved PLY assets to a typed core object manifest.

Only files cross this boundary. The source manifest and crops are preserved;
carved Gaussians are explicitly labelled as candidates, never as meshes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import re
from collections.abc import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vitrine.export import write_splat_file
from vitrine.objects import load_validated_objects, resolve_contained, sha256_file


def _first(mapping: Mapping, *keys):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _evidence_list(record: Mapping, candidate: Mapping) -> list | None:
    """Return only an explicit sidecar evidence list; never infer by index."""
    for owner in (record, candidate):
        for key in ("evidence", "observations", "supporting_views", "views", "frames"):
            value = owner.get(key)
            if isinstance(value, list):
                return value
        provenance = owner.get("provenance")
        if isinstance(provenance, Mapping):
            for key in ("evidence", "observations", "supporting_views", "views", "frames"):
                value = provenance.get(key)
                if isinstance(value, list):
                    return value
    return None


def _source_scene_reference(record: Mapping, candidate: Mapping, document: Mapping):
    """Return an explicitly declared source-scene lineage record, if any."""
    for owner in (record, candidate, document):
        value = _first(owner, "source_scene", "source_scene_ply")
        if isinstance(value, Mapping):
            return dict(value)
        value = _first(owner, "source_scene_sha256", "scene_sha256")
        if value is not None:
            return {"sha256": value}
    return None


def _safe_mask_name(index: int, path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._") or "mask"
    suffix = path.suffix.lower() if path.suffix else ".png"
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix):
        suffix = ".png"
    return f"{index:04d}-{stem}{suffix}"


def _copy_object_evidence(raw_list, *, source: Path, folder: Path, object_id: str) -> tuple[list[dict], list[str]]:
    """Copy complete masks and canonicalise exact frame/detection provenance.

    Partial upstream records are retained in ``source_record`` by the caller,
    while the canonical list remains empty/partial and ``support_status`` says
    why it cannot drive a surface reconstruction.  This keeps Gaussian
    candidates inspectable without turning a thumbnail or guessed frame into a
    false mask association.
    """
    if raw_list is None:
        return [], ["sidecar supplied no explicit per-view evidence list"]
    evidence_dir = folder / "evidence" / "masks"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    canonical: list[dict] = []
    issues: list[str] = []
    for index, raw in enumerate(raw_list):
        prefix = f"evidence[{index}]"
        if not isinstance(raw, Mapping):
            issues.append(f"{prefix} is not an object")
            continue
        # ``frame_id`` is deliberately not accepted as a COLMAP image ID: it
        # may be a sidecar list position or an extracted-video frame number.
        # A compatible sidecar must identify the registered COLMAP image
        # explicitly and may retain its own frame ID as source_frame_id.
        image_id = _first(raw, "image_id", "colmap_image_id")
        source_frame_id = _first(raw, "source_frame_id", "frame_id")
        image_name = _first(raw, "image_name", "image", "name", "frame_name")
        camera_id = _first(raw, "camera_id", "colmap_camera_id")
        instance_id = _first(raw, "instance_id", "instance", "object_instance_id")
        detection_id = _first(raw, "detection_id", "detection", "det_id")
        mask = _first(raw, "mask_path", "mask", "segmentation", "matte")
        declared_mask_hash = _first(raw, "mask_sha256", "mask_checksum", "mask_hash")
        if isinstance(mask, Mapping):
            declared_mask_hash = declared_mask_hash or _first(mask, "sha256", "checksum", "hash")
            mask = _first(mask, "path", "mask_path")
        missing = []
        if image_id is None:
            missing.append("image_id/colmap_image_id")
        if not isinstance(image_name, str) or not image_name.strip():
            missing.append("image_name")
        if camera_id is None:
            missing.append("camera_id")
        if instance_id is None and detection_id is None:
            missing.append("instance_id/detection_id")
        if not isinstance(mask, str) or not mask.strip():
            missing.append("mask_path")
        coordinate_space = _first(raw, "coordinate_space", "space")
        if coordinate_space not in ("view", "rectified"):
            missing.append("coordinate_space (view or rectified)")
        transform = _first(raw, "transform", "rectification")
        if coordinate_space == "rectified" and not isinstance(transform, Mapping):
            missing.append("rectification transform for rectified mask")
        if missing:
            issues.append(f"{prefix} missing {', '.join(missing)}")
            continue
        try:
            mask_path = resolve_contained(mask, f"{prefix}.mask_path", source)
        except Exception as exc:  # noqa: BLE001 - turn sidecar failure into explicit evidence status
            issues.append(f"{prefix} mask path invalid: {exc}")
            continue
        if not mask_path.is_file() or mask_path.is_symlink():
            issues.append(f"{prefix} mask is not a regular file: {mask}")
            continue
        actual_hash = sha256_file(mask_path)
        if declared_mask_hash is not None and (
            not isinstance(declared_mask_hash, str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", declared_mask_hash)
            or actual_hash != declared_mask_hash.lower()
        ):
            issues.append(f"{prefix} mask checksum mismatch for {mask}")
            continue
        copied_name = _safe_mask_name(index, mask_path)
        copied = evidence_dir / copied_name
        shutil.copy2(mask_path, copied)
        entry = {
            "image_id": image_id,
            "image_name": image_name,
            "camera_id": camera_id,
            "instance_id": instance_id,
            "detection_id": detection_id,
            "mask_path": f"{object_id}/evidence/masks/{copied_name}",
            "mask_sha256": actual_hash,
            "source_mask_path": str(mask),
            "mask_hash_source": "sidecar-declared" if declared_mask_hash is not None else "core-computed",
            "coordinate_space": coordinate_space,
        }
        if source_frame_id is not None:
            entry["source_frame_id"] = source_frame_id
        if transform is not None:
            entry["transform"] = transform
        for key in ("image_width", "image_height", "mask_width", "mask_height"):
            if key in raw:
                entry[key] = raw[key]
        canonical.append(entry)
    if not canonical:
        issues.append("no complete mask/frame/camera/instance associations were imported")
    return canonical, issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    if (out / 'objects.json').exists():
        parser.error('Refusing to overwrite an existing objects manifest.')
    doc = json.loads((source / 'objects.json').read_text(encoding='utf-8'))
    summary = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    if doc.get('schema') != 'vitrine/object/1' or not doc.get('objects'):
        parser.error('No actual sidecar object records were produced.')
    candidates = {item['ply']: item for item in summary['objects'] if item.get('ply')}
    records = []
    for index, record in enumerate(doc['objects']):
        assets = [a for a in record.get('assets', []) if a.get('role') == 'object_ply']
        if len(assets) != 1:
            parser.error('Expected exactly one observed object_ply per candidate.')
        asset = assets[0]
        ply = resolve_contained(asset['path'], 'asset.path', source)
        if not ply.is_file() or sha256_file(ply) != asset.get('sha256'):
            parser.error('Source asset missing or SHA-256 mismatch.')
        try:
            candidate = candidates[asset['path']]
        except KeyError:
            parser.error(f"No summary candidate matches observed asset {asset['path']!r}.")
        oid = f'candidate_{index + 1:02d}'
        folder = out / oid
        if folder.exists():
            parser.error(f'Refusing to overwrite candidate directory {folder}.')
        folder.mkdir(parents=True)
        shutil.copy2(ply, folder / 'observed.ply')
        splat = write_splat_file(ply, folder / 'observed.splat', radius_multiple=None)
        if splat.stat().st_size == 0 or splat.stat().st_size % 32:
            parser.error('Converted splat has no valid binary Gaussian records.')
        preview = candidate.get('seed_crop')
        if preview:
            crop = resolve_contained(preview, 'seed_crop', source)
            if not crop.is_file() or crop.is_symlink():
                parser.error(f'Seed preview is not a regular file: {preview}')
            shutil.copy2(crop, folder / 'preview.png')
        evidence, evidence_issues = _copy_object_evidence(
            _evidence_list(record, candidate), source=source, folder=folder, object_id=oid
        )
        source_scene = _source_scene_reference(record, candidate, doc)
        if source_scene is None:
            evidence_issues.append(
                'sidecar supplied no source scene PLY checksum; occlusion reference cannot be verified'
            )
        elif not isinstance(source_scene.get('sha256'), str) or not re.fullmatch(
            r'[0-9a-fA-F]{64}', source_scene['sha256']
        ):
            evidence_issues.append('source scene PLY lineage is missing a valid SHA-256 checksum')
        # A candidate can still be viewed as an observed Gaussian subset when
        # the external sidecar did not emit masks.  It must never enter the
        # supported surface path in that state; object_mesh checks this status
        # and fails with the actionable issues below.
        support_status = 'supported-evidence' if not evidence_issues else 'missing-evidence'
        records.append({
            'object_id': oid,
            'label': f'Equipment candidate {index + 1:02d}',
            'splat_path': f'{oid}/observed.splat',
            'sha256': sha256_file(splat),
            'preview_path': f'{oid}/preview.png' if preview else None,
            'confidence': candidate['carve_confidence'],
            'orientation_status': {'status': 'observed-world-coordinates',
                                   'placement': candidate.get('placement_status')},
            'provenance': {'prompt': record['label'], 'source_asset': asset,
                           'source_record': record, 'source_preview': preview,
                           'gaussian_count': splat.stat().st_size // 32,
                           'review_status': 'candidate-needs-visual-review',
                           'derivative_class': 'observed-gaussian-subset',
                           'evidence': evidence,
                           'support_status': support_status,
                           'support_issues': evidence_issues,
                           'source_scene': source_scene},
        })
    evidence = out / 'provenance'
    evidence.mkdir(exist_ok=True)
    for name in ('objects.json', 'summary.json'):
        shutil.copy2(source / name, evidence / name)
    (out / 'objects.json').write_text(json.dumps({
        'schema': 'vitrine/object/1', 'objects': records,
        'method': 'Local segmentation + multiview Gaussian carve; model lineage preserved per object',
        'source_manifest_sha256': hashlib.sha256((source / 'objects.json').read_bytes()).hexdigest(),
        'note': 'Observed Gaussian candidates; source crops are evidence previews. No mesh generation.',
    }, indent=2), encoding='utf-8')
    validated = load_validated_objects(out)
    print(f'Validated {len(validated)} real carved object splats in {out}')


if __name__ == '__main__':
    main()
