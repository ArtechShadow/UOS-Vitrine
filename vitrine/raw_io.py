"""Sony RAW development for ingest; original sensor files are never modified."""
from pathlib import Path

from PIL import Image, ImageCms
from PIL.TiffImagePlugin import IFDRational

RAW_SUFFIXES = {".arw"}


def _dependencies():
    try:
        import rawpy
        import exifread
    except ImportError as exc:
        raise OSError("Sony ARW support requires rawpy and ExifRead; install requirements.txt") from exc
    return rawpy, exifread


def _metadata(path, flip):
    _, exifread = _dependencies()
    with Path(path).open("rb") as source:
        tags = exifread.process_file(source, details=False)
    exif = Image.Exif()
    # Copy photographic metadata, never RAW TIFF offsets or MakerNote pointers
    # that would point into the original sensor file from the developed JPEG.
    for name, code in {"Make":271, "Model":272, "DateTime":306,
                       "Artist":315, "Copyright":33432}.items():
        if tag := tags.get("Image " + name):
            exif[code] = str(tag)
    orientation = tags.get("Image Orientation")
    exif[274] = int(orientation.values[0]) if orientation else {3:3, 5:8, 6:6}.get(flip, 1)
    sub = {}
    for name, code in {"ExposureTime":33434, "FNumber":33437, "ISOSpeedRatings":34855,
                       "DateTimeOriginal":36867, "DateTimeDigitized":36868,
                       "FocalLength":37386, "FocalLengthIn35mmFilm":41989,
                       "LensMake":42035, "LensModel":42036}.items():
        tag = tags.get("EXIF " + name)
        if tag is None:
            continue
        value = tag.values if isinstance(tag.values, str) else tag.values[0]
        if hasattr(value, "num") and hasattr(value, "den"):
            value = IFDRational(value.num, value.den)
        sub[code] = value
    if sub:
        exif[34665] = sub
    parsed = Image.Exif()
    parsed.load(exif.tobytes())
    return parsed


def raw_info(path):
    """Read actual sensor-output dimensions and metadata, not an embedded JPEG."""
    rawpy, _ = _dependencies()
    try:
        with rawpy.imread(str(path)) as raw:
            exif = _metadata(path, raw.sizes.flip)
            size = (raw.sizes.width, raw.sizes.height)
        return size, exif
    except (rawpy.LibRawError, KeyError, TypeError) as exc:
        raise OSError(f"Cannot read Sony ARW {Path(path).name}: {exc}") from exc


def open_image(path, *, preview=False):
    """Return a closeable PIL image; RAW orientation is applied by the caller."""
    if Path(path).suffix.lower() not in RAW_SUFFIXES:
        return Image.open(path)
    rawpy, _ = _dependencies()
    try:
        with rawpy.imread(str(path)) as raw:
            exif = _metadata(path, raw.sizes.flip)
            pixels = raw.postprocess(
                use_camera_wb=True, use_auto_wb=False, no_auto_bright=True,
                output_color=rawpy.ColorSpace.sRGB, output_bps=8,
                gamma=(2.4, 12.92), user_flip=0, half_size=preview,
            )
        image = Image.fromarray(pixels)
        image.info["exif"] = exif.tobytes()
        image.info["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        return image
    except (rawpy.LibRawError, KeyError, TypeError) as exc:
        raise OSError(f"Cannot develop Sony ARW {Path(path).name}: {exc}") from exc


def development_record():
    rawpy, _ = _dependencies()
    return {"decoder":"rawpy", "version":rawpy.__version__,
            "libraw_version":list(rawpy.libraw_version), "source_format":"Sony ARW",
            "white_balance":"as-shot", "auto_white_balance":False,
            "auto_brightness":False, "colour_space":"sRGB", "output_bits":8,
            "gamma":[2.4,12.92], "orientation":"EXIF transpose", "originals_modified":False}
