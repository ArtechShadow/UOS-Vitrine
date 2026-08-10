# UOS Vitrine: An Archive-Grade Local Pipeline for 3D Gaussian Splat Preservation

## Nested Cinema - Vera's Not Alone: Technical and Preservation Report

**Author:** Glenn Watts  
**Affiliation:** XR Lab, University of Salford  
**Document status:** Draft academic report  
**Evidence date:** 6 August 2026

---

# Abstract

UOS Vitrine is a free, fully local and reproducible pipeline for converting photographs and video of a physical environment into a high-quality 3D Gaussian Splat, together with the source material, camera solution, software record and checksums needed to treat the reconstruction as a defensible preservation object. The principal test bed is *Nested Cinema - Vera's Not Alone*, an immersive film installation by Dr Pavel Prokopic at the University of Salford, MediaCityUK. The installation is a constructed room-set inside a white gallery and combines newspaper-clad partitions, domestic furniture, vintage audio equipment, mirrors and moving screens. Its temporary nature makes the distinction between a visually persuasive derivative and an auditable preservation package especially important.

The pipeline combines EXIF-preserving ingest, sharpness-based video-frame selection, multi-camera COLMAP structure-from-motion, MCMC Gaussian densification, random high-resolution crop training, a blur-sensitive SSIM objective, held-out evaluation, full spherical-harmonic PLY export, compact web derivatives, mesh derivation and a checksummed preservation package. The validated laptop run registered 222 of 222 images using two camera models and trained one million Gaussians in 60.4 minutes on an RTX 3060 Laptop GPU. It achieved 25.72 dB PSNR and 0.8167 SSIM on 28 held-out views while peaking at 1.94 GB VRAM. These measurements demonstrate that time, rather than memory, is the binding constraint on the 6 GB laptop.

A controlled RTX 5090 reproduction of the laptop recipe reached 25.064 dB PSNR and 0.8211 SSIM in 2.4 minutes, closely matching the laptop's 25.72 dB and 0.8167 while running approximately 25 times faster. The investigation also isolated the failure of the workstation-archive profile: runs using a 1600-pixel crop from 4096-pixel sources suffered mass opacity collapse, regardless of whether the Gaussian cap was one million or six million. A safe-cap run retained only about 2,200 live Gaussians and reached 17.489 dB; the earlier six-million attempt retained about 80,000 and reached 14.31 dB. Crop/source scale, not `cap_max` or the learning-rate repair, is the variable that consistently separates healthy and collapsed runs. The 5090 control is therefore the strongest working workstation result, while the archive profile remains explicitly unvalidated.

The report also cross-references DreamLab Vitrine, a separate GPL-licensed capture-adaptive system focused on structured object assets and Unreal Engine 5.8 delivery. UOS Vitrine is strongest as a small archive-first pipeline; DreamLab is strongest at SAM3 segmentation, object reconstruction and engine assembly. A file-based integration is proposed in which UOS produces the authoritative archive master and DreamLab consumes registered images, COLMAP poses and a scene splat to create explicitly derived object assets. The two repositories remain separate in code, licence, product identity and preservation responsibility.

---

# Executive Summary

## Purpose

The project addresses a practical cultural-heritage problem: how to preserve a temporary, spatial and immersive artwork using equipment and infrastructure that an institution can operate locally. A Gaussian splat alone is insufficient because it does not identify its source images, camera positions, processing environment or inferred regions. UOS Vitrine therefore treats the splat as one derivative inside a larger evidence package. The work was informed by Glenn Watts's personal, hands-on testing of mobile scanning workflows in Polycam and Luma AI. Those services provided practical baselines for capture effort, processing convenience and visual output, while exposing the need for a workflow with stronger control over source evidence, settings, reproducibility and preservation packaging.

## Principal findings

- Mixed stills and video require separate camera models. Applying one set of intrinsics to both groups can silently warp the reconstruction.
- Glenn Watts personally tested using still photographs and video together in one reconstruction workflow. The test showed that the two sources are complementary: stills carry fine visual detail while video helps continuity and fills coverage gaps.
- The RTX 3060 Laptop's 6 GB VRAM is not the limiting resource for the measured training configuration. The validated run used 1.94 GB; elapsed time is the practical constraint.
- Rasterisation cost depends strongly on the number of Gaussians projected into the camera frustum, not merely the total population.
- SSIM is required because pixel-wise losses are too tolerant of blur. Newspaper detail is a core quality requirement, not a cosmetic preference.
- Random crops preserve access to high-resolution source detail while bounding the rendered pixel count per training step.
- The laptop recipe reproduced on the RTX 5090 at 25.064 dB and 0.8211 SSIM in 2.4 minutes, approximately 25 times faster than the original laptop run.
- The failed archive runs shared `crop=1600` and `source_long_edge=4096`. Their live populations collapsed to 0.2-1.3 percent even when the Gaussian cap changed, isolating crop/source scale as the decisive tested variable.
- The preservation deliverable is the combination of originals, poses, processing record, model, derivatives, limitations and checksums.
- Object segmentation is best integrated through DreamLab's existing pipeline rather than reimplemented in this repository.

## Current deliverables

The implemented command-line pipeline covers ingest, SfM, training, evaluation, export, packaging, verification and mesh derivation. The laptop reconstruction and compact web derivative have been produced. The Windows/RTX 5090 port is operational, and a healthy control run matches the laptop quality in 2.4 minutes. Failed archive runs and their opacity-health evidence are retained as diagnostic records. Practitioner testing with Polycam and Luma AI established experiential baselines, but these were not controlled benchmark runs and are not presented as quantitative comparisons. Formal newspaper-legibility evaluation and a controlled visual comparison with the retained Luma reference remain open quality gates. Object reconstruction and Unreal placement are integration tasks owned by the DreamLab pipeline.

## Recommendation

Retain the original laptop run as the archival quality reference and the RTX 5090 control as the verified workstation reproduction. Disable or clearly quarantine the current workstation-archive profile until a larger crop/source configuration can maintain a healthy live-Gaussian population and improve the accepted metrics. Seal the authoritative full-scene package before creating object or engine derivatives. Exchange data with DreamLab through a versioned, checksummed file contract rather than merging the two systems.

---

# 1. Introduction

## 1.1 The preservation problem

Site-based immersive work is difficult to preserve because its meaning is distributed across physical construction, media content, spatial arrangement, audience movement and time. Conventional documentation normally records selected viewpoints. A game-engine scene or photorealistic reconstruction can support spatial re-presentation, but neither is neutral: every reconstruction encodes assumptions about unseen surfaces, moving imagery, scale and material behaviour.

*Nested Cinema - Vera's Not Alone* makes those tensions visible. The installation premiered at the University of Salford's MediaCity campus in June 2023 as the first public example of Dr Pavel Prokopic's Nested Cinema concept, combining an atmospheric physical set, screened film, cinematic virtual reality and connected lighting and sound [9]. The captured subject is the room-set built inside the gallery. Its newspaper partitions depend on readable surface detail; its furniture and equipment establish period and atmosphere; its mirrors, glass and screens violate the static Lambertian assumptions that make reconstruction tractable. Once de-installed, the installation survives through records rather than continued physical access. The pipeline must therefore preserve not only appearance but also the relationship between observation and inference.

## 1.2 Archive-grade in this project

The phrase *archive-grade* does not claim survey accuracy or formal OAIS conformance. It describes a repeatable engineering and documentation standard:

- Camera originals remain untouched and retain EXIF.
- Derived training images are distinguishable from originals.
- Camera poses and intrinsics are preserved in readable COLMAP text files.
- Hardware, software versions, parameters and measured results are recorded.
- The master splat remains unquantised and retains full spherical harmonics.
- Access copies and meshes are labelled as replaceable derivatives.
- Every packaged file receives a SHA-256 checksum.
- Known limitations and curatorial choices are stated in plain language.

This standard is deliberately achievable on every run. A theoretically richer preservation model that depends on later manual documentation would be less reliable in practice.

## 1.3 Practitioner-led baseline testing

Before and alongside development of UOS Vitrine, author Glenn Watts personally tested the end-to-end experience of scanning physical environments with Polycam and Luma AI. This practitioner testing included the practical acts that shape a real capture workflow: moving through a space, judging coverage, submitting or processing imagery, waiting for reconstruction and inspecting the resulting navigable model. It supplied an important user-centred baseline for what a convenient scanning service makes possible and where an institutional preservation workflow requires additional control.

One specific test combined video footage and high-resolution still photographs in the same reconstruction workflow. Glenn found that the sources performed different but complementary roles. Video supplied a continuous path through the environment and helped bridge positions that were not covered by individual photographs, while the stills retained substantially more surface detail. This finding became a design requirement for UOS Vitrine: mixed sources should be supported deliberately, without pretending that their resolutions, sharpness or camera intrinsics are interchangeable.

The tests are treated as exploratory baselines rather than laboratory benchmarks. Capture sets, processing versions, hidden service parameters and output representations were not held constant, so the report does not infer a numerical ranking between Polycam, Luma AI and UOS Vitrine. The personal tests instead motivated four requirements: retain camera originals; expose the reconstruction stages; preserve camera solutions and parameters; and package fixity, provenance and limitations with the visual model. The retained Luma PLY and Luma-derived keyframes support later matched-view inspection, but the derivative keyframes are not used to train UOS Vitrine.

## 1.4 Contributions

The project contributes a measured laptop-first 3DGS pipeline, a robust mixed-camera data path, a quality objective sensitive to blur, deterministic splat-cost controls, an archive package schema and a documented integration boundary with a larger object-to-Unreal system. It also contributes a diagnosed negative result: the large workstation crop/source configuration drives mass opacity collapse, while the laptop recipe remains healthy and reproduces its quality approximately 25 times faster on the RTX 5090.

## 1.5 Report scope

This report documents UOS Vitrine and the Nested Cinema evidence available on 6 August 2026. DreamLab Vitrine is discussed as a separately owned companion system. Claims about the University pipeline are grounded in this repository and its run reports. Claims about DreamLab are grounded in its v5 report and inspected source snapshot. Mock interface images are not used as evidence of completed features.

---

# 2. Case Study and Source Evidence

## 2.1 Nested Cinema

The test installation spans three nested representational layers: conventional screens, the physical room installation and virtual reality. Contemporary University reporting described *Vera's Not Alone* as a world-first immersive film installation and identified it as the first example of Nested Cinema practice-as-research [9]. In March 2026, the University announced that the wider Nested Cinema research project had secured a £298,000 AHRC Catalyst Award, including £250,000 from UK Research and Innovation, to support development from May 2026 to October 2028 [10]. The capture addressed the physical installation space. The room includes newspaper-covered partition walls, vintage radio and reel-to-reel equipment, rugs, a sofa and other domestic props inside a white gallery.

### AHRC Catalyst project leadership and bid contribution

The University announcement identifies Dr Pavel Prokopic as principal investigator. Professor Andy Miah and Dr Stuart Haffenden Cornejo are named as project co-leads, with Jayne Sayer contributing specialist expertise in dynamic lighting and film production design. It also acknowledges Roger McKinley for strategic support from the project's outset and Dr Mark Dyer for research-development expertise that helped shape and strengthen the AHRC bid [10]. These roles provide the institutional and research context for the preservation case study; they do not imply authorship of the UOS Vitrine software or this report.

| Person | Published role or contribution |
| --- | --- |
| Dr Pavel Prokopic | Principal investigator and originator of Nested Cinema |
| Professor Andy Miah | Project co-lead |
| Dr Stuart Haffenden Cornejo | Project co-lead |
| Jayne Sayer | Dynamic-lighting and film-production-design specialist |
| Roger McKinley | Strategic support from the project's outset |
| Dr Mark Dyer | Research-development guidance that helped shape and strengthen the AHRC bid |

The installation is an unusually demanding reconstruction subject. Fine printed text provides a strong test of sharpness, while screens and reflections create inconsistent observations. Furniture produces occlusion and narrow circulation paths. The white surrounding gallery includes low-texture surfaces that are difficult for feature matching.

## 2.2 Source inventory

The preserved source consists of 72 iPhone 14 Pro stills at 4344 by 5792 pixels and a 60-second 1280 by 720 video containing 1,819 frames. The stills are the archival master and retain EXIF, including focal information. Video fills coverage gaps between still positions. A separate set of 383 1024 by 768 Luma-derived images was produced through Glenn Watts's personal Luma AI workflow testing. It is EXIF-stripped, motion-blurred and excluded from training. The accompanying Luma PLY is retained only as an external quality reference. Polycam was also tested personally as a capture-and-reconstruction baseline; its role in this report is experiential, and no Polycam output is represented as part of the authoritative Nested Cinema evidence package.

## 2.3 Why stills remain the master

The video provides continuity, but its lower resolution and motion blur limit fine detail. The 25 MP stills resolve labels and newspaper textures that are not recoverable from 720p frames. This division of roles was first observed through Glenn Watts's personal combined video-and-stills workflow test and was subsequently confirmed in the implemented Nested Cinema data path. UOS Vitrine therefore maintains the distinction between primary evidence and coverage assistance: stills dominate visual detail, while selected video frames help camera registration and spatial continuity.

## 2.4 Capture interpretation

Moving screens cannot be represented faithfully by a static scene model. Capturing screens off preserves physical geometry but removes content; pausing them invents a representative instant; recording playback retains the event while producing temporal artefacts. The existing capture is preserved unchanged, and the report records screens as time-varying, unreliable regions. No post-hoc decision is presented as neutral.

---

# 3. System Architecture

## 3.1 Pipeline overview

UOS Vitrine is organised as a series of independently runnable stages: ingest, SfM, training, evaluation, export, mesh derivation, packaging and verification. Each stage writes a compact JSON report into its run directory. Expensive stages can therefore be inspected or repeated without rerunning unrelated work.

The same algorithm is used across laptop and workstation tiers. Profiles change measured quantities such as image resolution, crop size, Gaussian cap and iteration count. This is important operationally: hardware scaling does not create a second, divergent preservation method.

## 3.2 Ingest

Ingest classifies media by source folder, resolution and available metadata. Video is sampled at four frames per second. Laplacian variance provides a local sharpness score, and the sharpest candidate in each temporal bucket is selected so quality filtering does not destroy temporal coverage. The still and video groups remain in separate folders because COLMAP's `single_camera_per_folder` mode assigns distinct intrinsics.

For the laptop run, ingest staged 72 stills and 150 video frames. Ninety-three candidate video frames were rejected, including the blurriest 15 percent. The workstation ingest retained 200 video frames and 72 stills.

## 3.3 Structure from motion

COLMAP is run in Docker because it is not available from the target CachyOS repositories. EXIF is preserved through any image resize so focal priors survive. Exhaustive matching is used where loop closure matters, followed by bundle adjustment with principal-point refinement. The solved model is converted to text as part of normal processing.

The multi-camera requirement is non-negotiable. Camera entries are referenced per image throughout the loader and trainer. Code that applies one camera to every image can produce plausible-looking but warped results without raising an error.

## 3.4 Dataset and held-out views

Every eighth registered image is withheld from optimisation. The split is distributed across the capture rather than clustered at one point. Training uses group-aware sampling so the larger video group cannot overwhelm the higher-value stills. Evaluation renders complete views and reports PSNR and SSIM against images never presented to the optimiser.

## 3.5 Gaussian initialisation

The sparse COLMAP cloud supplies initial Gaussian means and colours. Local k-nearest-neighbour distance determines initial scale, avoiding a global radius that would be inappropriate across variable point density. Identity quaternions and high initial opacity provide stable starting conditions.

## 3.6 MCMC densification

The trainer uses gsplat's MCMC strategy rather than threshold-based default densification. A hard `cap_max` fixes the maximum Gaussian population and makes time and memory behaviour predictable. Opacity and scale regularisation are included because they are part of the MCMC method, not optional embellishments.

The cap is primarily a throughput control. Measurements show that the laptop has substantial unused VRAM at the validated settings. Increasing the cap is justified only by measured quality gain, not by available memory.

## 3.7 Random high-resolution crops

Source images are retained on CPU at a profile-selected long edge. Each iteration renders a square crop and adjusts the camera principal point:

```
cx_crop = cx - x0
cy_crop = cy - y0
width = height = crop_size
```

This lets the optimiser encounter full-resolution texture without rendering an entire 2560- or 4096-pixel image at every step. A full 2560-pixel view contains roughly eleven times as many pixels as a 768-pixel crop. The design began as a memory strategy, but measurement shows its principal benefit is throughput.

## 3.8 Photometric objective

The reference objective is `0.8 * L1 + 0.2 * (1 - SSIM)`. SSIM is implemented in pure PyTorch with a separable 11-pixel Gaussian window. It adds 9 to 17 percent per iteration in measured tests. That cost is accepted because pixel-wise losses remain tolerant of blur. During development, a blurred image retained 10.8 dB PSNR while SSIM collapsed to 0.013.

## 3.9 Schedules and export

Position learning rate decays by approximately one hundred times across training so geometry can settle. Spherical-harmonic bands are introduced progressively, allowing base colour and geometry to stabilise before higher-order view-dependent terms. Export clamps only extreme Gaussian scales, expressing the threshold as a fraction of scene scale because COLMAP world units are arbitrary.

The master is an uncompressed full-SH PLY. Compact browser formats are access derivatives and may discard higher-order appearance information. Their convenience does not make them preservation masters.

---

# 4. Hardware Profiles and Measurements

## 4.1 Laptop measurement

The target laptop uses an RTX 3060 Laptop GPU with 6 GB VRAM. A benchmark with 1.5 million Gaussians, 768-square output and degree-three spherical harmonics measured 1.46 GB for full forward and backward processing. Frustum occupancy, not total population alone, dominated iteration time.

| Gaussians projecting into view | Measured time per iteration |
| --- | --- |
| 94 percent | 500 ms |
| 39 percent | 222 ms |
| 15 percent | 95 ms |

An interior camera commonly observes roughly one third of the room. The middle benchmark therefore provides the practical basis for laptop estimates.

## 4.2 Workstation measurement

An RTX 5090 benchmark reduced a comparable step to approximately 12-14 ms. At that speed, Python and kernel-launch overhead become more visible and the relationship between frustum fraction and time flattens. The workstation throughput factor is measured rather than inferred from specifications.

## 4.3 Profile table

| Profile | Source long edge | Crop | Gaussian cap | Iterations | SH degree |
| --- | ---: | ---: | ---: | ---: | ---: |
| laptop-draft | 1600 | 512 | 400,000 | 7,000 | 2 |
| laptop-standard | 2048 | 768 | 1,000,000 | 15,000 | 3 |
| laptop-archive | 2560 | 768 | 1,500,000 | 30,000 | 3 |
| workstation-draft | 2048 | 800 | 1,000,000 | 7,000 | 2 |
| workstation-standard | 3200 | 1280 | 3,000,000 | 20,000 | 3 |
| workstation-archive | 4096 | 1600 | 6,000,000 | 30,000 | 3 |

The workstation-archive row records the investigated configuration, not an approved preset. On the present trainer it is actively harmful: `crop=1600` with `source_long_edge=4096` causes mass opacity collapse. It must not be presented as archive quality or used for production until a replacement profile passes the held-out and live-population gates.

## 4.4 Fragile CUDA toolchain

The project has no system CUDA toolkit. CUDA 13 `nvcc` comes from pip wheels and cannot compile against the installed GCC 16 headers. A compatible GCC 15 toolchain is required. gsplat's cached CUDA extension is already valid, but changing `TORCH_CUDA_ARCH_LIST`, `CC` or `CXX` alters the build command and invalidates that cache. A multi-minute import generally indicates an unintended rebuild.

The runtime also handles missing unversioned CUDA library symlinks and preloads `libcudart.so.13` globally before importing the extension. These are operational details with direct reproducibility consequences and therefore belong in the preservation record.

---

# 5. Results

## 5.1 Laptop-standard reconstruction

The validated laptop run staged 222 images and registered every one of them. COLMAP recovered two camera models and 111,203 sparse points. SfM took 36 minutes on CPU in the original run; Docker GPU passthrough was repaired later and reduces comparable SfM processing to approximately five minutes.

Training completed 15,000 iterations in 60.4 minutes. The final model contains one million Gaussians and uses full degree-three spherical harmonics. Peak VRAM was 1.94 GB. The final held-out metrics were 25.72 dB PSNR and 0.8167 SSIM.

| Step | PSNR | SSIM | Gaussians |
| ---: | ---: | ---: | ---: |
| 2,500 | 16.42 | 0.550 | 295,041 |
| 5,000 | 16.45 | 0.557 | 999,097 |
| 7,500 | 17.34 | 0.565 | 1,000,000 |
| 10,000 | 17.33 | 0.571 | 1,000,000 |
| 12,500 | 18.91 | 0.586 | 1,000,000 |
| 15,000 final | 25.72 | 0.817 | 1,000,000 |

The final metric jump occurs after MCMC densification ends at 75 percent of the run. The remaining iterations refine a fixed population. This pattern supports retaining a substantial post-densification optimisation period.

## 5.2 Export and viewing

The full-SH `scene.ply` is approximately 248 MB. The compact `.splat` derivative is 17.8 MB. Local viewers were used without uploading the capture. On an Optimus laptop the browser initially rendered WebGL on the Intel integrated GPU at approximately 15 frames per second. Forcing NVIDIA PRIME render offload increased performance to approximately 94 frames per second.

The initial viewer orientation was also incorrect. A scene-up vector was recovered from all 222 COLMAP camera poses, producing `[0.1105, -0.9937, 0.0168]`. This corrected the visual level without changing the archival model.

## 5.3 RTX 5090 control and archive-profile collapse

The Windows/RTX 5090 investigation reused a single registered dataset of 272 images, with 238 views for training and 34 for evaluation. The decisive control ran the proven laptop-standard recipe: a 768-pixel crop, 2048-pixel source long edge, one-million cap and 15,000 iterations. It completed in 2.4 minutes at 1.75 GB peak VRAM and achieved 25.064 dB PSNR and 0.8211 SSIM. This matches the original laptop result within expected run and dataset variation while reducing training time by approximately 25 times.

| Run | Crop / source | Cap | Live exported Gaussians | Time | PSNR | SSIM |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| RTX 3060 baseline | 768 / 2048 | 1.0M | about 555k (55%) | 60.4 min | 25.72 | 0.8167 |
| RTX 5090 control | 768 / 2048 | 1.0M | about 528k (53%) | 2.4 min | 25.064 | 0.8211 |
| RTX 5090 quality candidate Q1 | 1024 / 3072 | 1.5M | about 940k (62.7%) | 3.0 min | 24.709 | 0.8163 |
| RTX 5090 safe-cap | 1600 / 4096 | 1.0M | about 2.2k (0.2%) | 7.6 min | 17.489 | 0.6897 |
| RTX 5090 six-million archive | 1600 / 4096 | 6.0M | about 80k (1.3%) | 23.6 min | 14.31 | 0.6591 |

The controlled matrix isolates crop/source scale as the variable that tracks the failure. Both healthy runs use 768/2048; both collapsed runs use 1600/4096. Reducing `cap_max` from six million to one million did not restore the live population, and correcting the position-learning-rate horizon did not restore it either. Those changes address genuine independent issues, but they are not the cause of this regression.

The opacity trace supplies the mechanism evidence. In the large-crop runs, the fraction of Gaussians above the live-opacity threshold falls catastrophically as training proceeds. The best-supported explanation is that one random 1600-pixel crop from a 4096-pixel source does not revisit each Gaussian frequently enough for reconstruction gradients to counter global opacity and scale regularisation. MCMC then relocates dead splats from a shrinking live pool, reinforcing the collapse. This mechanism remains a testable causal hypothesis; the empirical conclusion that 1600/4096 is unsafe in the current trainer is definitive.

The first post-diagnosis quality candidate tested source long edge 3072, crop 1024, a 1.5-million cap and opacity regularisation reduced from 0.01 to 0.002. It stayed healthy and exported 940,495 live Gaussians, demonstrating that reduced opacity pressure prevents the catastrophic collapse at this intermediate scale. However, its final 24.709 dB PSNR and 0.8163 SSIM did not beat the simpler control. Greater nominal resolution and a larger healthy population therefore remain insufficient evidence of greater visual quality.

A matched-view diagnostic used held-out still `IMG_6351`, which was excluded from training, to compare the source photograph, the 5090 control and Q1 from exactly the same COLMAP camera. Both splat renders soften the sofa fabric, newspaper print and equipment details that remain visibly sharp in the source, while Q1 is marginally less accurate numerically and introduces additional smearing. This is not evidence that the capture needs more images overall. It points first to mixed-source optimisation and pose or intrinsic consistency: the required detail exists in the current still photographs but is not surviving reconstruction. The next controlled test should therefore be a stills-only baseline using the existing archival masters. Selected video frames should then be reintroduced only where they demonstrably close a coverage gap. Additional capture is justified only for surfaces absent from the still set, and should prioritise sharp close-range photographs with strong overlap, stable focus and stable exposure; adding more 720p video indiscriminately is likely to reinforce softness rather than remove it.

The 5090 control is the honest working workstation result. It does not yet exceed the laptop's quality, but it reproduces that quality at transformative speed. Exceeding the baseline requires a controlled multi-variable study of crop, source resolution, Gaussian cap, opacity regularisation and possibly multi-crop batches, with live population monitored as an acceptance metric.

### Training energy and electricity-cost comparison

The Windows runs sample GPU power through `nvidia-smi` and integrate it over training. Monetary cost uses the recorded tariff of £0.2703 per kWh. Values below one penny are retained at higher precision here rather than rounded to £0.00.

| RTX 5090 run | Time | Energy | Estimated electricity cost | Outcome |
| --- | ---: | ---: | ---: | --- |
| Control, 768/2048 | 2.4 min | 0.013 kWh | £0.00351 | Best verified quality |
| Q1, 1024/3072, opacity reg 0.002 | 3.0 min | 0.018 kWh | £0.00487 | Healthy but lower metrics |
| Safe-cap, 1600/4096 | 7.6 min | 0.049 kWh | £0.01324 | Opacity collapse |

These figures currently cover sampled GPU power rather than whole-system wall energy. They are suitable for relative trainer comparisons on this workstation but should not be represented as the complete institutional electricity cost. A wall-socket meter remains the preferred boundary for the planned sustainability integration.

## 5.4 Quality gates still open

The formal one-to-one newspaper-clipping comparison has not been completed, so readability is not claimed as validated. The Luma reference has also not undergone a controlled matched-view comparison. These tests must use saved cameras, clearly identified source crops and the same display conditions. A visually attractive orbit alone is not sufficient evidence.

## 5.5 Negative results as evidence

Preservation engineering benefits from recording failure. Omitting the workstation regression would create the false impression that six million Gaussians are inherently superior. The failure also demonstrates why exported live population belongs beside PSNR and SSIM: a nominal one- or six-million cap says little if 98.7-99.8 percent of the population becomes effectively transparent. Reporting the full matrix constrains future claims, directs experiments toward crop sampling and regularisation, and preserves the difference between throughput capability and reconstruction quality.

---

# 6. Preservation Package

## 6.1 Package structure

The package contains `originals/`, `sfm/`, `model/`, `derivatives/`, `manifest.json` and a human-readable `README.md`. Originals are copied without resizing, recompression or metadata removal. The SfM directory contains camera intrinsics, poses, sparse points and the COLMAP database. The model directory contains the full-SH master PLY. Derivatives hold access copies, meshes and previews.

## 6.2 Manifest

The manifest records the package schema, UTC creation time, title, subject, software versions, GPU, CUDA version, COLMAP image identifier, Vitrine revision, profile settings, ingest/SfM/training reports, file sizes and SHA-256 checksums. The package verifier re-hashes each listed file and reports missing or changed content.

## 6.3 Why PLY remains the master

Compressed splat formats are useful for delivery but still change rapidly. PLY is older, widely readable and transparent enough to inspect without a dedicated viewer. The master retains position, scale, rotation, opacity and all spherical-harmonic coefficients. Derivatives may be replaced as viewers evolve.

## 6.4 Honest limitations

A splat interpolates views; it does not measure surfaces. Unseen regions are inferred. Mirrors, screens, glass and polished metal are represented according to their observed pixels rather than physical optics. Moving content is frozen or smeared. COLMAP recovers arbitrary scale unless an external measurement establishes world units. These limitations belong in the deposit record rather than in small-print documentation outside the package.

## 6.5 Relationship to repository standards

The package does not claim to implement OAIS or PREMIS. It follows compatible instincts: preserve originals, document agents and events, maintain fixity, distinguish masters from derivatives and record significant properties. Its compact schema is designed to be produced automatically by every run.

---

# 7. UOS Vitrine and DreamLab Vitrine

## 7.1 Two systems with one family name

UOS Vitrine and DreamLab Vitrine address related parts of the same cultural-digitisation problem but are separate products. The University pipeline asks whether a temporary installation can be captured, measured and packaged locally on modest hardware. DreamLab's v5 system asks how capture can be diagnosed and routed into a structured scene of room representation, per-object assets and Unreal Engine delivery.

The distinction should appear in stakeholder language: **UOS Vitrine (preservation)** and **DreamLab Vitrine (lab stack)**. Vitrine Capture is a third FastAPI and React product and is outside this report.

## 7.2 Comparative architecture

| Capability | UOS Vitrine | DreamLab Vitrine |
| --- | --- | --- |
| Primary outcome | Auditable preservation package | Structured game-ready scene |
| Core scene | gsplat MCMC PLY | LichtFeld or alternative scene path |
| Hardware posture | 6 GB laptop first | Multi-service GPU appliance |
| Mixed still/video cameras | Proven with two models | Broader capture routing |
| Object segmentation | External integration | SAM3/SAM3.1 pipeline |
| Object reconstruction | External integration | TRELLIS.2 primary; other backends |
| Unreal delivery | Handoff only | FBX/Nanite and splat routes |
| Archive fixity | First-class SHA-256 package | Run lineage and delivery metadata |
| Licence | MIT project code | GPL project code plus model licences |

## 7.3 DreamLab v5 report

The companion report is *Vitrine: A Capture-Adaptive Video-to-3D-Scene Pipeline for Unreal Engine 5.8*, v5, authored by Dr John O'Hare for DreamLab AI Consulting Ltd. It documents a 50-page academic treatment of capture routing, the DreamLab reference run, object generation, mesh experiments and UE 5.8 delivery. This UOS report does not reuse its prose, figures or brand treatment. It cross-references the system where responsibilities meet.

## 7.4 What inspection of the current DreamLab code established

DreamLab already provides SAM3 concept segmentation, per-frame masks, provenance-carrying object crops and depth-aware multi-view projection of masks through COLMAP cameras. It can isolate per-object Gaussian subsets and feed clean source crops into object reconstruction. It carries object-lineage metadata and exports textured assets for downstream assembly.

Two important limitations remain for the proposed pilot. Current placement solves position and uniform scale from the isolated Gaussian subset but marks orientation as unsolved. The pipeline also retains the full scene splat; it does not currently create a cleaned background splat with accepted object Gaussians removed. These limitations should be resolved explicitly rather than hidden by manual scene adjustment.

## 7.5 File handoff contract

UOS should export a versioned handoff containing the authoritative scene-Ply hash, registered staged images, COLMAP text model, capture metadata, coordinate convention, parent archive-manifest hash and stable object requests. DreamLab should validate the handoff and bypass its own ingest, SfM and scene retraining.

The initial object pilot covers the vintage radio, reel-to-reel equipment and sofa. SAM3 masks and multi-view projections identify scene-space support. Source-image crops condition the object generator. Generated GLB and FBX assets return with stable IDs, model/checkpoint provenance, placement confidence and preview renders.

## 7.6 Master and cleaned derivative

The original UOS full-scene PLY remains byte-identical and authoritative. DreamLab may create `scene_cleaned.ply` by subtracting the exact union of accepted object selections. The selection indices, thresholds and before/after counts must be preserved. No inferred background fill is added by default. The cleaned scene and generated objects are engine derivatives linked to the archive master, not replacements for it.

## 7.7 Placement validation

Position and scale should initialise from the scene-space Gaussian subset. Orientation should be solved against at least three per-frame silhouettes, with bounded refinement and a reported mask-overlap score. Low-confidence placements remain `needs_review` and must not be presented as validated Unreal assets. Generated backsides and occluded surfaces must be labelled as inferred.

## 7.8 Licensing and ownership

The repositories remain separate. Importing GPL DreamLab pipeline code into the MIT UOS package would change the licensing implications. File contracts preserve organisational and legal boundaries. Model checkpoint licences, media rights and generated-asset terms must be recorded alongside each derivative.

---

# 8. Curatorial, Ethical and Technical Limitations

## 8.1 Evidence and inference

The model is most reliable near observed camera positions. The viewer can move beyond that evidence and encounter plausible but unsupported surfaces. Preservation interfaces should therefore make source views and camera paths available rather than presenting unrestricted navigation as equivalent to direct observation.

## 8.2 Screens and time-based media

The installation's moving screens are constitutive, not incidental. A static splat cannot preserve their temporal sequence. The physical room reconstruction should be accompanied by original audiovisual works, playback documentation and rights information where available. The spatial model is one layer of the preservation record.

## 8.3 Reflective materials

Mirrors and polished equipment can create false correspondences and view-dependent artefacts. Masks may improve geometric reconstruction, but masking is itself an interpretive act. Any use of masks must be stored and documented rather than applied destructively to originals.

## 8.4 Generated object assets

Single- or limited-view object generation produces geometry and texture that were not observed. These assets are useful for interaction and game-engine collision but should carry `surface: inferred` lineage. The authoritative scene splat and source images remain available for comparison.

## 8.5 Scale

COLMAP geometry has arbitrary scale. Relative placement is consistent inside one solve, but dimensions are not metres unless established through an external measurement. Future captures should include a measured scale bar or surveyed distance when real-world scale is significant.

## 8.6 Sustainability

Local processing reduces dependence on a cloud reconstruction service, but it does not make computation environmentally or financially free. CUDA, compiler and browser compatibility can change, and longer or larger training runs consume additional electricity even when they fail to improve quality. The strongest long-term preservation strategy is still to preserve originals, poses and parameters so future tools can reproduce the representation, but operational sustainability also requires measured energy use rather than assumptions based on GPU class.

Glenn Watts plans to integrate electricity monitoring expressed in kilowatt-hours and pounds per kilowatt-hour. Each pipeline run should record energy consumed by stage, the tariff used for the calculation and the resulting estimated electricity cost. The tariff must be stored with its currency, effective date and source because £/kWh changes over time. Reporting both kWh and cost prevents a cheap tariff from being mistaken for an efficient computation and allows later comparison under a common price.

| Proposed sustainability field | Purpose |
| --- | --- |
| `energy_kwh` | Measured electrical energy consumed by the run or stage |
| `tariff_gbp_per_kwh` | Electricity price applied to the calculation |
| `estimated_cost_gbp` | `energy_kwh` multiplied by the recorded tariff |
| `measurement_scope` | GPU-only, workstation or wall-socket measurement boundary |
| `measurement_device` | Meter, API or telemetry source used |
| `carbon_intensity` | Optional time-and-region-specific emissions factor |

The preferred measurement boundary is whole-system wall energy because GPU telemetry omits CPU, memory, storage and conversion stages. If only GPU power telemetry is available, the manifest should identify that narrower boundary explicitly. Sustainability comparisons should be paired with PSNR, SSIM and completion status: the relevant question is not simply which run used less electricity, but which run produced an accepted preservation result for the least measured energy and cost.

---

# 9. Recommendations and Roadmap

## 9.1 Replace the unsafe workstation-archive profile

The regression is now isolated sufficiently to prevent accidental reuse. Mark the current 1600/4096 workstation-archive profile as unvalidated or disable it. Preserve the 25.064 dB RTX 5090 control as the verified workstation baseline. The first intermediate experiment at crop 1024, source 3072, cap 1.5 million and opacity regularisation 0.002 remained healthy but scored lower, so it must not be promoted. Future work should test one remaining axis at a time, including longer post-densification refinement and multi-crop batches; monitor live-opacity percentage throughout training and reject runs that fall below a declared health threshold.

## 9.2 Complete formal visual gates

Before requesting a reshoot, run and retain a stills-only 5090 baseline from the existing 72 archival masters. Compare it against the mixed stills-and-video control from the same held-out cameras. Reintroduce only the minimum video subset needed for confirmed registration or coverage gaps, and document any region that genuinely lacks photographic coverage.

Create a fixed set of held-out newspaper regions with source and rendered crops at matched resolution. Save the exact camera definitions and create a signed contact sheet. Align a small set of viewer cameras for a transparent Luma comparison. Avoid a single subjective orbit as the sole quality claim.

## 9.3 Seal the preferred archive

Package and verify the preferred scene together with originals, COLMAP text model, training report, derivatives and limitations. Store the package under a stable run identifier. Treat later encodings and object assets as linked supplemental derivatives.

## 9.4 Implement the DreamLab handoff

Add export and verification commands in UOS and a trusted-package importer in DreamLab. Preserve stable IDs across masks, crops, selections, reconstructed assets and placements. Disable cloud fallbacks for preservation runs. Complete orientation solving and cleaned-splat generation before claiming an interactive object pilot.

## 9.5 Capture future installations systematically

Lock exposure and white balance, retain maximum-resolution stills, close loops at more than one height and photograph critical details from several angles. Run a draft reconstruction while the installation is still available. Record decisions about screens, mirrors and occluded areas in the capture manifest.

## 9.6 Add energy and electricity-cost monitoring

Integrate stage-level energy measurement into the normal run reports and preservation manifest. Record kWh, the applicable £/kWh tariff, estimated cost, measurement boundary and device. Begin with ingest, SfM and training as separate stages so the project can distinguish GPU training cost from CPU, Docker and image-processing overhead. Use the results to compare profiles on accepted quality per kWh and accepted quality per pound, rather than rewarding a fast run that fails the visual or metric gates.

---

# Appendix A. Operator Playbook

## A.1 Pre-capture

- Confirm rights and preservation scope.
- Decide how screens and mirrors will be handled.
- Lock exposure and white balance.
- Keep flash off and ISO as low as practical.
- Confirm maximum-resolution capture and EXIF retention.
- Place a measured scale reference if physical dimensions matter.

## A.2 Coverage

Walk the room perimeter while shooting inward, then repeat at another height. Maintain approximately 60-80 percent overlap. Close the loop by revisiting the starting positions. Add dedicated close passes for text, controls, labels and significant props. Photograph floor-wall junctions, corners, thresholds and accessible areas behind furniture.

## A.3 Video

Use video as connective coverage rather than the detail master. Move slowly, pause at corners and do not zoom. Keep stills and videos in separate source folders. The ingest stage will sample and score frames while preserving temporal distribution.

## A.4 Commands

```
python -m vitrine doctor
python -m vitrine profiles
python -m vitrine run --run-dir runs/<name> --quality archive \
  --title "<title>" --subject "<subject>"
python -m vitrine evaluate --run-dir runs/<name>
python -m vitrine verify runs/<name>/archive
```

## A.5 Acceptance checks

- All intended camera groups appear separately in `cameras.txt`.
- Registration rate and sparse-point count are recorded.
- Held-out PSNR and SSIM are present.
- Fine-detail contact sheets are reviewed.
- Full-SH PLY and web derivative open locally.
- Package verification reports no missing or changed files.
- Limitations and interpretive capture decisions appear in the manifest and README.

---

# Appendix B. Preservation Package Schema

```
archive/
  originals/       untouched camera photographs and video
  sfm/             cameras.txt, images.txt, points3D.txt, database.db
  model/           scene.ply, full spherical harmonics
  derivatives/     web splat, mesh, previews and future access formats
  manifest.json    versions, settings, metrics and SHA-256 fixity
  README.md        plain-language context and verification instructions
```

The manifest schema identifier is `vitrine/preservation-package/1`. Verification detects corruption or unintended change; it does not replace a backup and replication policy.

---

# Appendix C. Environment Snapshot

| Component | Validated environment |
| --- | --- |
| Project Python | 3.11 virtual environment |
| Torch | 2.11 with CUDA 12.8 in project venv |
| gsplat | 1.5.3 |
| Laptop GPU | RTX 3060 Laptop, 6 GB, compute 8.6 |
| Workstation GPU | RTX 5090 class, 32 GB |
| COLMAP | Docker image |
| Meshing | pymeshlab screened Poisson |
| Master splat | Full-SH 3DGS PLY |
| Integrity | SHA-256 manifest |

The system CUDA toolchain and project venv are deliberately separated. Toolchain health must be checked before a run, and a fast cached gsplat import is an operational acceptance test.

---

# Appendix D. Document Control

| Field | Value |
| --- | --- |
| Title | UOS Vitrine: An Archive-Grade Local Pipeline for 3D Gaussian Splat Preservation |
| Author | Glenn Watts |
| Affiliation | XR Lab, University of Salford |
| Status | Draft academic report |
| Evidence date | 6 August 2026 |
| UOS evidence | Repository docs and run reports in UOS_Vitrine |
| DreamLab reference | Vitrine v5 academic report and inspected repository snapshot |
| Next revision | After safe workstation-profile tuning, legibility gate and object handoff pilot |

---

# References

[1] B. Kerbl, G. Kopanas, T. Leimkuehler and G. Drettakis. 3D Gaussian Splatting for Real-Time Radiance Field Rendering. ACM Transactions on Graphics, 2023.

[2] T. Karras et al. 3D Gaussian Splatting as Markov Chain Monte Carlo. 2024.

[3] J. L. Schoenberger and J.-M. Frahm. Structure-from-Motion Revisited. CVPR, 2016.

[4] Nerfstudio Project. gsplat documentation and MCMC strategy API, version family used by this project.

[5] Meta AI Research. SAM 3 and SAM 3D Objects repositories and model documentation, accessed August 2026.

[6] Dr John O'Hare. Vitrine: A Capture-Adaptive Video-to-3D-Scene Pipeline for Unreal Engine 5.8. DreamLab AI Consulting Ltd, v5, June 2026.

[7] Glenn Watts. UOS Vitrine repository documentation: Project Plan, Progress Report, Capture SOP and Preservation Package, evidence snapshot 6 August 2026.

[8] Open Preservation Foundation and Library of Congress guidance on fixity, preservation masters and sustainable formats, consulted for general preservation framing.

[9] University of Salford. "University to showcase world-first cinematic experience that seeks to blur line between real and imaginary." University News, 1 June 2023. https://www.salford.ac.uk/news/university-to-showcase-world-first-cinematic-experience-that-seeks-to-blur-line-between-real-and-imaginary (accessed 6 August 2026).

[10] University of Salford. "Pioneering cinema research project Nested Cinema secures AHRC Catalyst Award." University News, 2 March 2026. https://www.salford.ac.uk/news/pioneering-cinema-research-project-nested-cinema-secures-ahrc-catalyst-award (accessed 6 August 2026).
