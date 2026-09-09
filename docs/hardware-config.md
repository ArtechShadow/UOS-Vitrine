# Hardware detection and runtime configuration

`vitrine.hardware` reports host facts and derives conservative process settings
without making CUDA a package requirement.  The detector first reads
`nvidia-smi` when it is available, then uses PyTorch as an optional source of
CUDA device details.  CPU and RAM facts are collected from the platform APIs or
the usual Linux system files.  A machine with no CUDA installation still gets
a valid snapshot.

The two public calls have stable, JSON-safe return shapes:

```python
from vitrine.hardware import detect_hardware, resolve_runtime

hardware = detect_hardware()              # flat host facts
runtime = resolve_runtime(hardware=hardware)  # workers/cache settings
```

`detect_hardware()` returns a record with `schema`, `platform`, `os_version`,
`architecture`, `gpu_available`, `cuda_available`,
`torch_cuda_available`, `gpu_vendor`, `gpu_model`, `driver_version`,
`cuda_version`, `compute_capability`, `vram_total_gb`, `vram_free_gb`,
`cpu_model`, `cpu_logical_cores`, `ram_total_gb`, `ram_available_gb`, and
`capability_tier`.  `source_gpu`, `source_ram`, `config_path`, and `overrides`
identify where optional facts came from.

`resolve_runtime()` keeps that snapshot under `hardware` and adds:

```python
runtime["capability_tier"]             # cpu | constrained | standard | high
runtime["legacy_tier"]                 # laptop | workstation compatibility bridge
runtime["workers"]["sfm"]              # native SfM worker ceiling
runtime["workers"]["io"]
runtime["workers"]["torch"]
runtime["workers"]["preview"]
runtime["workers"]["max_concurrent_jobs"]
runtime["cache"]["view_cache_budget_gb"]
runtime["cache"]["safety_factor"]
runtime["gpu"]
```

The generic capability tiers describe resource capacity.  They carry no
throughput multiplier or training ETA.  `legacy_tier` exists only for callers
that still select the historical laptop/workstation profile names.  In
particular, a high-memory RTX A6000 is not assigned an RTX 5090 ETA.

## Configuration and overrides

The optional project default is [`config/hardware.json`](../config/hardware.json):

```json
{
  "schema": "vitrine/hardware-config/1",
  "hardware_overrides": {},
  "runtime": {
    "ram_reserve_gb": 4.0,
    "cache_fraction": 0.5,
    "cache_safety_factor": 1.25,
    "max_sfm_workers": 8,
    "max_io_workers": 4,
    "torch_threads": 8,
    "preview_workers": 1,
    "max_concurrent_jobs": 1
  }
}
```

Set `VITRINE_HARDWARE_CONFIG` to use another JSON file.  Hardware overrides
can also be supplied for a single process with
`VITRINE_HARDWARE_OVERRIDE`, whose value must be a JSON object, for example:

```text
VITRINE_HARDWARE_OVERRIDE={"gpu_vendor":"NVIDIA","gpu_model":"NVIDIA RTX A6000","vram_total_gb":48,"vram_free_gb":44,"compute_capability":"8.6"}
```

The precedence is explicit function override, environment JSON override,
config-file override, then live probe.  Unknown fields, invalid numbers,
impossible free-versus-total values, unsupported capability tiers, and unknown
runtime settings raise `HardwareConfigError`; typos therefore cannot silently
alter worker or cache selection.  A missing project default falls back to
built-in runtime defaults so an installed package remains usable when the
repository-level config file is not present.

## Profile and ETA boundaries

`quality="demo"` is the exact measured recipe recorded in
`docs/demo-video-quality-20260906.md`:

| setting | value |
| --- | ---: |
| source long edge | 2304 px |
| random crop | 1536 px |
| requested MCMC cap | 2,000,000 splats |
| iterations | 15,000 |
| SH degree | 3 |
| COLMAP long edge | 3200 px |

The recorded training time is approximately 4.3 minutes on the RTX 5090 for
that real video-only run.  The sparse-point guard may lower the effective cap
for a particular reconstruction.  The profile returns that measured time only
when the supplied hardware model matches the RTX 5090 family; for an unknown
GPU or an RTX A6000 it returns no ETA.  Legacy numeric profiles remain
unchanged.  `Profile.validation_warnings()` and `profiles.describe()` expose a
warning for the legacy workstation archive row because its 1600/4096
crop/source ratio is below the measured 50% safe-coverage rule and remains
unvalidated.

## RAM preflight

`ViewSet` performs a cache preflight before decoding images by default.  The
public `ram_guard_report(model, images_dir, long_edge=...)` helper opens image
headers, estimates the RGB `uint8` cache size, applies the runtime safety
factor, and returns `status` (`ok`, `exceeded`, or `unknown`) together with
the estimated and budget values.  An exceeded budget raises `MemoryError` with
the requested resolution and a lower-resolution remediation.  If the platform
cannot report available RAM, the status is `unknown` and the loader logs that
boundary rather than claiming the cache is safe.

Pass the already-resolved runtime to avoid a second probe when a caller owns
the preflight:

```python
views = ViewSet(
    model,
    images_dir,
    long_edge=profile.source_long_edge,
    runtime=runtime,
)
```

`ram_guard=False` is available for controlled diagnostics and small unit-test
fixtures.  It should not be used to bypass a failed preflight for a real
capture.
