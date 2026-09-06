# UOS Vitrine academic report

This folder contains the editable manuscript and reproducible PDF build for:

**UOS Vitrine: An Archive-Grade Local Pipeline for 3D Gaussian Splat Preservation**

Author: Glenn Watts, XR Lab, University of Salford.

## Build

From the repository root:

```powershell
python report/uos-vitrine/build_report.py
```

The build reads documentation, selected source photographs and completed JSON
run reports. It does not write to `source/` or `runs/`. The PDF is written to:

```text
output/pdf/UOS-Vitrine-Academic-Report-Draft.pdf
```

Dependencies: `reportlab`, `Pillow`, `pypdf`, `pdfplumber` and PyMuPDF for
render-based QA.

## Evidence policy

- `Branding/mockups/` is not used as proof of implemented features.
- Source photographs are captioned as source observations.
- Measured claims come from JSON run reports or the repository's documented
  benchmarks.
- The DreamLab system is cross-referenced as a separate product and codebase.

