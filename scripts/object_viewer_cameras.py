"""Frame observed object candidates from their actual selected source camera."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vitrine.colmap_io import read_model
from vitrine.ply import read_splat_ply
from vitrine.objects import load_validated_objects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--objects', required=True, type=Path)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--intrinsics', required=True, type=Path)
    args = parser.parse_args()
    load_validated_objects(args.objects)
    model = read_model(args.model)
    images = {image.name: image for image in model.images}
    intrinsic_records = json.loads(args.intrinsics.read_text())
    records = json.loads((args.objects / 'objects.json').read_text())['objects']
    for record in records:
        source = record['provenance']['source_record']
        seed = source['stage_scores']['seed_lane_a']
        intrinsic = intrinsic_records[seed]
        image = images[intrinsic['colmap_name']]
        rotation = image.rotation_matrix().astype(float)
        position = image.camera_centre().astype(float)
        centre = np.asarray(source['carve']['centroid'], dtype=float)
        up = -rotation.T[:, 1]
        forward = centre - position
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, up)
        right /= np.linalg.norm(right)
        up = np.cross(right, forward)
        folder = args.objects / record['object_id']
        cloud = read_splat_ply(folder / 'observed.ply')
        points = np.asarray(cloud['means'], dtype=float)
        vectors = points - position
        depth = vectors @ forward
        positive = depth > 1e-4
        slopes = np.abs(vectors[positive] @ up) / depth[positive]
        horizontal = np.abs(vectors[positive] @ right) / depth[positive]
        # A square fitting cone also remains useful in narrow viewer windows.
        slope = max(float(np.quantile(slopes, .99)), float(np.quantile(horizontal, .99)))
        fov = float(np.clip(np.degrees(2 * np.arctan(slope * 1.15)), 18, 90))
        camera = {'name': image.name, 'label': 'Source camera',
                  'position': position.tolist(), 'lookAt': centre.tolist(),
                  'up': up.tolist(), 'verticalFov': fov,
                  'provenance': 'Actual COLMAP source-camera position, aimed at candidate centroid; fitted FOV.'}
        (folder / 'viewer-cameras.json').write_text(json.dumps([camera], indent=2))
        print(record['object_id'], image.name, 'fov', round(fov, 1))


if __name__ == '__main__':
    main()
