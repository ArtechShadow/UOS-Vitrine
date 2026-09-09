# Separated splat to surface mesh (experimental)

In the capture's Objects workspace, run separation, inspect its candidates, then choose **Create object meshes**. A background worker reconstructs all validated separated `.splat` assets from the registered camera views. Coloured triangle PLY downloads appear after the complete batch succeeds. CLI: `.venv/Scripts/python.exe -m vitrine --run-dir runs/NAME object-meshes`.

Install `requirements-mesh.txt` in the reconstruction environment. CUDA/gsplat, registered COLMAP text cameras and the original prepared photographs are required. The existing depth renderer uses per-view intrinsics and undistortion. No training objective or hardware profile changes are made.

Pipeline: validate sidecar hashes → decode the SH0 web splat → render expected depth and colour → back-project opaque pixels → estimate normals → screened Poisson → validate finite vertices and triangle indices → publish an immutable generation. Depth clipping follows scene scale. Inputs stay unchanged. Transformed object assets are refused because they cannot be rendered with the original camera poses without a coordinate conversion.

Outputs live under `object-meshes/published/GENERATION`. `latest.json` is atomically updated only after the whole batch completes. Failure retains the last published meshes. Each manifest records input hashes, camera hashes, method and settings. A subsequent `package` run checks mesh hashes and archives the latest generation under `derivatives/object-meshes`; existing archives are not automatically rewritten. New separation makes previous mesh downloads visibly stale until rebuilt.

This is a surface approximation from the existing SH0 viewing derivative, not a full-SH master conversion, UV texture baking, semantic cleanup or guaranteed watertight topology. Poisson can bridge unseen regions and separation errors become mesh errors. The new path disables the old mesher's unvalidated obscurance-based trimming. Structural validation does not certify shape accuracy.

Verified locally: dependency imports and required MeshLab filters, Python/JavaScript syntax, CLI registration. **Real-object conversion and UI end-to-end validation remain incomplete**: this PC has no separated capture data. Before the demo, compare each surface against its source photographs and splat; record time, peak memory, holes, bridges and disconnected geometry. Do not present this feature as validated until that check is complete.
