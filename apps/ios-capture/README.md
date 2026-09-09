# Vitrine Capture (iOS)

Guided high-quality room capture for the Vitrine preservation pipeline.
The app does **not** reconstruct. It locks the camera, walks the operator
through the capture SOP, and exports a session folder that
`vitrine ingest --session` consumes.

```text
iPhone  →  capture session zip  →  workstation ingest  →  SfM / train / package
```

Photographs are the preservation master. LiDAR and ARKit are guidance only.

## Requirements

- macOS with Xcode 15+
- iOS 17+
- A physical iPhone. Simulator has no camera, LiDAR, or AE/AWB lock.
- LiDAR coverage heatmap needs a Pro device; other iPhones still get pose,
  overlap, sharpness and the SOP wizard.

This Windows development machine cannot compile the app. Open the sources
on a Mac:

1. Create a new iOS App in Xcode (`VitrineCapture`, SwiftUI, iOS 17).
2. Replace the generated files with the contents of `VitrineCapture/`.
3. Add camera, microphone, and (for LiDAR) world-sensing usage strings from
   `Info.plist`.
4. Sign with the XR Lab team account and install on a device.

`project.yml` is an [XcodeGen](https://github.com/yonaskolb/XcodeGen) spec if
you prefer `xcodegen generate` to a hand-made project.

## What it exports

See `docs/capture-session.md`. The zip must contain:

- `capture.json` (`vitrine/capture-session/1`)
- `stills/<camera-group>/` original HEIC/JPEG, EXIF intact
- `video/` original MOV
- optional `sidecar/` (never ingested)

Validate on the workstation:

```bash
python -m vitrine capture-session validate path/to/session.zip
python -m vitrine ingest --session path/to/session.zip --run-dir runs/my-capture
```

## Out of scope

On-device COLMAP, splat training, cloud upload, Android.
