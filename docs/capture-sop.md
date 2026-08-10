# Capture SOP — photographing a room for 3D reconstruction

Written against the *Nested Cinema* capture, but general to any enclosed
installation space.

**The single most important thing to understand:** reconstruction quality is
decided at capture time. No amount of processing recovers a surface that was
never photographed, or sharpens a frame that was smeared by camera shake. An
hour of careful shooting saves a day of disappointing runs.

If you take nothing else from this document: **shoot a draft the same day,
before the installation comes down.** A `draft` run takes minutes and tells you
whether the capture registers at all. Discovering a coverage hole while the set
still exists is a minor inconvenience; discovering it afterwards is permanent.

---

## Equipment

A recent phone is genuinely fine — the *Nested Cinema* stills were shot on an
iPhone 14 Pro and carry everything the pipeline needs. What matters far more
than the camera is that **the settings do not change between frames**.

A tripod is not required and often counterproductive: it slows you down and
tempts you into too few positions. Steady handheld with good light beats
tripod-bound with sparse coverage.

---

## Camera settings

| Setting | Do this | Why |
|---|---|---|
| Exposure | **Lock it** | Auto-exposure changes brightness between frames; the optimiser then has to explain the same wall being two different colours, and resolves it as blotching |
| White balance | **Lock it** | Same reasoning, in colour |
| Focus | Lock, or tap the mid-depth of each shot | Autofocus hunting mid-capture produces soft frames that fail feature matching |
| ISO | As low as the light allows | Noise is not structure, but the feature detector cannot tell |
| Flash | **Off** | A moving light source makes every surface look different from every position — the one thing that most reliably breaks reconstruction |
| Format | JPEG or HEIC, keep EXIF | COLMAP seeds focal length from EXIF. Stripping it makes the solver start from a guess |
| Resolution | Maximum | You can always downscale later; you cannot upscale |

Dim installations tempt you into a wide aperture. Resist a little: shallow
depth of field means only part of each frame is sharp, and blurred regions
contribute nothing.

---

## Coverage

Think in terms of **every surface seen from at least three positions, well
apart**. Two views are enough to place a point but not to be confident about
it; three or more is what makes geometry stable.

### The orbit

Walk the perimeter shooting inward, then walk it again at a different height.

- **Overlap 60–80%** between consecutive frames. Roughly: move about a
  pace between shots, no more.
- **Two heights minimum** — around chest and around knee. A single height
  leaves the tops and undersides of objects unresolved.
- **Close the loop.** Return to where you started and re-shoot the first few
  positions. This is what lets the solver recognise that the end of the walk
  meets the beginning, instead of letting the reconstruction drift open like a
  spiral.

### Detail passes

For anything that matters — instrument panels, labels, the newspaper clippings
on the walls — do a **separate close pass**: fill the frame, keep the same
60–80% overlap, and shoot it from three or four angles.

This is the part most captures skip, and it is precisely what determines
whether text is readable in the result. Fine detail must be *resolved in the
source frames*; the optimiser cannot invent it.

### Common holes

- **Corners and the floor–wall junction** — easy to miss, very visible when
  absent.
- **Behind and under furniture.** If you cannot photograph it, accept that the
  model will invent it, and note that in the package.
- **Ceilings**, if they are part of the space.
- **Doorways and thresholds** — the transition between the set and the
  surrounding room needs views from both sides.

---

## Video

Video complements stills; it does not replace them. Its value is continuous
coverage that ties the still positions together.

- **Move slowly.** Slower than feels natural — roughly a slow walking pace,
  and pause briefly at corners.
- **Do not zoom.** Zooming mid-shot changes the intrinsics continuously, which
  the single camera model cannot represent.
- Keep it short and deliberate. 60 seconds of steady walking is worth more
  than five minutes of wandering.

The pipeline extracts frames at 4 fps and keeps the sharpest in each temporal
bucket, discarding the blurriest 15% outright. On the *Nested Cinema* footage
that rejected 93 of 243 frames — nearly two in five — which is normal for
handheld video in low light.

Put video in `source/video/` and stills in `source/stills/`. Separate folders
mean separate camera models, which is required and not optional.

---

## Difficult materials

These do not break the run; they produce confident, wrong geometry. Decide in
advance how to handle them, and **record the decision** — it is an
interpretive choice about what is being preserved.

**Mirrors** reconstruct as a window into a plausible fake room. The
*Nested Cinema* set has one in the corner. Options: cover it during capture,
mask it in post, or accept and document it.

**Screens** are worse, and this installation is full of them. A playing screen
is both view-dependent and time-varying — it looks different from every
position *and* at every moment. The optimiser will either smear it or bake in
one arbitrary frame.

For *Nested Cinema* specifically this is a real curatorial question, because
the moving image is not incidental to the work. Three defensible answers:

1. Capture with screens **off** — preserves the physical set honestly, loses
   the work's content.
2. Capture with screens **paused on a representative frame** — preserves the
   composition, but freezes something that was never static.
3. Capture **playing** and accept the artefacts — most honest to the
   experience, worst as geometry.

There is no neutral option, which is exactly why it belongs in the manifest.

**Glass, gloss and polished metal** shift their highlights as you move. Extra
views from more angles help; they will never be perfect.

**Blank painted walls** give the feature detector nothing to match. If
registration fails in an area of flat colour, that is usually why.

---

## Before you leave

A short checklist, worth actually running:

- [ ] Every surface photographed from **three or more** positions
- [ ] Loop closed — start positions re-shot at the end
- [ ] Two or more heights
- [ ] Close detail pass on anything with text or fine structure
- [ ] Exposure and white balance were locked throughout
- [ ] Corners, floor edges and thresholds covered
- [ ] Reflective and screen-bearing surfaces handled, and the decision noted
- [ ] **A `draft` run completed while the set is still standing**

That last one is the difference between a capture you can fix and one you
cannot.

---

## Rough numbers

From the *Nested Cinema* capture, as a starting point rather than a rule:

| | Count |
|---|---|
| Stills, 25 MP | 72 |
| Video | 60 s at 720p → 243 frames extracted, 150 kept |
| Registered into one model | 2 camera models |

For a room of roughly 4 × 5 m, 72 stills is a workable minimum. More is better,
especially for the detail passes — the marginal cost of another 50 photographs
is a few minutes at capture time and nothing at all afterwards.
