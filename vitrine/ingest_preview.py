"""Best-effort visual record of real ingest decisions; never changes selection."""
from pathlib import Path
import logging
import time
import uuid

from PIL import Image, ImageOps

from .construction import atomic_json

logger = logging.getLogger(__name__)


class IngestPreview:
    def __init__(self, folder, total):
        self.folder = Path(folder)
        self.records = []
        self.by_path = {}
        self.data = dict(total=total, scored=0, kept=0, rejected=0, extracted=0, complete=False)
        self.last = 0
        self.publish(force=True)

    def publish(self, force=False):
        if not force and time.monotonic() - self.last < .3:
            return
        try:
            atomic_json(self.folder / 'selection.json', {**self.data, 'records': self.records})
            self.last = time.monotonic()
        except (OSError, ValueError):
            logger.debug('Could not publish image selection', exc_info=True)

    def scored(self, path, score):
        self.data['phase'] = 'sorting'
        self.data['scored'] += 1
        self.data['current'] = path.name
        if str(path) not in self.by_path:
            record = dict(id=uuid.uuid4().hex, file=path.name, group=path.parent.name,
                          status='pending', reason='Scored; comparing with other views in this camera group', video=False)
            self.thumbnail(path, record)
            self.records.append(record)
            self.by_path[str(path)] = record
        self.publish()

    def decision(self, group, path, status, reason, score=None):
        record = self.by_path.get(str(path))
        if record:
            record.update(group=group, status=status, reason=reason, sharpness=score)
            self.data[status] += 1
            self.publish()
            return
        record = dict(id=uuid.uuid4().hex, file=path.name, group=group,
                      status=status, reason=reason, sharpness=score, video=False)
        self.thumbnail(path, record)
        self.records.append(record)
        self.by_path[str(path)] = record
        self.data[status] += 1
        self.publish()

    def thumbnail(self, path, record):
        try:
            target = self.folder / 'selection-thumbnails' / (record['id'] + '.jpg')
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(path) as original:
                original.thumbnail((256, 192))
                thumb = ImageOps.exif_transpose(original).convert('RGB')
                thumb.save(target, 'JPEG', quality=75)
            record['thumbnail'] = target.name
        except (OSError, ValueError):
            # A failed thumbnail must not prevent preparing the source image.
            logger.debug('Could not preview %s', path, exc_info=True)
    def extracting(self, video):
        self.data.update(phase='extracting', video=video)
        self.publish(force=True)

    def extracted(self, group, path):
        if str(path) in self.by_path:
            return
        record = dict(id=uuid.uuid4().hex, file=path.name, group=group,
                      status='pending', reason='Extracted from video; awaiting image selection', video=True)
        self.thumbnail(path, record)
        if 'thumbnail' not in record:
            return
        self.records.append(record)
        self.by_path[str(path)] = record
        self.data['extracted'] += 1
        self.data['total'] += 1
        self.publish()

    def finish(self):
        self.data['complete'] = True
        self.data['current'] = None
        self.publish(force=True)
