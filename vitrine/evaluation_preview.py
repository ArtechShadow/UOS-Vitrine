"""Publish reduced-size source/render pairs after each real evaluation view."""
from pathlib import Path
import uuid

import numpy as np
from PIL import Image

from .construction import atomic_json


class EvaluationPreview:
    def __init__(self, folder, observer):
        self.folder = Path(folder)
        self.observer = observer
        self.records = []

    def __call__(self, metrics, rendered, reference, completed, total):
        name = uuid.uuid4().hex
        self.folder.mkdir(parents=True, exist_ok=True)
        for label, pixels in [('source', reference), ('render', rendered)]:
            image = Image.fromarray((pixels.detach().cpu().numpy().clip(0, 1) * 255).astype(np.uint8))
            image.thumbnail((640, 480))
            image.save(self.folder / f'{name}-{label}.jpg', 'JPEG', quality=85)
        self.records.append(dict(id=name, camera=metrics.name, psnr=metrics.psnr, ssim=metrics.ssim,
                                 source=f'{name}-source.jpg', render=f'{name}-render.jpg'))
        atomic_json(self.folder.parent / 'evaluation-progress.json',
                    dict(count=completed, total=total, records=self.records))
        self.observer.update(count=completed, total=total, unit='evaluated views', message=metrics.name)
