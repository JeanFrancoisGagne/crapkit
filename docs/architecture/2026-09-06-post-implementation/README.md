# Fresh whole-project architecture review

Reviewed commit: `499d9db4f9ff4d212fb94ea3975c7477b6b1c968`.

Open `crapkit-architecture-rerun-2026-09-06.html` for the ranked visual review. It contains 18 fresh candidates and an 18-area coverage map. No product changes were made in this review.

`build-review.py` renders the four candidate inputs and `review-data.json`; run it from this directory to regenerate HTML and combined JSON. `prepare-review.py` reconstructs inventory/coverage metadata and appends decision checkpoints, so use it only when intentionally refreshing those inputs.

Raw review notes, disposable reproduction scripts and measured output live in `crapkit-architecture-rerun-2026-09-06-evidence/`. Script commands in candidate records name the original Windows workspace. Bind `PYTHONPATH` to the reviewed checkout's `src`, use the matching Python interpreter, and run the named script from the evidence folder. Several probes assert the original checkout path; adjust that assertion only when replaying the same reviewed source elsewhere. Do not point them at an unverified editable installation.

The scripts use disposable repositories, local subprocesses or in-memory execution adapters. Release-plan probes execute no remote publication. Synthetic measurements and controlled interleavings prove the recorded case, not its prevalence.

The HTML's Tailwind and Mermaid scripts load from their public CDNs. CSS module diagrams, candidate text, evidence and tables remain readable without those scripts. The prior implementation review remains in `../2026-09-06/`.

`manifest.json` binds the files included in the local archive. It intentionally excludes itself and the ZIP. Source locations in candidates refer to the reviewed commit; the report's own commit contains documentation and evidence only.
