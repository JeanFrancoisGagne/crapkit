# Go coverprofile fixtures

Recorded Go coverprofiles the `coverprofile` reader reads in the unit and e2e
tests. Each was written by Go 1.26.8 (`golang:1.26-bookworm`, linux/amd64)
with the command shown, from the root of a fresh copy of `go-repo/` mounted at
`/home/user/repo`, with `GOTOOLCHAIN=local` and no network, and committed as
it came out. The same commands in the pinned image `golang:1.26.8-bookworm`
(`golang@sha256:dc9ad6c05acc7a88e5b71bde60a5fe3bd4b9f0db209011711b464107438a8107`,
go1.26.8 linux/amd64) write the same blocks: `go test` merges the packages'
profiles in the order the packages finish, so only the order of the two
packages' lines can differ.

- `go-1.26.8-set.out`: the go stanza `crapkit init` writes for a repo with a
  root `go.mod`, run as written. `go test` writes the default mode, `set`.
- Stanza `go`: `go test -coverprofile=.crapkit/cov/go.out ./...`
- `go-1.26.8-count.out`: `go test -covermode=count -coverprofile=count.out ./...`
- `go-1.26.8-atomic.out`: `go test -covermode=atomic -coverprofile=atomic.out ./...`
- `go-1.26.8-coverpkg.out`: `go test -coverpkg=./... -coverprofile=coverpkg.out ./...`.
  Each test binary lists every package's blocks, so each block appears twice:
  the shapes binary runs `calc.Mul` through `Area`, and the calc binary lists
  `shapes.go` with every count 0.
- `go-1.26.8-func.txt`: `go tool cover -func=go-1.26.8-set.out`, the
  per-function statement percentages Go reports for the set profile.

The set, count and atomic profiles come from three runs of one suite, so they
hold the same blocks; only the counts differ (`set` writes 0 or 1).

`go-repo/` is the Go fixture repo, module `example.com/shapes`. Its tests run
`Classify` (the zero branch never), `SumPositive`, `Scaled` (its func literal's
zero branch never) and `Area`; `Unused` never runs. `internal/calc` holds `Mul`,
which only `Area` calls, and `Div`, which its own test calls.

`WALKTHROUGH.md` takes a Go repo from `crapkit init` through the commented
lane, the flip conflict and the scoped reseed.

crapkit counts a block's lines: every line from a block's first to its last is
hit when any block over it ran. `go tool cover -func` counts statements, so
its percentages differ from crapkit's line-based cov: `Classify` is 80% of its
statements in `go-1.26.8-func.txt` and 6 of 8 lines to crapkit.

## The accuracy probe (Addendum B)

`tests/accuracy/coverage_oracles/probes/go` holds the Go probe, module
`example.com/probe`, and its hand-worked `ground_truth.tsv`. Its recordings
under `tests/accuracy/coverage_oracles/recorded/` came from one container of
`golang:1.26.8-bookworm` (digest above), from the probe's directory, with
`PROBE_SCENARIO` set to `call` or `idle`:

- `go-1.26.8-set`, `-count`, `-atomic`:
  `go test -covermode=<mode> -coverprofile=<scenario>.out ./...`
- `gocover-cobertura-1.5.0`: `gocover-cobertura < set-<scenario>.out`, built by
  `go install github.com/boumenot/gocover-cobertura@v1.5.0` (1.3.0 does not
  build on Go 1.26). Its `<source>` was the container path of the probe; the
  committed file says `go`, and `timestamp` is 0.

Reported, not gated: `go tool cover -func` over the call run beside crapkit's
line-based cov. crapkit counts every line of a block, so a block's opening
line, the `if` line two blocks share and a body's closing brace count; Go
counts each statement once.

| function | go tool cover -func | crapkit cov | why they part |
|---|---|---|---|
| IfElse | 66.7% (2 of 3) | 4 of 5 lines | the signature line and the body's closing brace sit in run blocks |
| Logical | 66.7% (2 of 3) | 3 of 5 lines | the missed body block holds two lines, its statement and its brace |
| Pick | 66.7% (2 of 3) | 4 of 6 lines | they agree: each block holds two lines |
| Loop | 75.0% (3 of 4) | 4 of 6 lines | the missed body block holds the for line, its statement and its brace |
| Branchless | 100% | 4 of 4 lines | they agree |
| OneLine | 100% | 1 of 1 line | they agree |
| Untouched | 0% | 0 of 5 lines | they agree |
| WithClosure | 80.0% (4 of 5) | 2 of 2 lines, and the func literal 4 of 5 | Go counts the literal's statements in WithClosure; crapkit gives them the literal's own span |
| Method | 66.7% (2 of 3) | 4 of 5 lines | as IfElse |
