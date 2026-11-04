# A Go repo from `crapkit init` to a measured scope

`tests/e2e/test_go_flip_reseed_e2e.py` runs these steps on a repo that holds a
Python scope `calc`, measured by a live lane, and a Go scope `cmd` under a root
`go.mod`.

## Before: 0.8.x

0.8.x reads no Go coverage. `crapkit init` writes the Go scope with
`coverage_optional = true`, and the scope stays cc-only for good: every Go
function scores `crap = ccn`, a function at ccn 3 with no test at all passes a
ceiling of 4, and no lane can change that.

## After: 0.9.0

1. `crapkit init` finds the root `go.mod` and leaves the go stanza commented
   under the cc-only scope:

   ```
   # [[lane]]
   # name = "go"
   # command = "go test -coverprofile=.crapkit/cov/go.out ./..."
   # artifact = ".crapkit/cov/go.out"
   # parser = "coverprofile"
   # scopes = ["cmd"]
   ```

   `crapkit doctor` notes that scope `cmd` is cc-only and that a reader can
   measure go through `parser = "coverprofile"` from go test.

2. `crapkit coverage`, `crapkit ratchet seed` and a commit mark the Go
   functions over the ceiling at their ccn: `Classify`, `SumPositive` and
   `Mul` at 3.0000.

3. Uncomment the stanza and delete `coverage_optional = true`. The next
   `crapkit verify` exits 3 with one conflict for the scope, not one exit 7 per
   mark:

   ```
   1 scope(s): cmd (coverage cc-only to coverprofile=1) ...
   cmd was cc-only and is now measured by its coverprofile lane(s), so its
   marks hold no coverage yet: follow docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one
   ```

4. `crapkit coverage` measures the scope, and `crapkit ratchet seed --scope cmd`
   replaces cmd's marks with the run's values and counts the rises:
   `Classify` (6 of 8 lines run) rises to 3.1406, `Mul` (no line run) to
   12.0000, and `SumPositive` (every line run) stays at 3.0000. The seed prints
   `scope cmd: added N, tightened 0, raised 2`.

5. `crapkit verify` exits 0. `calc`'s stamp line and marks keep their bytes.

A later bump of the coverprofile reader's number raises no mark: only the
cc-only to measured flip does.
