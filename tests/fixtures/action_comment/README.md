# The Action's comment, rendered from recorded payloads

The three payloads and the changed-file list a pull request handed
`tools/action/comment.py`: the 2026-09-03 review's policy repo, on a branch that
adds an untested `route()` (ccn 8) beside a ratchet-marked `legacy_router()`, with a
`diff_uncovered_max` of 3. `tests/unit/test_action_contract.py` renders them and
pins README's "What the comment looks like" fence to that render byte for byte.

crapkit 0.4.15 recorded `verify.json`, as its `tool_versions` says. The five keys
`verify --json` gained since (`changed_paths`, `forgiven_failures`, `retried_passes`,
`unmarked_over_target` and `untracked_in_scope`) were added by hand with this pull
request's values, and the suite fails when verify prints a key this payload
lacks, so the fence shows what the current release renders.

Regenerate the fence with:

    python tools/action/comment.py --coverage tests/fixtures/action_comment/coverage.json \
      --coverage-exit 0 --verify tests/fixtures/action_comment/verify.json --verify-exit 6 \
      --worklist tests/fixtures/action_comment/worklist.json \
      --changed tests/fixtures/action_comment/changed.txt --top 5 --out /tmp/comment.md
