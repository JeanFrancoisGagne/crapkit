# Walkthrough: a Bun repo with no vitest or jest, before and after the commented bun lane

This walkthrough takes a TypeScript repo that tests with `bun:test` from `crapkit init` to its
first measured run, once with crapkit before init learned the commented lanes and once after.

The repo holds `src/shapes.ts` (a copy of `tests/accuracy/coverage_oracles/probes/ts/shapes.ts`),
`src/shapes.test.ts`, one `bun:test` test that calls `ifElse(true)`, `switchCase("a")` and
`loop([1, 2])`, a `package.json` with no test runner in it, and the `bun.lock` that
`bun add -d @types/bun` wrote. The output below ran with bun 1.4.4 on Windows, in a fresh clone.
The `-> next:` lines show the command as an installed `crapkit` prints it. Run ids and shas
differ on another machine.

## Before: the istanbul template, and coverage refuses

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): src
next: declare a [[lane]] per coverage command (see the commented template), then run `crapkit coverage`
added to .gitignore: .crapkit/
```

TypeScript is a language init writes live lanes for, so the scope carries no
`coverage_optional`. No vitest or jest is in `package.json`, so init writes no lane, only the
commented coveragepy and istanbul templates, each with `scopes = ["<your-scope>"]`.

```
$ crapkit doctor
...
ok   scope 'src': 1 file
ok   every tracked source file belongs to a scope
note no [[lane]] declared - inventory works; coverage needs one
ok   lizard 1.24.0
doctor: no problems found
$ crapkit coverage
crapkit: no [[lane]] to run for scope(s) src - declare a [[lane]] measuring them in crapkit.toml, or set coverage_optional = true on a scope no coverage parser can read
[exit 3]
```

The templates name vitest, and this repo runs bun, so nothing in the config fits it as written.

## After: the commented bun lane gives a measured first run

init prints the same lines and writes the same templates, and `bun.lock` at the root adds one
more commented lane, already pointed at the scope:

```toml
# Lanes for the toolchains this repo uses: uncomment one and delete coverage_optional from its scopes to measure them (docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one).
# [[lane]]
# name = "bun"
# command = "bun test --coverage --coverage-reporter=lcov --coverage-dir=.crapkit/cov/bun"
# artifact = ".crapkit/cov/bun/lcov.info"
# parser = "lcov"
# scopes = ["src"]
```

doctor prints what it printed before: the scope was never cc-only, so no reader note appears,
and `crapkit coverage` still refuses the lane-less scope with exit 3. Delete the `# ` in front
of the six bun lines; the scope has no `coverage_optional` to delete:

```
$ crapkit coverage
run 1 @ 0b16110a449: 16 functions scored: 15 measured / 1 untested, 4 over ceiling 6, CRAP load 87.12, grade F
-> next: crapkit worklist
$ crapkit worklist
worklist @ 0b16110a449 (run 1, floor ccn>=5, churn 12mo) - 4 of 4 active (worklist_top 50), 0 dormant
  risk      3.0  ccn   3  crap    12.0  cov   0%    1c/1a  src/shapes.ts:14  logical ( left , right )
  risk      3.0  ccn   3  crap    12.0  cov   0%    1c/1a  src/shapes.ts:18  nullish ( value null undefined )
  risk      3.0  ccn   3  crap    12.0  cov   0%    1c/1a  src/shapes.ts:63  ignoredIstanbul ( flag )
  risk      3.0  ccn   3  crap    12.0  cov   0%    1c/1a  src/shapes.ts:71  ignoredV8 ( flag )
no crapkit-ratchet.tsv yet: seed marks each function over its ceiling at today's score, and from then on a mark may only fall
-> next: crapkit ratchet seed
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 4, tightened 0 - 4 mark(s) vs run 1 (0b16110a449)
-> next: commit crapkit-ratchet.tsv, then run `crapkit verify`
$ crapkit verify
verify OK @ 5f9a1d3a968 vs baseline 0b16110a449 (3 changed files)
  changed files: .gitignore, crapkit-ratchet.tsv, crapkit.toml
```

The first run is measured, so the first seed holds measured marks and no flip ever appears.
The test calls three of the probe's functions; the four on the worklist never run, and their
crap rises from ccn 3 to 12 at 0% cov.
