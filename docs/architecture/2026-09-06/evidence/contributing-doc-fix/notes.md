# CONTRIBUTING correction

`CONTRIBUTING.patch` changes two passages in `CONTRIBUTING.md`: replace the obsolete doctor failure exemption with the current cache isolation rule, and place the event-base gate on its actual Ubuntu/Python 3.12 matrix leg. Other documentation, source, tests, benchmark counts, and timing claims remain outside this patch.

| Checked source | Evidence |
| --- | --- |
| `tests/unit/test_init_probe.py:27-42` | Autouse `_forget_probed_words` clears `admin._start_probe` before and after each test. |
| `tests/unit/test_doctor_results_artifact.py:21-38` | Autouse `probe_that_started` clears the real cache on both sides and replaces the probe during each test. |
| `.github/workflows/ci.yml:10-12` | Six test legs: two operating systems and three Python versions. |
| `.github/workflows/ci.yml:46-51` | Gate condition selects Ubuntu/Python 3.12; `BASE_REF` is the PR base SHA or push event's previous SHA; command passes `--base "$BASE_REF"`. |

## Existing contract tests

| Test | Relation to this edit |
| --- | --- |
| `tests/unit/test_docs_claims_contract.py::test_contributing_setup_carries_every_step_agents_calls_mandatory` | Reads CONTRIBUTING directly. The required setup commands remain unchanged. |
| `tests/unit/test_ci_install_contract.py::test_ci_installs_the_dev_extra_and_quotes_it` | Checks the workflow install command retained in the corrected table row. |
| `tests/unit/test_ci_install_contract.py::test_ci_does_not_swallow_the_crapkit_gate_exit_code` | Checks the blocking gate behavior described in the corrected row. |

No existing docs-contract assertion pins the obsolete doctor paragraph or claims that the gate runs on all six matrix legs. No test source update is required for this wording. The two autouse fixtures above supply the code evidence for the isolation paragraph.

The draft and its before copy are stored here. `source-sha256.txt` records the root CONTRIBUTING hash; the hash was unchanged after drafting. `git apply --check` accepts the patch against the root checkout. No patch was applied, no tests ran, and no benchmark count was refreshed. Apply only after the parent's frozen-input verification is complete.
