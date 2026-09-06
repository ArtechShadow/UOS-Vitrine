# Docker recovery for the fresh-capture rehearsal

Docker Desktop 4.54.0 was installed but its backend aborted before starting
the Linux engine. Logs identified an inaccessible stale Windows AF_UNIX
socket at `%LOCALAPPDATA%/Docker/run/dockerInference`. After that path was
preserved, startup exposed the same problem at
`%LOCALAPPDATA%/docker-secrets-engine/engine.sock`.

This matches reports on Docker's own issue tracker:
[stale socket crash sequence](https://github.com/docker/desktop-feedback/issues/460)
and [listener initialization despite disabled Model Runner](https://github.com/docker/desktop-feedback/issues/448).
Changing an optional inference setting was therefore not used as a presumed fix.

The crashed Docker Desktop/backend processes were stopped. Only the inspected
runtime socket folders were renamed to backups, and Docker Desktop was
restarted. A failed intermediate startup re-created the inference socket, so
that newly stranded socket folder was also preserved before the final restart.

Preserved folders:

- `C:/Users/realg/AppData/Local/Docker/run.pre-demo-20260906`
- `C:/Users/realg/AppData/Local/Docker/run.pre-demo-20260906-second`
- `C:/Users/realg/AppData/Local/docker-secrets-engine.pre-demo-20260906`

The inspected folders contained only zero-byte runtime socket reparse points.
No settings, images, volumes, WSL distributions or reconstruction files were
deleted or reset. No Windows reboot was required.

Verified after recovery:

```text
docker version --format '{{.Server.Version}}'
29.1.2

docker run --rm --gpus all colmap/colmap:latest nvidia-smi -L
GPU 0: NVIDIA GeForce RTX 5090
```

Both commands exited successfully. The probe used the exact COLMAP image,
which reports CUDA 12.9.1. The quality agent subsequently resumed SfM; a live
`colmap/colmap:latest` container was observed running. A successful GPU probe
establishes runtime availability, not reconstruction completion or quality.
