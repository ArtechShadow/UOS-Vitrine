# Local viewer dependencies

Pinned copies for offline presentation. No capture data is included.

- Three.js 0.160.0: `build/three.module.js`, `examples/jsm/controls/OrbitControls.js`. MIT; see `three-LICENSE.txt`.
- GaussianSplats3D 0.4.7: `build/gaussian-splats-3d.module.js`. MIT; see `gaussian-splats-3d-LICENSE.txt`.
- DM Sans (400, 500, 600, 700) and Source Serif 4 (500, 600, 700), retrieved from Google Fonts on 2026-09-05. See `fonts.css` for file mapping and respective OFL notices.

Renderer files were downloaded unmodified from their version-pinned npm distributions through jsDelivr. Fonts use local URLs in `fonts.css`; the UI makes no runtime Google Fonts requests. Retain licence notices when updating or redistributing.

SpaceMouse WebHID: `spacemouse-webhid` 1.0.0 and `@spacemouse-lib/core` 1.0.0,
bundled as browser ESM with esbuild 0.25.9. Supporting packages and licences
are recorded in `SPACEMOUSE-LICENSES.txt`. Source: https://github.com/nytamin/spacemouse.
This is an independent WebHID integration, not 3Dconnexion certification or
the proprietary 3DxWare SDK. No runtime CDN or driver-server connection is used.
# Surface inspection additions

`loaders/GLTFLoader.js`, `utils/BufferGeometryUtils.js`, and
`environments/RoomEnvironment.js` are unmodified Three.js r160 modules from
https://github.com/mrdoob/three.js/tree/r160/examples/jsm, matching the bundled
renderer. They share `three-LICENSE.txt` (MIT) and load locally without a CDN.
