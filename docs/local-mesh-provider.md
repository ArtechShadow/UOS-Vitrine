# Optional local mesh provider

The Gaussian baseline remains `GsplatEngine`. Heavy mesh models run in a
separate local process; no TRELLIS, Hunyuan, cloud API or weights are installed
by the core requirements. A configured command is an adapter, not proof that
its model is ready or its output quality is acceptable.

The existing `objects` command retains its validated external SAM2/objects.json
contract. `object-meshes` retains the experimental splat-depth/Poisson route.
The additional `mesh-images` command accepts an independent isolated image set;
it requires neither a trained scene splat nor a COLMAP solve.

An isolated image set contains a UTF-8 manifest and image/mask pairs:

```json
{
  "schema": "vitrine/isolated-images/1",
  "images": [{"image": "images/front.png", "mask": "masks/front.png"}]
}
```

Paths are relative to the manifest directory. Every mask must be nonempty and
have the image's dimensions. Segmentation providers should keep their source
provenance, mask confidence and failure records alongside this manifest. Core
checks shape and readability; it does not claim the mask is semantically correct.

Set `VITRINE_MESH_COMMAND_JSON` to a JSON array containing the separate adapter
executable and arguments. Set `VITRINE_MESH_WEIGHTS_JSON` to a JSON array of
required local weight files. The adapter receives `--input MANIFEST --output
FILE.glb`, must use local assets, and must return a nonzero exit code on failure.

```powershell
python -m vitrine mesh-images --input runs/object-input/isolated.json --output runs/object-mesh-generation
```

The destination must be new. Logs and incomplete files remain in a uniquely
named `.working-*` sibling on failure. Successful output must be a complete
GLB 2.0 container with embedded mesh buffers; its SHA-256 and input-manifest
SHA-256 are saved before atomic publication. The Gaussian pipeline is unaffected
by failure here. TRELLIS/Hunyuan adapters and their weight installers are future
optional integrations, not bundled demo capabilities.
