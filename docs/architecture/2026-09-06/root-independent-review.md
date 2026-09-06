# Independent review of integrated root changes

This historical independent review ran against `crapkit` during integration. Both doctor findings below are repaired and integrated. [fresh-findings.json](fresh-findings.json) records final dispositions across the project; [validation.json](validation.json) records current validation. Source lines and focused counts in this note identify the earlier review snapshot.

| Priority | Finding | Evidence and disposition |
| --- | --- | --- |
| P2 | Doctor parsed startup diagnostics as part of the interpreter path. | The new bounded capture merged stderr with stdout, then split the whole capture into an executable and two versions. A temporary `sitecustomize.py` printed `BENIGN_STARTUP_WARNING` to stderr. Public `doctor --repo <fixture>` then warned that its own interpreter was foreign and included the warning in the reported executable. Root added a framed version record and its regression test. A repeated public probe confirms the warning no longer contaminates the executable. |
| P2 | Doctor's new byte capture assumed UTF-8 even when the child wrote a Windows encoding. | The capture decoded as UTF-8 with replacement. A real temporary venv under `José`, with inherited `PYTHONIOENCODING=cp1252`, returned `Jos\ufffd` in the executable field. Root now explicitly sets the child's `PYTHONIOENCODING=utf-8` (`src/crapkit/cli/admin.py:864`). Repeated real-venv probe returns the exact path: `UNICODE_EQUAL True`. Resolved. |

## Retained Windows diagnostic

This diagnostic uses a Windows venv launcher and compares against Git commit `20f00e1371334f84aa70bba6f7b23bfc4bdae0f6`. Run it from the repository root in PowerShell with that commit available and the development dependencies installed:

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -B docs/architecture/2026-09-06/root-review-probe.py
```

The probe verifies `crapkit.__file__`, compares old and current MCP `TOOLS` assignments, creates a disposable Git fixture for public doctor, and creates a disposable venv for the encoding check. It does not change the source checkout. It is a retained Windows diagnostic, not the portable regression suite.

Both doctor findings are resolved. The final probe output is saved beside this report as `root-review-probe-final.txt`.

The MCP registry is structurally equal before and after extraction: `MCP_TOOLS_EQUAL True`. Shared schema constants have no mutating production consumer. The hook still reads current index blobs; `--base` changes only the diff basis to the resolved merge base. Unknown bases refuse through the existing Git error path. The Action now allocates an invocation-specific state directory and removes only its own detached base worktree. No additional substantive finding was established in these paths.

The admin-size follow-up closed without another module. Pytest configuration precedence has one owner in `config.py`, consumed by `scaffold.py`. Process deadlines have one owner in `procs.py`. Init and doctor share the first-run checks through `_lane_first_run_note`, `_warn_missing_pytest_cov`, and `_first_run_failure`. Moving that cluster would retain the same rules and callers while adding imports. `doctor.py` remains the home for pure checks; the version protocol was repaired at its existing process boundary.

## Current regression replay

An earlier focused run passed 142 checks in 3.51 seconds. That historical count is not the final suite total. Run the maintained tests from the repository root:

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -B -m pytest tests/unit/test_pytest_config_shared.py tests/unit/test_config_testpaths_guard.py tests/unit/test_action_reentry.py tests/unit/test_action_contract.py tests/unit/test_mcp_registry.py tests/unit/test_doctor_lane_probe.py
python -B -m pytest tests/e2e/test_hook_base.py
```

Inspected: `config.py`, `scaffold.py`, the init/doctor/probe groups in `cli/admin.py`, the staged read lifecycle in `gitio.py`, `hook-precommit` parsing and dispatch, `.github/workflows/ci.yml`, `action.yml`, MCP schema construction and listing, and their focused tests. This review did not execute a hosted GitHub Actions event or a POSIX process-tree test. Their current execution status belongs to [validation.json](validation.json).
