# Local SAM2 object-sidecar setup

The segmentation/model environment remains a separately licensed local process.
Its code, weights, configuration and private rehearsal evidence are not supplied
by this Git checkout. No installed sidecar or model cache was available to the
recovery task, so this document makes no installation or performance claim.

Follow [the Windows runbook](a6000-rehearsal.md) for the normal dashboard and
CLI setup. The external adapter must satisfy [the exact object-support
contract](object-support-contract.md), including full-SH source lineage and
exact frame, camera, instance and real mask identities. Older outputs without
that evidence remain inspectable but cannot pass supported reconstruction.

The core invokes `VITRINE_OBJECT_SIDECAR` with the JSON string array from
`VITRINE_OBJECT_SIDECAR_ARGS_JSON`, appending `--package RUN --out STAGING`.
Use an executable plus arguments, never a shell command. Configure these from
your private local installation record. Keep models and their licences outside
the core MIT environment; do not vendor model code or weights into this tree.
The tracked launcher is only a process adapter; the importer handles files and
core export/validation without importing external model code.

Prepare model files and dependencies online, verify the exact installed runner
against the contract, then test disconnected operation. Environment flags are
not proof that external code cannot download. Missing assets must fail locally.
No portable external model installer is claimed to have been delivered here.

Run isolation through the normal dashboard or configured CLI:

```powershell
& $Python -m vitrine --run-dir $RunDir objects --timeout 1800
& $Python -m vitrine --run-dir $RunDir object-meshes --object-id $ObjectId
```

Set these variables to your selected interpreter, fresh run and real object ID.
The core validates staging before publication and retains prior and failed
outputs. A timeout currently stops the direct child only; inspect attempt-owned
descendants before retrying. Do not stop unrelated processes.

To test an already running dashboard with real results:

```powershell
$env:VITRINE_DEMO_URL = 'http://127.0.0.1:8765'
& $Python -m pytest tests/test_demo_sidecar_live.py -v
```

These tests do not start a GPU job and cannot replace visual inspection. Verify
identity, mask alignment, captured-view shape and the preservation archive.
Personal paths, machine-specific environment inventories and private rehearsal
measurements are deliberately excluded from this public setup guide.
