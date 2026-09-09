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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vitrine.export import write_splat_file
from vitrine.objects import load_validated_objects, resolve_contained, sha256_file


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
        candidate = candidates[asset['path']]
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
            shutil.copy2(crop, folder / 'preview.png')
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
                           'derivative_class': 'observed-gaussian-subset'},
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
