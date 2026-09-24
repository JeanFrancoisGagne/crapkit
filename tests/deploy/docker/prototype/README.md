# Deploy image prototype

The throwaway build that sized the deploy images before the real Dockerfile
existed (Docker 29.8.0, BuildKit 0.33). Kept because the budgets in the plan
come from these runs; `../Dockerfile` replaced it.

| Stage | Measured | Log |
|---|---|---|
| base (debian trixie-slim, uv, three CPythons, node) | 947 MB, 47.6 s cold | `logs/base.log`, `logs/base.time` |
| wheelhouse (crapkit 0.4.0 to 0.8.0, three CPythons) | 14 MB, 39.5 s | `logs/wh.log`, `logs/wh.time` |
| harness (10 npm CLIs) | 2.1 GB of node_modules, 2 min 36 s | `logs/h.log`, `logs/h.time` |
| cells (base + wheelhouse), warm | 3.6 s | `logs/c.log`, `logs/c.time` |

`cell.sh VERSION [UPGRADE_TO]` is the offline start-and-upgrade cell that took
about 20 s under `--network none`. `probe*.sh` are the harness probes; they ran
with the network on, fetched the Cursor agent tarball at run time, and read
the crapkit checkout from a `/src` mount. `OFFLINE.md` records the same probes
rerun with `--network none` in the pinned images.
