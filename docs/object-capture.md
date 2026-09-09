# Object capture

Choose **Create a splat → Object**, add photographs or video, name the object and submit. The capture opens in the live construction workspace, using the same image preparation, COLMAP, splat training and preservation packaging as Scene capture.

Keep the object stationary and move the camera around it. Capture overlapping views at several heights, including the top and visible details. Keep focus, lighting and the background consistent. Do not flip the object or use a turntable with a stationary background. Hidden undersides are not reconstructed from unseen evidence. Reflective, transparent and featureless objects can be difficult to reconstruct.

This mode produces an object-focused Gaussian Splat, not an automatically isolated object or watertight mesh. Background geometry may remain. Existing object-isolation controls still require their separate compatible sidecar.

The upload API accepts optional `capture_type=scene|object` (default `scene`). The run ticket, run summary and archive manifest retain the type. CLI users can pass `run --capture-type object`; a later `package` invocation reuses the recorded type unless explicitly overridden. Historical captures without a type display as Scene. Hardware profiles, objectives and training parameters are unchanged.

Validation: syntax and browser controls checked locally. A full real-object capture through reconstruction and packaging remains unverified until real capture media and the workstation reconstruction dependencies are available. Object quality and processing time have not been benchmarked.
