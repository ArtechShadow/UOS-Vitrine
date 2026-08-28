"""Trust boundary for external object-sidecar output.

The object sidecar is a separate, differently-licensed project invoked as a
subprocess (see ``cli.cmd_objects``). Everything it writes is **untrusted
input**: it may contain symlinks, absolute or ``..`` paths, mismatched hashes,
invalid UTF-8, or malformed records. This module is the single place that
validates and canonicalises that output before the preservation package
archives or records any of it, so a hostile or broken sidecar cannot write
outside the archive, smuggle unverified assets, or crash the packager.

Validation is *total*: any problem raises :class:`ObjectManifestError` with a
human-readable message. Callers choose whether to surface it (``cmd_objects``,
strict) or skip the objects and warn (``build_package``, defensive).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

OBJECT_SCHEMA = "vitrine/object/1"
_CHUNK = 1 << 20

#: A safe path component / identifier: no separators, no traversal, no reserved
#: URL or Windows characters. Deliberately strict.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Per-object fields carried into the preservation manifest. Required ones must
# be present and valid; optional ones are copied only when present.
_REQUIRED = ("object_id", "label", "mesh_path", "sha256")


class ObjectManifestError(ValueError):
    """Raised when object-sidecar output fails validation."""


def safe_component(name: Any) -> bool:
    """True if ``name`` is a safe single path component / identifier."""
    return isinstance(name, str) and bool(_SAFE_COMPONENT.match(name))


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_contained(rel: Any, field: str, root: Path) -> Path:
    """Resolve ``rel`` beneath ``root``, rejecting absolute/backslash/`..` paths.

    Returns the resolved path; raises if ``rel`` is not a relative path that
    stays inside ``root`` once resolved.
    """
    if not isinstance(rel, str) or not rel:
        raise ObjectManifestError(f"{field} must be a non-empty string")
    if "\\" in rel or re.match(r"^[A-Za-z]:", rel):
        raise ObjectManifestError(f"{field} must be a POSIX relative path, got {rel!r}")
    pp = PurePosixPath(rel)
    if pp.is_absolute() or any(part in ("..", "") for part in pp.parts):
        raise ObjectManifestError(f"{field} must be relative with no '..': {rel!r}")
    root = root.resolve()
    resolved = (root / rel).resolve()
    if not resolved.is_relative_to(root):
        raise ObjectManifestError(f"{field} escapes the archive root: {rel!r}")
    return resolved


def _validate_transform(value: Any, field: str) -> list[list[float]]:
    if not (isinstance(value, list) and len(value) == 4):
        raise ObjectManifestError(f"{field} must be a 4x4 array")
    for row in value:
        if not (isinstance(row, list) and len(row) == 4 and all(_is_finite_number(x) for x in row)):
            raise ObjectManifestError(f"{field} must be a 4x4 array of finite numbers")
    return value


def _validate_unit(value: Any, field: str) -> float:
    if not _is_finite_number(value) or not (0.0 <= float(value) <= 1.0):
        raise ObjectManifestError(f"{field} must be a finite number in [0, 1]")
    return float(value)


def load_validated_objects(objects_dir: Path) -> list[dict[str, Any]]:
    """Validate ``objects_dir`` and return projected, verified object records.

    Raises :class:`ObjectManifestError` on any problem: missing/malformed
    ``objects.json``, wrong schema, bad record shapes, unsafe/absent
    ``mesh_path``, hash mismatch, duplicate id or mesh path, out-of-range
    confidence/coverage, or non-finite numbers. Each returned record carries the
    **recomputed** sha256, and optional fields only when present.
    """
    objects_dir = Path(objects_dir)
    manifest = objects_dir / "objects.json"
    if not manifest.is_file():
        raise ObjectManifestError(f"no objects.json in {objects_dir}")
    try:
        text = manifest.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ObjectManifestError(f"objects.json is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise ObjectManifestError(f"objects.json unreadable: {exc}") from exc
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ObjectManifestError(f"objects.json is not valid JSON: {exc}") from exc

    if not isinstance(doc, dict):
        raise ObjectManifestError("objects.json top level must be an object")
    if doc.get("schema") != OBJECT_SCHEMA:
        raise ObjectManifestError(
            f"objects.json schema must be {OBJECT_SCHEMA!r}, got {doc.get('schema')!r}"
        )
    records = doc.get("objects")
    if not isinstance(records, list):
        raise ObjectManifestError("objects.json 'objects' must be a list")

    root = objects_dir.resolve()
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    projected: list[dict[str, Any]] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise ObjectManifestError(f"objects[{i}] must be an object")
        missing = [k for k in _REQUIRED if k not in rec]
        if missing:
            raise ObjectManifestError(f"objects[{i}] missing required field(s): {missing}")

        oid = rec["object_id"]
        if not safe_component(oid):
            raise ObjectManifestError(f"objects[{i}].object_id must match [A-Za-z0-9._-]+: {oid!r}")
        if oid in seen_ids:
            raise ObjectManifestError(f"duplicate object_id {oid!r}")
        seen_ids.add(oid)

        label = rec["label"]
        if not isinstance(label, str) or not label:
            raise ObjectManifestError(f"objects[{i}].label must be a non-empty string")

        mesh_path = rec["mesh_path"]
        resolved = resolve_contained(mesh_path, f"objects[{i}].mesh_path", root)
        # is_symlink must be checked on the literal (unresolved) path — a resolved
        # path has followed the link and would never report as one.
        literal = objects_dir / mesh_path
        if literal.is_symlink() or not resolved.is_file():
            raise ObjectManifestError(f"objects[{i}].mesh_path is not a regular file: {mesh_path!r}")
        if mesh_path in seen_paths:
            raise ObjectManifestError(f"duplicate mesh_path {mesh_path!r}")
        seen_paths.add(mesh_path)

        declared = rec["sha256"]
        if not isinstance(declared, str) or not _HEX64.match(declared.lower()):
            raise ObjectManifestError(f"objects[{i}].sha256 must be 64 hex characters")
        actual = sha256_file(resolved)
        if actual != declared.lower():
            raise ObjectManifestError(
                f"objects[{i}].sha256 mismatch for {mesh_path!r}: declared {declared}, actual {actual}"
            )

        out: dict[str, Any] = {
            "object_id": oid, "label": label, "mesh_path": mesh_path, "sha256": actual,
        }
        if "transform" in rec:
            out["transform"] = _validate_transform(rec["transform"], f"objects[{i}].transform")
        if "orientation_status" in rec:
            status = rec["orientation_status"]
            if not isinstance(status, dict):
                raise ObjectManifestError(f"objects[{i}].orientation_status must be an object")
            out["orientation_status"] = status
        if "coverage" in rec:
            out["coverage"] = _validate_unit(rec["coverage"], f"objects[{i}].coverage")
        if "confidence" in rec:
            out["confidence"] = _validate_unit(rec["confidence"], f"objects[{i}].confidence")
        projected.append(out)
    return projected


def load_validated_composed_scene(objects_dir: Path) -> dict[str, Any] | None:
    """Validate an optional composed-scene reference in ``objects.json``.

    Returns ``{"path", "sha256", "derivative_class"}`` with a **recomputed**
    sha256, or ``None`` when absent. Raises :class:`ObjectManifestError` on a
    malformed reference (unsafe/missing path or hash mismatch) — the same
    trust-boundary rules as ``mesh_path``. A composed scene mixes observed and
    generated content, so it is always classified ``composed-derivative``.
    """
    objects_dir = Path(objects_dir)
    manifest = objects_dir / "objects.json"
    if not manifest.is_file():
        return None
    try:
        doc = json.loads(manifest.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    ref = doc.get("composed_scene") if isinstance(doc, dict) else None
    if ref is None:
        return None
    if not isinstance(ref, dict):
        raise ObjectManifestError("composed_scene must be an object")
    rel = ref.get("path")
    resolved = resolve_contained(rel, "composed_scene.path", objects_dir.resolve())
    if not resolved.is_file() or resolved.is_symlink() or (objects_dir / rel).is_symlink():
        raise ObjectManifestError(f"composed_scene.path is not a regular file: {rel!r}")
    declared = ref.get("sha256")
    if not isinstance(declared, str) or not _HEX64.match(declared.lower()):
        raise ObjectManifestError("composed_scene.sha256 must be 64 hex characters")
    actual = sha256_file(resolved)
    if actual != declared.lower():
        raise ObjectManifestError(
            f"composed_scene.sha256 mismatch for {rel!r}: declared {declared}, actual {actual}")
    return {"path": rel, "sha256": actual, "derivative_class": "composed-derivative"}


def safe_copy_tree(src: Path, dest: Path) -> int:
    """Copy ``src`` → ``dest`` rejecting symlinks and non-regular files.

    Walks without following symlinks, refuses any symlink entry (file *or*
    directory) and any non-regular file (fifo, socket, device), and enforces
    that every destination path resolves beneath ``dest``. Returns the file count.
    """
    src = Path(src)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()
    count = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        base = Path(dirpath)
        for name in dirnames:
            if (base / name).is_symlink():
                raise ObjectManifestError(f"refusing symlinked directory in objects tree: {base / name}")
        for name in filenames:
            item = base / name
            if item.is_symlink():
                raise ObjectManifestError(f"refusing symlink in objects tree: {item}")
            if not item.is_file():
                raise ObjectManifestError(f"refusing non-regular file in objects tree: {item}")
            rel = item.relative_to(src)
            target = dest / rel
            if not (target.resolve().is_relative_to(dest_root)):
                raise ObjectManifestError(f"objects path escapes the archive: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            count += 1
    return count
