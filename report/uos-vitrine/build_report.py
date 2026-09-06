"""Build the XR Lab UOS Vitrine academic report.

The generator is intentionally read-only with respect to ``source/`` and
``runs/``. It reads small JSON reports and selected source photographs, writes
intermediate assets below ``tmp/pdfs/``, and writes the final PDF below
``output/pdf/``.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image as PILImage
from PIL import ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "report" / "uos-vitrine"
MANUSCRIPT = REPORT_DIR / "report.md"
TMP_DIR = ROOT / "tmp" / "pdfs" / "uos-vitrine"
OUT_DIR = ROOT / "output" / "pdf"
OUT_PDF = OUT_DIR / "UOS-Vitrine-Academic-Report-Draft.pdf"

NAVY = HexColor("#0B1325")
NAVY_2 = HexColor("#162238")
ORANGE = HexColor("#FF6400")
ORANGE_DARK = HexColor("#C94800")
INK = HexColor("#172033")
MID = HexColor("#667085")
LIGHT = HexColor("#F2F4F7")
PALE_ORANGE = HexColor("#FFF2E8")
GREEN = HexColor("#237A57")
RED = HexColor("#B42318")
BLUE = HexColor("#3156A3")
WHITE = colors.white

LOGO_WHITE = ROOT / "Branding" / "XR_Lab_Logo_Transparent_Cropped.png"
LOGO_BLACK = ROOT / "Branding" / "XR_Lab_Logo_Black_Text_Transparent.png"
VITRINE_WORDMARK = ROOT / "Branding" / "vitrine-viewfinder-wordmark.png"
VITRINE_MARK = ROOT / "Branding" / "partners" / "vitrine-mark.png"
APP_VISUAL = Path(r"C:\Users\realg\Pictures\Screenshots\Screenshot 2026-08-06 222856.png")
APP_VISUAL_DIAGNOSTIC = Path(r"C:\Users\realg\Pictures\Screenshots\Screenshot 2026-08-06 222826.png")


def _register_fonts() -> tuple[str, str, str]:
    """Use stable Windows fonts when present and safe built-ins otherwise."""
    candidates = [
        (Path(r"C:\Windows\Fonts\aptos.ttf"), "Aptos"),
        (Path(r"C:\Windows\Fonts\calibri.ttf"), "Calibri"),
    ]
    bold_candidates = [
        (Path(r"C:\Windows\Fonts\aptos-bold.ttf"), "Aptos-Bold"),
        (Path(r"C:\Windows\Fonts\calibrib.ttf"), "Calibri-Bold"),
    ]
    mono_candidates = [
        (Path(r"C:\Windows\Fonts\consola.ttf"), "Consolas"),
    ]

    body = "Helvetica"
    bold = "Helvetica-Bold"
    mono = "Courier"
    for path, name in candidates:
        if path.is_file():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            body = name
            break
    for path, name in bold_candidates:
        if path.is_file():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            bold = name
            break
    for path, name in mono_candidates:
        if path.is_file():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            mono = name
            break
    return body, bold, mono


BODY_FONT, BOLD_FONT, MONO_FONT = _register_fonts()


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontName=BODY_FONT,
            fontSize=9.3, leading=13.0, textColor=INK, spaceAfter=5.5,
            alignment=TA_LEFT, allowWidows=0, allowOrphans=0,
        ),
        "chapter": ParagraphStyle(
            "Chapter", parent=base["Heading1"], fontName=BOLD_FONT,
            fontSize=25, leading=29, textColor=NAVY, spaceBefore=4,
            spaceAfter=16, keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontName=BOLD_FONT,
            fontSize=15, leading=19, textColor=NAVY, spaceBefore=10,
            spaceAfter=6, keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "H3", parent=base["Heading3"], fontName=BOLD_FONT,
            fontSize=11.5, leading=14, textColor=ORANGE_DARK, spaceBefore=8,
            spaceAfter=4, keepWithNext=True,
        ),
        "caption": ParagraphStyle(
            "Caption", parent=base["BodyText"], fontName=BODY_FONT,
            fontSize=8, leading=10.5, textColor=MID, spaceBefore=4,
            spaceAfter=10,
        ),
        "table": ParagraphStyle(
            "TableCell", parent=base["BodyText"], fontName=BODY_FONT,
            fontSize=7.7, leading=9.5, textColor=INK,
        ),
        "table_head": ParagraphStyle(
            "TableHead", parent=base["BodyText"], fontName=BOLD_FONT,
            fontSize=7.8, leading=9.5, textColor=WHITE,
        ),
        "code": ParagraphStyle(
            "Code", parent=base["Code"], fontName=MONO_FONT,
            fontSize=7.4, leading=9.2, textColor=WHITE, backColor=NAVY_2,
            borderPadding=7, borderColor=NAVY_2, borderWidth=0.5,
            spaceBefore=5, spaceAfter=8,
        ),
        "callout": ParagraphStyle(
            "Callout", parent=base["BodyText"], fontName=BODY_FONT,
            fontSize=9, leading=12.5, textColor=INK, backColor=PALE_ORANGE,
            borderColor=ORANGE, borderWidth=0.8, borderPadding=8,
            leftIndent=4, rightIndent=4, spaceBefore=6, spaceAfter=8,
        ),
        "toc_h": ParagraphStyle(
            "TOCHeading", parent=base["Heading1"], fontName=BOLD_FONT,
            fontSize=24, leading=28, textColor=NAVY, spaceAfter=14,
        ),
    }


ST = styles()


def inline_markup(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<font name='%s'>\1</font>" % MONO_FONT, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)
    return text


def image_for_report(path: Path, max_w: float, max_h: float) -> Image:
    with PILImage.open(path) as im:
        w, h = im.size
    scale = min(max_w / w, max_h / h)
    return Image(str(path), width=w * scale, height=h * scale)


def make_contact_sheet() -> Path:
    """Create a labelled contact sheet from actual preserved source stills."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / "nested-cinema-source-contact-sheet.jpg"
    stills = sorted((ROOT / "source" / "stills").glob("*.JPEG"))
    if not stills:
        return out
    picks = [stills[0], stills[len(stills) // 3], stills[(2 * len(stills)) // 3], stills[-1]]
    cell_w, cell_h = 760, 520
    sheet = PILImage.new("RGB", (cell_w * 2, cell_h * 2), "#0B1325")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    for i, path in enumerate(picks):
        with PILImage.open(path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            fitted = ImageOps.fit(image, (cell_w, cell_h - 42), method=PILImage.Resampling.LANCZOS)
        x, y = (i % 2) * cell_w, (i // 2) * cell_h
        sheet.paste(fitted, (x, y))
        draw.rectangle((x, y + cell_h - 42, x + cell_w, y + cell_h), fill="#0B1325")
        draw.text((x + 16, y + cell_h - 35), path.name, fill="#FFFFFF", font=font)
    sheet.save(out, quality=90, optimize=True)
    return out


def make_cover_background() -> Path:
    """Prepare the validated Vitrine run screenshot as the cover field."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / "cover-app-visual.jpg"
    if not APP_VISUAL.is_file():
        return out
    with PILImage.open(APP_VISUAL) as source:
        image = source.convert("RGB")
        image = ImageOps.fit(image, (1400, 1980), method=PILImage.Resampling.LANCZOS,
                             centering=(0.52, 0.5))
        image = ImageEnhance.Contrast(image).enhance(1.02)
        image = ImageEnhance.Brightness(image).enhance(0.88)
        navy = PILImage.new("RGB", image.size, "#0B1325")
        image = PILImage.blend(image, navy, 0.18)
        image.save(out, quality=92, optimize=True)
    return out


def make_cover_diagnostic() -> Path:
    """Prepare the failed 5090 run screenshot as a supporting evidence inset."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / "cover-diagnostic-run.jpg"
    if not APP_VISUAL_DIAGNOSTIC.is_file():
        return out
    with PILImage.open(APP_VISUAL_DIAGNOSTIC) as source:
        image = source.convert("RGB")
        image = ImageOps.fit(image, (1200, 760), method=PILImage.Resampling.LANCZOS,
                             centering=(0.5, 0.44))
        image = ImageEnhance.Brightness(image).enhance(1.18)
        image.save(out, quality=91, optimize=True)
    return out


def make_dark_vitrine_mark() -> Path:
    """Create a navy/orange mark for light pages without changing brand masters."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / "vitrine-mark-dark.png"
    if not VITRINE_MARK.is_file():
        return out
    with PILImage.open(VITRINE_MARK) as source:
        image = source.convert("RGBA")
        pixels = []
        for red, green, blue, alpha in image.getdata():
            if alpha and red > 175 and green > 175 and blue > 175:
                pixels.append((11, 19, 37, alpha))
            else:
                pixels.append((red, green, blue, alpha))
        image.putdata(pixels)
        image.save(out)
    return out


def make_chapter_visual(number: str) -> Path:
    """Create a consistent chapter image from preserved Nested Cinema stills."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / f"chapter-{number}-source-visual.jpg"
    stills = sorted((ROOT / "source" / "stills").glob("*.JPEG"))
    if not stills:
        return out
    positions = {"1": 3, "2": 11, "3": 19, "4": 27, "5": 35,
                 "6": 43, "7": 51, "8": 59, "9": 67}
    path = stills[min(positions.get(number, 0), len(stills) - 1)]
    with PILImage.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image = ImageOps.fit(image, (1200, 1000), method=PILImage.Resampling.LANCZOS,
                             centering=(0.5, 0.5))
        image = ImageEnhance.Color(image).enhance(0.78)
        image = ImageEnhance.Contrast(image).enhance(1.08)
        navy = PILImage.new("RGB", image.size, "#0B1325")
        image = PILImage.blend(image, navy, 0.28)
        image.save(out, quality=91, optimize=True)
    return out


class PipelineDiagram(Flowable):
    def __init__(self, width: float, height: float = 88 * mm):
        super().__init__()
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        stages = [
            ("SOURCE", "stills + video"),
            ("INGEST", "select sharp frames"),
            ("SfM", "two camera models"),
            ("TRAIN", "MCMC + SSIM + crops"),
            ("MEASURE", "held-out PSNR / SSIM"),
            ("PACKAGE", "masters + fixity"),
        ]
        margin = 5 * mm
        gap = 4 * mm
        box_w = (self.width - 2 * margin - gap * (len(stages) - 1)) / len(stages)
        y = 32 * mm
        c.setFillColor(LIGHT)
        c.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont(BOLD_FONT, 13)
        c.drawString(margin, self.height - 14 * mm, "Archive-first reconstruction flow")
        for i, (name, sub) in enumerate(stages):
            x = margin + i * (box_w + gap)
            c.setFillColor(NAVY if i not in (0, 5) else ORANGE_DARK)
            c.roundRect(x, y, box_w, 24 * mm, 2.5 * mm, fill=1, stroke=0)
            c.setFillColor(WHITE)
            c.setFont(BOLD_FONT, 7.4)
            c.drawCentredString(x + box_w / 2, y + 14 * mm, name)
            c.setFont(BODY_FONT, 5.8)
            c.drawCentredString(x + box_w / 2, y + 8 * mm, sub)
            if i < len(stages) - 1:
                c.setStrokeColor(ORANGE)
                c.setLineWidth(1.2)
                c.line(x + box_w, y + 12 * mm, x + box_w + gap - 1.5 * mm, y + 12 * mm)
                c.line(x + box_w + gap - 3 * mm, y + 13.5 * mm,
                       x + box_w + gap - 1.5 * mm, y + 12 * mm)
                c.line(x + box_w + gap - 3 * mm, y + 10.5 * mm,
                       x + box_w + gap - 1.5 * mm, y + 12 * mm)
        c.setFillColor(MID)
        c.setFont(BODY_FONT, 7)
        c.drawString(margin, 14 * mm,
                     "Originals and solved poses remain reproducible evidence; models and access formats remain derivatives.")


class PackageDiagram(Flowable):
    def __init__(self, width: float, height: float = 130 * mm):
        super().__init__()
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        c.setFillColor(NAVY)
        c.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont(BOLD_FONT, 14)
        c.drawString(9 * mm, self.height - 15 * mm, "Preservation package")
        rows = [
            ("originals/", "Untouched camera evidence", ORANGE),
            ("sfm/", "Readable camera intrinsics, poses and sparse points", HexColor("#58A6FF")),
            ("model/", "Full-SH Gaussian PLY master", HexColor("#9B8AFB")),
            ("derivatives/", "Web splats, mesh and previews", HexColor("#5FD3A3")),
            ("manifest.json", "Versions, settings, metrics and SHA-256", HexColor("#FEC84B")),
            ("README.md", "Human-readable context and limitations", HexColor("#E4E7EC")),
        ]
        y = self.height - 30 * mm
        for name, desc, accent in rows:
            c.setFillColor(NAVY_2)
            c.roundRect(9 * mm, y - 8 * mm, self.width - 18 * mm, 14 * mm, 2 * mm, fill=1, stroke=0)
            c.setFillColor(accent)
            c.rect(9 * mm, y - 8 * mm, 2 * mm, 14 * mm, fill=1, stroke=0)
            c.setFont(BOLD_FONT, 8.5)
            c.drawString(15 * mm, y, name)
            c.setFillColor(WHITE)
            c.setFont(BODY_FONT, 8)
            c.drawString(48 * mm, y, desc)
            y -= 17 * mm


class ComparisonDiagram(Flowable):
    def __init__(self, width: float, height: float = 95 * mm):
        super().__init__()
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        half = self.width / 2
        c.setFillColor(LIGHT)
        c.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.roundRect(5 * mm, 12 * mm, half - 8 * mm, self.height - 24 * mm, 3 * mm, fill=1, stroke=0)
        c.setFillColor(ORANGE_DARK)
        c.roundRect(half + 3 * mm, 12 * mm, half - 8 * mm, self.height - 24 * mm, 3 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont(BOLD_FONT, 12)
        c.drawCentredString(half / 2, self.height - 25 * mm, "UOS VITRINE")
        c.drawCentredString(half + half / 2, self.height - 25 * mm, "DREAMLAB VITRINE")
        left = ["archive master", "measured laptop 3DGS", "multi-camera stills + video", "fixity + provenance"]
        right = ["structured engine scene", "SAM3 object discovery", "object GLB / FBX", "Unreal 5.8 assembly"]
        c.setFont(BODY_FONT, 8)
        for i, text in enumerate(left):
            c.drawCentredString(half / 2, self.height - (38 + i * 10) * mm, text)
        for i, text in enumerate(right):
            c.drawCentredString(half + half / 2, self.height - (38 + i * 10) * mm, text)
        c.setStrokeColor(ORANGE)
        c.setLineWidth(2)
        c.line(half - 10 * mm, 7 * mm, half + 10 * mm, 7 * mm)
        c.setFillColor(MID)
        c.setFont(BOLD_FONT, 7)
        c.drawCentredString(half, 2.5 * mm, "VERSIONED FILE HANDOFF - NOT A CODE MERGER")


class CardFlowDiagram(Flowable):
    """Reusable visual sequence for methods and decision boundaries."""

    def __init__(self, width: float, title: str, cards: list[tuple[str, str]],
                 footer: str, height: float = 76 * mm):
        super().__init__()
        self.width, self.height = width, height
        self.title, self.cards, self.footer = title, cards, footer

    def draw(self):
        c = self.canv
        c.setFillColor(LIGHT)
        c.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont(BOLD_FONT, 12)
        c.drawString(8 * mm, self.height - 13 * mm, self.title)
        n = len(self.cards)
        gap = 4 * mm
        box_w = (self.width - 16 * mm - gap * (n - 1)) / n
        y, box_h = 23 * mm, 30 * mm
        for idx, (label, note) in enumerate(self.cards):
            x = 8 * mm + idx * (box_w + gap)
            c.setFillColor(NAVY if idx % 2 == 0 else ORANGE_DARK)
            c.roundRect(x, y, box_w, box_h, 2.5 * mm, fill=1, stroke=0)
            c.setFillColor(WHITE)
            c.setFont(BOLD_FONT, 8)
            c.drawCentredString(x + box_w / 2, y + 19 * mm, label)
            c.setFont(BODY_FONT, 6.2)
            words = note.split()
            lines, current = [], ""
            for word in words:
                trial = (current + " " + word).strip()
                if c.stringWidth(trial, BODY_FONT, 6.2) > box_w - 5 * mm:
                    lines.append(current); current = word
                else:
                    current = trial
            if current: lines.append(current)
            for j, line in enumerate(lines[:3]):
                c.drawCentredString(x + box_w / 2, y + (12 - j * 4) * mm, line)
            if idx < n - 1:
                c.setStrokeColor(ORANGE)
                c.setLineWidth(1.4)
                c.line(x + box_w, y + box_h / 2, x + box_w + gap, y + box_h / 2)
        c.setFillColor(MID)
        c.setFont(BODY_FONT, 6.8)
        c.drawString(8 * mm, 9 * mm, self.footer)


class SourceMixDiagram(Flowable):
    def __init__(self, width: float, height: float = 72 * mm):
        super().__init__(); self.width, self.height = width, height

    def draw(self):
        c = self.canv
        c.setFillColor(NAVY); c.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE); c.setFont(BOLD_FONT, 12)
        c.drawString(8 * mm, self.height - 13 * mm, "Source hierarchy and training status")
        rows = [
            ("72 stills", "25 MP + EXIF", "ARCHIVAL MASTER", ORANGE, 1.0),
            ("1,819 video frames", "720p temporal source", "SELECTED FOR COVERAGE", BLUE, .78),
            ("383 Luma frames", "service derivative", "EXCLUDED FROM TRAINING", MID, .48),
        ]
        for i, (name, detail, status, colour, frac) in enumerate(rows):
            y = self.height - (27 + i * 13) * mm
            c.setFillColor(HexColor("#263650")); c.roundRect(8 * mm, y, self.width - 16 * mm, 9 * mm, 2 * mm, fill=1, stroke=0)
            c.setFillColor(colour); c.roundRect(8 * mm, y, (self.width - 16 * mm) * frac, 9 * mm, 2 * mm, fill=1, stroke=0)
            c.setFillColor(WHITE); c.setFont(BOLD_FONT, 7.5); c.drawString(11 * mm, y + 3.2 * mm, name)
            c.setFont(BODY_FONT, 6.5); c.drawString(43 * mm, y + 3.2 * mm, detail)
            c.setFont(BOLD_FONT, 6.2); c.drawRightString(self.width - 11 * mm, y + 3.2 * mm, status)


class FrustumDiagram(Flowable):
    def __init__(self, width: float, height: float = 75 * mm):
        super().__init__(); self.width, self.height = width, height

    def draw(self):
        c = self.canv
        c.setFillColor(LIGHT); c.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=0)
        c.setFillColor(NAVY); c.setFont(BOLD_FONT, 12); c.drawString(8 * mm, self.height - 13 * mm, "Why random crops preserve detail at bounded cost")
        x, y, w, h = 10 * mm, 16 * mm, 62 * mm, 42 * mm
        c.setFillColor(NAVY_2); c.rect(x, y, w, h, fill=1, stroke=0)
        c.setStrokeColor(ORANGE); c.setLineWidth(2); c.rect(x + 27 * mm, y + 11 * mm, 22 * mm, 22 * mm, fill=0, stroke=1)
        c.setFillColor(WHITE); c.setFont(BODY_FONT, 7); c.drawString(x + 3 * mm, y + 3 * mm, "full 2048-2560 px source on CPU")
        c.setFillColor(ORANGE); c.setFont(BOLD_FONT, 7); c.drawString(x + 29 * mm, y + 35 * mm, "768 px crop")
        c.setStrokeColor(ORANGE); c.line(x + w + 5 * mm, y + h / 2, x + w + 20 * mm, y + h / 2)
        c.setFillColor(NAVY); c.roundRect(x + w + 22 * mm, y + 5 * mm, 65 * mm, 32 * mm, 3 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE); c.setFont(BOLD_FONT, 8); c.drawString(x + w + 28 * mm, y + 27 * mm, "Principal point offset")
        c.setFont(MONO_FONT, 8); c.drawString(x + w + 28 * mm, y + 18 * mm, "cx' = cx - x0")
        c.drawString(x + w + 28 * mm, y + 11 * mm, "cy' = cy - y0")


class SegmentationHandoffDiagram(Flowable):
    def __init__(self, width: float, height: float = 75 * mm):
        super().__init__(); self.width, self.height = width, height

    def draw(self):
        c = self.canv
        c.setFillColor(LIGHT); c.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=0)
        c.setFillColor(NAVY); c.setFont(BOLD_FONT, 12); c.drawString(8 * mm, self.height - 13 * mm, "Proposed object-segmentation handoff")
        cards = [
            ("UOS MASTER", "images + poses + splat", NAVY),
            ("SAM3", "per-view masks", ORANGE_DARK),
            ("PROJECTION", "multi-view object IDs", BLUE),
            ("SAM3D / TRELLIS.2", "derived object mesh", GREEN),
            ("UNREAL", "placed scene asset", NAVY),
        ]
        gap, x0, y = 3 * mm, 7 * mm, 25 * mm
        bw = (self.width - 14 * mm - gap * 4) / 5
        for i, (title, sub, colour) in enumerate(cards):
            x = x0 + i * (bw + gap)
            c.setFillColor(colour); c.roundRect(x, y, bw, 25 * mm, 2.5 * mm, fill=1, stroke=0)
            c.setFillColor(WHITE); c.setFont(BOLD_FONT, 6.7); c.drawCentredString(x + bw / 2, y + 16 * mm, title)
            c.setFont(BODY_FONT, 5.7); c.drawCentredString(x + bw / 2, y + 8 * mm, sub)
        c.setFillColor(ORANGE_DARK); c.setFont(BOLD_FONT, 7)
        c.drawString(8 * mm, 14 * mm, "ARCHIVE MASTER REMAINS UNCHANGED")
        c.setFillColor(MID); c.setFont(BODY_FONT, 6.5)
        c.drawRightString(self.width - 8 * mm, 14 * mm, "Meshes, transforms and cleaned scenes are labelled derivatives")


class MetricChart(Flowable):
    def __init__(self, width: float, height: float = 100 * mm):
        super().__init__()
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        c.setFillColor(LIGHT)
        c.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont(BOLD_FONT, 13)
        c.drawString(9 * mm, self.height - 14 * mm, "Crop/source scale separates healthy and collapsed runs")
        labels = ["3060 baseline", "5090 control", "5090 safe-cap"]
        psnr = [25.72, 25.064, 17.489]
        ssim = [0.8167, 0.8211, 0.6897]
        base_y = 20 * mm
        chart_h = 55 * mm
        col_w = (self.width - 24 * mm) / 3
        for i, label in enumerate(labels):
            x = 10 * mm + i * col_w
            c.setFillColor([NAVY, GREEN, ORANGE_DARK][i])
            bar_h = chart_h * psnr[i] / 30.0
            c.roundRect(x, base_y, 15 * mm, bar_h, 2 * mm, fill=1, stroke=0)
            c.setFillColor(INK)
            c.setFont(BOLD_FONT, 9)
            c.drawString(x, base_y + bar_h + 4 * mm, f"{psnr[i]:.2f} dB")
            c.setFillColor([BLUE, HexColor("#5FD3A3"), ORANGE][i])
            s_h = chart_h * ssim[i]
            c.roundRect(x + 18 * mm, base_y, 15 * mm, s_h, 2 * mm, fill=1, stroke=0)
            c.setFillColor(INK)
            c.drawString(x + 18 * mm, base_y + s_h + 4 * mm, f"{ssim[i]:.4f}")
            c.setFillColor(MID)
            c.setFont(BODY_FONT, 7.5)
            c.drawString(x, 11 * mm, label)
        c.setFont(BODY_FONT, 7)
        c.drawRightString(self.width - 8 * mm, 4 * mm, "Left bar: PSNR scaled to 30 dB | Right bar: SSIM scaled to 1.0")


class ChapterDivider(Flowable):
    """A deliberately paced divider for each numbered academic chapter."""

    STRAPLINES = {
        "1": "Why a splat needs evidence",
        "2": "Nested Cinema and its source record",
        "3": "From camera originals to a measured model",
        "4": "Measured constraints, not assumptions",
        "5": "Validated success and useful failure",
        "6": "The splat is one derivative",
        "7": "Complementary systems, explicit boundaries",
        "8": "Evidence, inference and curatorial choice",
        "9": "A measured route to the next version",
    }

    def __init__(self, title: str, width: float, height: float):
        super().__init__()
        self.title = title
        self.width = width
        self.height = height
        match = re.match(r"^(\d+)\.\s*(.*)$", title)
        self.number = match.group(1) if match else ""
        self.name = match.group(2) if match else title
        self.toc_title = title

    def wrap(self, avail_width, avail_height):
        return avail_width, avail_height

    def draw(self):
        c = self.canv
        c.setFillColor(NAVY)
        c.roundRect(0, 17 * mm, self.width, self.height - 34 * mm,
                    5 * mm, fill=1, stroke=0)
        visual = make_chapter_visual(self.number)
        image_x = self.width - 91 * mm
        image_y = self.height - 151 * mm
        if visual.is_file():
            c.drawImage(str(visual), image_x, image_y, width=84 * mm, height=82 * mm,
                        preserveAspectRatio=False)
            c.setFillColor(NAVY)
            c.setFillAlpha(0.18)
            c.rect(image_x, image_y, 84 * mm, 82 * mm, fill=1, stroke=0)
            c.setFillAlpha(1)
        c.setFillColor(ORANGE)
        c.rect(0, self.height - 59 * mm, self.width, 2 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFillAlpha(0.25)
        c.setFont(BOLD_FONT, 94)
        c.drawRightString(self.width - 10 * mm, self.height - 48 * mm, self.number)
        c.setFillAlpha(1)
        c.setFillColor(WHITE)
        c.setFont(BOLD_FONT, 10)
        c.drawString(13 * mm, self.height - 34 * mm, f"CHAPTER {self.number}")
        title_size = 21 if len(self.name) > 24 else 27
        title_leading = 25 if len(self.name) > 24 else 32
        name_style = ParagraphStyle(
            "DividerTitle", fontName=BOLD_FONT, fontSize=title_size, leading=title_leading,
            textColor=WHITE, alignment=TA_LEFT,
        )
        paragraph = Paragraph(inline_markup(self.name), name_style)
        paragraph.wrapOn(c, 68 * mm, 70 * mm)
        paragraph.drawOn(c, 13 * mm, self.height - 105 * mm)
        c.setStrokeColor(HexColor("#263650"))
        c.setLineWidth(0.7)
        for radius in (8, 15, 22):
            c.circle(self.width - 25 * mm, 51 * mm, radius * mm, fill=0, stroke=1)
        c.setFillColor(HexColor("#B8C1D1"))
        c.setFont(BODY_FONT, 11)
        c.drawString(13 * mm, 39 * mm, self.STRAPLINES.get(self.number, ""))
        c.setFillColor(ORANGE)
        c.setFont(BOLD_FONT, 7.5)
        c.drawString(13 * mm, 28 * mm, "UOS VITRINE  |  XR LAB")
        c.setFillColor(HexColor("#8792A8"))
        c.setFont(BODY_FONT, 6.2)
        c.drawRightString(self.width - 10 * mm, 28 * mm,
                          "VISUAL: PRESERVED NESTED CINEMA SOURCE PHOTOGRAPH")


class AcademicDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename, pagesize=A4, rightMargin=23 * mm, leftMargin=25 * mm,
            topMargin=24 * mm, bottomMargin=23 * mm,
            title="UOS Vitrine: An Archive-Grade Local Pipeline for 3D Gaussian Splat Preservation",
            author="Glenn Watts, XR Lab, University of Salford",
            subject="3D Gaussian Splatting, digital preservation, Nested Cinema, DreamLab integration",
            creator="UOS Vitrine reproducible ReportLab build",
        )
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=self._on_page)])
        self._bookmark_count = 0

    def _on_page(self, canvas, doc):
        page = canvas.getPageNumber()
        canvas.saveState()
        if page == 1:
            cover_background = make_cover_background()
            if cover_background.is_file():
                canvas.drawImage(str(cover_background), 0, 0, width=A4[0], height=A4[1],
                                 preserveAspectRatio=False)
            canvas.setFillColor(NAVY)
            canvas.setFillAlpha(0.96)
            canvas.rect(0, 0, 112 * mm, A4[1], fill=1, stroke=0)
            canvas.setFillAlpha(1)
            canvas.setFillColor(ORANGE)
            canvas.rect(0, A4[1] - 17 * mm, A4[0], 1.6 * mm, fill=1, stroke=0)
            if VITRINE_MARK.is_file():
                canvas.drawImage(str(VITRINE_MARK), 20 * mm, A4[1] - 57 * mm,
                                 width=27 * mm, height=27 * mm, mask="auto", preserveAspectRatio=True)
            canvas.setFillColor(ORANGE)
            canvas.rect(51 * mm, A4[1] - 55 * mm, 1.2 * mm, 23 * mm, fill=1, stroke=0)
            canvas.setFillColor(WHITE)
            canvas.setFont(BOLD_FONT, 25)
            canvas.drawString(57 * mm, A4[1] - 44 * mm, "VITRINE")
            canvas.setFillColor(HexColor("#B8C1D1"))
            canvas.setFont(BODY_FONT, 8.2)
            canvas.drawString(57 * mm, A4[1] - 51 * mm, "Digital preservation")
            canvas.setFillColor(WHITE)
            canvas.setFont(BOLD_FONT, 7)
            canvas.drawString(20 * mm, A4[1] - 22 * mm, "XR LAB TECHNICAL REPORT  /  2026")
            canvas.setFont(BOLD_FONT, 25)
            canvas.drawString(20 * mm, A4[1] - 91 * mm, "An Archive-Grade")
            canvas.drawString(20 * mm, A4[1] - 103 * mm, "Local Pipeline")
            canvas.setFont(BODY_FONT, 15)
            canvas.drawString(20 * mm, A4[1] - 118 * mm, "for 3D Gaussian Splat")
            canvas.drawString(20 * mm, A4[1] - 127 * mm, "Preservation")
            canvas.setFillColor(HexColor("#B8C1D1"))
            canvas.setFont(BODY_FONT, 10.5)
            canvas.drawString(20 * mm, A4[1] - 148 * mm, "Nested Cinema - Vera's Not Alone")
            canvas.drawString(20 * mm, A4[1] - 157 * mm, "Technical and Preservation Report")
            canvas.setFillColor(ORANGE)
            canvas.roundRect(20 * mm, A4[1] - 176 * mm, 73 * mm, 8 * mm,
                             2 * mm, fill=1, stroke=0)
            canvas.setFillColor(WHITE)
            canvas.setFont(BOLD_FONT, 6.7)
            canvas.drawCentredString(56.5 * mm, A4[1] - 173.2 * mm,
                                     "LOCAL  /  REPRODUCIBLE  /  ARCHIVE-FIRST")
            canvas.setFillColor(WHITE)
            canvas.setFont(BOLD_FONT, 11)
            canvas.setFont(BOLD_FONT, 7.2)
            canvas.drawString(20 * mm, 102 * mm, "PROJECT CONTRIBUTORS")
            canvas.setFillColor(HexColor("#B8C1D1"))
            canvas.setFont(BODY_FONT, 7.2)
            canvas.drawString(20 * mm, 94 * mm,
                              "Glenn Watts  |  Roger McKinley  |  John O'Hare (DreamLab AI)")
            canvas.setFillColor(WHITE)
            canvas.setFont(BOLD_FONT, 7)
            canvas.drawString(20 * mm, 67 * mm, "REPORT AUTHOR")
            canvas.setFont(BOLD_FONT, 11)
            canvas.drawString(20 * mm, 61 * mm, "Glenn Watts")
            canvas.setFont(BODY_FONT, 9.5)
            canvas.drawString(20 * mm, 53 * mm, "XR Lab, University of Salford")
            canvas.setFillColor(ORANGE)
            canvas.setFont(BOLD_FONT, 9)
            canvas.drawString(20 * mm, 40 * mm, "DRAFT ACADEMIC REPORT  |  6 AUGUST 2026")
            diagnostic = make_cover_diagnostic()
            if diagnostic.is_file():
                canvas.setStrokeColor(ORANGE)
                canvas.setLineWidth(1.2)
                canvas.rect(123 * mm, 39 * mm, 75 * mm, 48 * mm, fill=0, stroke=1)
                canvas.drawImage(str(diagnostic), 124 * mm, 40 * mm,
                                 width=73 * mm, height=46 * mm, preserveAspectRatio=False)
                canvas.setFillColor(NAVY)
                canvas.setFillAlpha(0.9)
                canvas.rect(124 * mm, 40 * mm, 73 * mm, 8 * mm, fill=1, stroke=0)
                canvas.setFillAlpha(1)
                canvas.setFillColor(WHITE)
                canvas.setFont(BOLD_FONT, 5.8)
                canvas.drawString(127 * mm, 43 * mm, "5090 DIAGNOSTIC RUN  /  NEGATIVE RESULT")
            if LOGO_WHITE.is_file():
                canvas.drawImage(str(LOGO_WHITE), 151 * mm, 16 * mm,
                                 width=42 * mm, height=14 * mm, mask="auto", preserveAspectRatio=True)
            canvas.setFillColor(WHITE)
            canvas.setFont(BOLD_FONT, 6.5)
            canvas.drawRightString(193 * mm, 11 * mm, "ACTUAL VITRINE RUN VIEWS  /  VALIDATED + DIAGNOSTIC")
        else:
            canvas.setStrokeColor(ORANGE)
            canvas.setLineWidth(0.7)
            canvas.line(doc.leftMargin, A4[1] - 17 * mm, A4[0] - doc.rightMargin, A4[1] - 17 * mm)
            canvas.setFillColor(MID)
            canvas.setFont(BODY_FONT, 6.8)
            canvas.drawString(doc.leftMargin + 9 * mm, A4[1] - 13 * mm, "UOS VITRINE  |  DRAFT ACADEMIC REPORT")
            if LOGO_BLACK.is_file():
                canvas.drawImage(str(LOGO_BLACK), A4[0] - doc.rightMargin - 30 * mm,
                                 A4[1] - 15.2 * mm, width=30 * mm, height=9 * mm,
                                 mask="auto", preserveAspectRatio=True)
            dark_vitrine_mark = make_dark_vitrine_mark()
            if dark_vitrine_mark.is_file():
                canvas.drawImage(str(dark_vitrine_mark), doc.leftMargin, A4[1] - 15.3 * mm,
                                 width=7 * mm, height=7 * mm, mask="auto", preserveAspectRatio=True)
            canvas.setStrokeColor(HexColor("#D0D5DD"))
            canvas.line(doc.leftMargin, 16 * mm, A4[0] - doc.rightMargin, 16 * mm)
            canvas.setFillColor(MID)
            canvas.drawString(doc.leftMargin, 10 * mm, "Glenn Watts | XR Lab, University of Salford")
            canvas.drawRightString(A4[0] - doc.rightMargin, 10 * mm, str(page - 1))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, ChapterDivider):
            text = flowable.toc_title
            key = getattr(flowable, "_bookmark_name", None)
            if key is None:
                key = f"heading-{self._bookmark_count}"
                flowable._bookmark_name = key
                self._bookmark_count += 1
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=0, closed=False)
            self.notify("TOCEntry", (0, text, self.page - 1, key))
            return
        if isinstance(flowable, Paragraph):
            style = flowable.style.name
            if style in {"Chapter", "H2"}:
                level = 0 if style == "Chapter" else 1
                text = flowable.getPlainText()
                key = getattr(flowable, "_bookmark_name", None)
                if key is None:
                    key = f"heading-{self._bookmark_count}"
                    flowable._bookmark_name = key
                    self._bookmark_count += 1
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=False)
                self.notify("TOCEntry", (level, text, self.page - 1, key))


def parse_table(lines: list[str], start: int) -> tuple[Table, int]:
    raw: list[list[str]] = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        raw.append([cell.strip() for cell in lines[i].strip().strip("|").split("|")])
        i += 1
    if len(raw) > 1 and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in raw[1]):
        raw.pop(1)
    width = max(len(r) for r in raw)
    normalised = [r + [""] * (width - len(r)) for r in raw]
    data = []
    for row_idx, row in enumerate(normalised):
        style = ST["table_head"] if row_idx == 0 else ST["table"]
        data.append([Paragraph(inline_markup(cell), style) for cell in row])
    col_widths = [None] * width
    if width == 2:
        col_widths = [52 * mm, 100 * mm]
    elif width == 3:
        col_widths = [55 * mm, 49 * mm, 49 * mm]
    elif width == 4:
        col_widths = [52 * mm, 34 * mm, 34 * mm, 34 * mm]
    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.35, HexColor("#D0D5DD")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table, i


def manuscript_flowables(doc_width: float) -> list[Flowable]:
    text = MANUSCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    # The manuscript's opening title/author block is source metadata for human
    # readers. The PDF cover renders it separately, so begin parsing after the
    # first horizontal rule.
    try:
        lines = lines[lines.index("---") + 1:]
    except ValueError:
        pass
    story: list[Flowable] = [PageBreak()]

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("TOC0", fontName=BOLD_FONT, fontSize=10, leading=14,
                       leftIndent=0, firstLineIndent=0, textColor=NAVY, spaceBefore=4),
        ParagraphStyle("TOC1", fontName=BODY_FONT, fontSize=8.5, leading=11,
                       leftIndent=12, firstLineIndent=0, textColor=MID),
    ]
    story += [Paragraph("Contents", ST["toc_h"]), toc, PageBreak()]

    first_title_skipped = True
    i = 0
    para: list[str] = []
    in_code = False
    code: list[str] = []
    figure_inserted: set[str] = set()

    def flush_para():
        nonlocal para
        if para:
            joined = " ".join(x.strip() for x in para).strip()
            if joined:
                story.append(Paragraph(inline_markup(joined), ST["body"]))
            para = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_para()
            if in_code:
                story.append(Paragraph("<br/>".join(html.escape(x) for x in code), ST["code"]))
                code = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code.append(line)
            i += 1
            continue
        if not stripped:
            flush_para()
            i += 1
            continue
        if stripped == "---":
            flush_para()
            story.append(PageBreak())
            i += 1
            continue
        if stripped.startswith("|"):
            flush_para()
            table, i = parse_table(lines, i)
            story += [table, Spacer(1, 6 * mm)]
            continue
        if stripped.startswith("# "):
            flush_para()
            title = stripped[2:].strip()
            if not first_title_skipped:
                first_title_skipped = True
                i += 1
                continue
            if re.match(r"^\d+\.\s+", title):
                story += [ChapterDivider(title, doc_width, A4[1] - 47 * mm), PageBreak()]
            else:
                story.append(Paragraph(inline_markup(title), ST["chapter"]))
            i += 1
            continue
        if stripped.startswith("## "):
            flush_para()
            title = stripped[3:].strip()
            if title.startswith("7.6"):
                story.append(PageBreak())
            story.append(Paragraph(inline_markup(title), ST["h2"]))
            if title.startswith("1.3") and "baseline" not in figure_inserted:
                story += [CardFlowDiagram(doc_width, "Practitioner baseline to preservation requirements", [
                    ("POLYCAM", "hands-on mobile scanning"),
                    ("LUMA AI", "service reconstruction baseline"),
                    ("OBSERVE", "convenience and control gaps"),
                    ("UOS VITRINE", "local reproducible archive"),
                ], "Exploratory personal testing informed requirements; it is not presented as a controlled product benchmark."),
                Paragraph("Figure 1.1. Glenn Watts's personal Polycam and Luma AI testing informed the requirements for UOS Vitrine. The relationship is experiential rather than a numerical product comparison.", ST["caption"])]
                figure_inserted.add("baseline")
            elif title.startswith("2.2") and "contact" not in figure_inserted:
                contact = make_contact_sheet()
                if contact.is_file():
                    story += [image_for_report(contact, doc_width, 105 * mm),
                              Paragraph("Figure 2.1. Four photographs selected across the preserved 25 MP still sequence. These are source observations, not reconstruction renders.", ST["caption"])]
                figure_inserted.add("contact")
            elif title.startswith("2.3") and "source_mix" not in figure_inserted:
                story += [SourceMixDiagram(doc_width),
                          Paragraph("Figure 2.2. Source hierarchy. Bar length is illustrative of role and volume rather than a common numerical scale; status labels define what may enter training.", ST["caption"])]
                figure_inserted.add("source_mix")
            elif title.startswith("3.1") and "pipeline" not in figure_inserted:
                story += [PipelineDiagram(doc_width),
                          Paragraph("Figure 3.1. UOS Vitrine archive-first reconstruction flow. Expensive stages remain independently repeatable and report their outputs.", ST["caption"])]
                figure_inserted.add("pipeline")
            elif title.startswith("3.3") and "cameras" not in figure_inserted:
                story += [CardFlowDiagram(doc_width, "Multi-camera structure-from-motion", [
                    ("25 MP STILLS", "camera model A + EXIF"),
                    ("720p VIDEO", "camera model B + frames"),
                    ("COLMAP", "shared scene and bundle"),
                    ("PER-IMAGE LOOKUP", "correct intrinsics retained"),
                ], "Applying one camera model to both resolutions can silently warp the reconstruction."),
                Paragraph("Figure 3.2. The two capture groups share a solved scene but retain distinct intrinsics, referenced per registered image.", ST["caption"])]
                figure_inserted.add("cameras")
            elif title.startswith("3.7") and "crop" not in figure_inserted:
                story += [FrustumDiagram(doc_width),
                          Paragraph("Figure 3.3. Random-crop training exposes the optimiser to full-resolution detail while bounding each rendered step to a 768-pixel window.", ST["caption"])]
                figure_inserted.add("crop")
            elif title.startswith("5.3") and "metrics" not in figure_inserted:
                story += [MetricChart(doc_width),
                          Paragraph("Figure 5.1. The RTX 5090 control reproduces the laptop baseline at approximately 25 times the speed. The 1600/4096 safe-cap run collapses despite the same one-million cap, isolating crop/source scale as the decisive tested variable.", ST["caption"])]
                figure_inserted.add("metrics")
            elif title.startswith("6.1") and "package" not in figure_inserted:
                story += [PackageDiagram(doc_width),
                          Paragraph("Figure 6.1. The splat is one component of the preservation package. Originals and poses carry the strongest long-term evidential value.", ST["caption"])]
                figure_inserted.add("package")
            elif title.startswith("7.2") and "comparison" not in figure_inserted:
                story += [ComparisonDiagram(doc_width),
                          Paragraph("Figure 7.1. Complementary responsibilities of the two Vitrine systems. Integration occurs through a versioned file handoff.", ST["caption"])]
                figure_inserted.add("comparison")
            elif title.startswith("7.5") and "segmentation" not in figure_inserted:
                story += [SegmentationHandoffDiagram(doc_width),
                          Paragraph("Figure 7.2. Proposed segmentation integration. UOS Vitrine supplies registered evidence; DreamLab produces explicitly derived masks, object geometry and engine assets.", ST["caption"])]
                figure_inserted.add("segmentation")
            elif title.startswith("9.4") and "roadmap" not in figure_inserted:
                story += [CardFlowDiagram(doc_width, "Controlled route to the integrated version", [
                    ("FREEZE", "seal preferred archive"),
                    ("CONTRACT", "versioned handoff manifest"),
                    ("SEGMENT", "test bounded object set"),
                    ("VALIDATE", "pose scale and orientation"),
                    ("PUBLISH", "label all derivatives"),
                ], "Each gate has a reversible output and can be evaluated without modifying the preservation master."),
                Paragraph("Figure 9.1. Recommended staged integration sequence for object segmentation and engine delivery.", ST["caption"])]
                figure_inserted.add("roadmap")
            i += 1
            continue
        if stripped.startswith("### "):
            flush_para()
            story.append(Paragraph(inline_markup(stripped[4:].strip()), ST["h3"]))
            i += 1
            continue
        if stripped.startswith("- "):
            flush_para()
            items: list[ListItem] = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                item = lines[i].strip()[2:].strip()
                items.append(ListItem(Paragraph(inline_markup(item), ST["body"]), leftIndent=10))
                i += 1
            story.append(ListFlowable(items, bulletType="bullet", bulletColor=ORANGE,
                                      leftIndent=13, bulletFontName=BOLD_FONT, spaceAfter=5))
            continue
        if stripped.startswith("> "):
            flush_para()
            story.append(Paragraph(inline_markup(stripped[2:].strip()), ST["callout"]))
            i += 1
            continue
        if re.match(r"^\*\*(Author|Affiliation|Document status|Evidence date):", stripped):
            i += 1
            continue
        if stripped.startswith("## Nested Cinema"):
            i += 1
            continue
        para.append(line)
        i += 1
    flush_para()
    return story


def build() -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = AcademicDocTemplate(str(OUT_PDF))
    story = manuscript_flowables(doc.width)
    doc.multiBuild(story)
    return OUT_PDF


if __name__ == "__main__":
    output = build()
    print(output)
