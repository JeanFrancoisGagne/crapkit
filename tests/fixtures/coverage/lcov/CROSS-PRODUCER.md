# Cross-producer reading: bun and deno through lcov, c8 through istanbul

This is the Addendum B report on the commented bun and deno lanes. It sets crapkit's
per-function cov over the bun and deno recordings in this folder beside its cov over c8's
istanbul recording of the same probe shapes, and names each difference. It gates nothing.

## What was read

| Reading | Recording | Parser | Probe | What the producer writes |
|---|---|---|---|---|
| c8 | `tests/accuracy/coverage_oracles/recorded/c8-12.0.0/call.json` | istanbul | `probes/mjs/shapes.mjs` | v8 block ranges, each as a one-location branch |
| bun js | `bun-1.4.4.lcov` | lcov | `probes/js/shapes.js` | `DA` lines, no `BRDA` |
| bun ts | `bun-1.4.4.lcov` | lcov | `probes/ts/shapes.ts` | `DA` lines, no `BRDA` |
| deno js | `deno-2.9.8.lcov` | lcov | `probes/js/shapes.js` | `DA` lines and a taken/not-taken `BRDA` pair per v8 block |

c8's recording covers the ES module probe. The mjs, js and ts probes declare the same 16
functions in the same order on the same lines, and `probes/harness/drive.mjs` makes the same
calls on each in the `call` scenario, so the readings line up by function name. deno 2.9.8
wrote no record for the ts probe (README.md says why), so no deno ts column exists.

Each reading is one `crapkit coverage --export` run over a repo holding the three probes, with
one lane that copies the recording to its artifact. A function with arms in its span reads its
arms; a function with none reads its lines.

## Per-function cov

| Function | c8 | bun js | bun ts | deno js |
|---|---|---|---|---|
| ifElse | 0.5 | 0.75 | 0.75 | 0.5 |
| ternary | 0.5 | 1.0 | 1.0 | 0.5 |
| logical | 0.5 | 1.0 | 1.0 | 0.5 |
| nullish | 0.5 | 1.0 | 1.0 | 0.5 |
| defaultParam | 1.0 | 1.0 | 1.0 | 1.0 |
| switchCase | 0.333 | 0.5 | 0.429 | 0.5 |
| loop | 0.5 | 0.8 | 0.833 | 0.5 |
| withCallback | 0.667 | 1.0 | 0.0, untested | 1.0 |
| withCallback's callback | 0.667 | 0.8 | 0.8 | 0.5 |
| optionalChain | 1.0 | 1.0 | 1.0 | 1.0 |
| oneLine | 1.0 | 1.0 | 1.0 | 1.0 |
| arrow | 1.0 | 1.0 | 1.0 | 1.0 |
| ignoredIstanbul | 0.5 | 0.75 | 0.75 | 0.5 |
| ignoredV8 | 0.5 | 0.75 | 0.75 | 0.5 |
| asyncIf | 0.5 | 0.75 | 0.75 | 0.5 |
| Box.method | 0.5 | 0.75 | 0.75 | 0.5 |

## Findings

1. **deno and c8 share a criterion, and agree on 13 of 16 functions.** Both read v8's block
   counts. The three differences come from how each producer writes those blocks as arms.
2. **switchCase: deno 0.5, c8 0.333.** c8 writes the function's own block (lines 26-35, taken)
   and the two untaken case blocks (30-31, 32-33) as three one-location branches: 1 of 3. deno
   writes a taken/not-taken pair on line 30 and on line 32: 2 of 4.
3. **withCallback and its callback: deno 1.0 and 0.5, c8 0.667 and 0.667.** deno writes the
   callback's only pair on line 49, inside the callback, so the callback reads 1 of 2 arms and
   `withCallback` holds no arm and reads its own lines 45 and 52, both hit. c8 writes three
   blocks over the pair (45-52 taken, 46-51 taken, 50-51 not taken), and crapkit's istanbul
   reading gives both functions 0.667. Raw v8-to-istanbul output is not the istanbul model
   (`test_producers_agree.py`, rulings D7 and CO4), so neither side is the ground truth here.
4. **bun reads lines, not arms.** bun 1.4.4 writes no `BRDA`, so every bun reading is line
   coverage, a different criterion from c8's and deno's. The four functions with no untaken
   block agree at 1.0; the other twelve read higher in bun, because a line that ran counts in
   full even where one of its arms never ran (`ternary`, `logical` and `nullish` at 1.0 against
   0.5).
5. **bun ts against bun js: switchCase, loop and withCallback differ, from the spans.** bun
   writes the same `DA` lines for both probes. lizard ends each TypeScript function with a
   return type on the next function's first line (accuracy ruling definitions-1), and crapkit's
   join gives a line two spans claim to the shorter one. switchCase (26-37) loses its hit line
   26 to defaultParam (22-26): 3 of 7. loop (37-45) gains the hit line 45: 5 of 6.
   withCallback (45-54) loses 45 to loop, 46-51 to the callback and 54 to optionalChain, keeps
   lines 52 and 53, which hold no `DA` record, and reads as untested at 0.0. The same spans
   meet any lcov reading of a TypeScript file whose functions declare a return type.

No score in the corpus goldens moves: these readings run only here, through a lane a user
declares.
