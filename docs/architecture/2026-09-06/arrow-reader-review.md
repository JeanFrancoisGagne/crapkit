# Expression-arrow reader repair

Status: integrated. The original reader patch added one production module and one regression module; analyzer wiring and analysis version 10 were integrated afterward. The final cache repair shares typed-mode selection between this reader and cache identity. Installed code and global reader classes remain unchanged.

## Defect and result

Stock lizard 1.24.0 reads `const f = [(x) => x + 1, (x) => x + 2];` as one function. The TypeScript state keeps its started function across the comma. Square brackets lack a separate expression state, and ternary colons can enter the type-annotation reader. Together these rules merge sibling functions and their conditions. A physical newline does not solve the defect.

The local extension gives array expressions their own state, closes a completed expression arrow at its separator, keeps a nested arrow under its own function, and keeps ternary colons in expressions. It adjusts expression end lines to the final consumed body line. It retains lizard's physical start-line convention and raw signatures. The original tokens pass through once.

| Before | After |
|---|---|
| One started function crosses the array comma | Each sibling closes before the next sibling starts |
| Nested square brackets share outer expression state | Each bracket pair owns a nested expression state |
| A ternary colon enters type parsing | A pending ternary consumes its colon |
| Installed classes determine every reader | One fresh reader instance receives local states before its first token |

## Scope and integration

The source lines below refer to the original two-file patch, before analyzer wiring and the typed-mode cache follow-up.

| File | Evidence |
|---|---|
| `src/crapkit/lizardtypescript.py:23` | Local TypeScript state subclass |
| `src/crapkit/lizardtypescript.py:45` | Nested expression arrows receive a separate child |
| `src/crapkit/lizardtypescript.py:64` | Array bodies retain continued physical lines |
| `src/crapkit/lizardtypescript.py:96` | Precise refusal for ambiguous TypeScript commas |
| `src/crapkit/lizardtypescript.py:132` | Fresh reader-state replacement; tokens pass through unchanged |
| `tests/unit/test_lizardtypescript_expressions.py:39` | Sibling counts and per-function CCN |
| `tests/unit/test_lizardtypescript_expressions.py:77` | Previously accepted map/filter chain metrics retained |
| `tests/unit/test_lizardtypescript_expressions.py:84` | Generic typed arrow declarations remain accepted |
| `tests/unit/test_lizardtypescript_expressions.py:96` | Exact refusal and parenthesized workaround |
| `tests/unit/test_lizardtypescript_expressions.py:126` | Ten language-family record comparisons |
| `tests/unit/test_lizardtypescript_expressions.py:132` | One token traversal and unchanged stock classes |

`src/crapkit/analyze.py` imports the extension under `deferred_pygments`, prepends it to the existing chain and stamps `ANALYSIS_VERSION = 10`. Changed extraction invalidates old cached metric records. Reader-version admission separately protects anonymous ratchet marks and claims from old snapshots; a version bump alone does not prove that old ordinals still name the same functions.

The test helper removes this extension for stock comparisons. Integrated analyzer, cache and coverage checks passed afterward. `uses_type_syntax` now supplies the same `.ts`/`.tsx` mode to the reader and cache key, so a JSX hit cannot bypass a TSX refusal. See `final-analysis-review.md` and `evidence/evidence-execution/reader-migration-handoff.md` for the paired follow-up evidence.

## Exact supported limit

In `.ts` and `.tsx`, an unparenthesized expression-arrow body with an unmatched `<` when a comma arrives does not provide enough structure for this reader to tell a generic type-argument comma from an expression separator. The reader raises `ToolError` naming the file and function start line. This covers both `x => pair<T,U>(x), next` and `x => x < 0, next`.

Wrap that body in parentheses or use a block:

```ts
const f = [x => (pair<T,U>(x) || x), x => x + 1];
const g = [x => (x < 0), x => x + 1];
```

Both forms produce two functions with the expected complexity in the regression tests. Generic arrow parameters such as `<T,U>(x: T, y: U) => x`, typed parameters and return annotations, a final comparison with no ambiguous comma, and JavaScript comparison bodies remain accepted.

Documented behavior: "Expression arrows in arrays and argument lists are measured separately. In TypeScript, wrap an arrow body in parentheses when it contains `<` before a comma, for example `x => (pair<T,U>(x))` or `x => (x < 0)`. Without that delimiter, analysis refuses the file because the reader cannot distinguish type arguments from an expression separator. Generic arrow parameter declarations remain supported."

## Alternatives checked

1. Stock or current upstream reader: keeps the reproduced defect. The pinned upstream revision below carries the same relevant states.
2. Patch the installed class or reader registry: changes unrelated consumers and couples behavior to import order. The per-reader extension avoids both.
3. Add a partial generic/comparison lookahead parser: requires TypeScript type grammar plus following-token rules. Microsoft's parser handles that with `parseTypeArgumentsInExpression` and `canFollowTypeArgumentsInExpression`. A comma-only heuristic silently assigns the wrong function. The explicit refusal covers only the unresolved form and provides a tested delimiter workaround.
4. Rewrite source or add synthetic offsets: changes signatures or physical positions and needs another traversal. The state extension retains original tokens and physical lines.

## Validation

All Python commands used `PYTHONPATH` pointing at this worktree's `src`. The baseline replay asserts the resolved `crapkit.__file__` first. No network writes occurred.

| Check | Result | Evidence |
|---|---|---|
| Original stock-reader fixture | One function instead of two | `evidence/final-review/arrow-prototype.json` |
| Same assertions with local extension omitted | 11 failures, 28 deselected | `evidence/final-review/arrow-stock-red.txt`, `evidence/final-review/arrow-stock-replay.py` |
| Targeted repaired tests | 39 passed | `evidence/final-review/arrow-tests-green.txt` |
| Analyzer, reader, cognitive tests | 244 passed | `evidence/final-review/arrow-focused.txt` |
| Upstream TypeScript assertions, both readers | 88 tests, 2 skips, pass | `evidence/final-review/arrow-upstream-final.txt` |
| Upstream JavaScript assertions, both readers | 54 tests, 3 skips, pass | `evidence/final-review/arrow-upstream-final.txt` |
| Upstream TSX assertions, both readers | 44 tests, no skips, pass | `evidence/final-review/arrow-upstream-final.txt` |
| Raw upstream record comparison | 159 parsed fixtures; counts, names, spans, NLOC, parameters and CCN unchanged | `evidence/final-review/arrow-upstream-final.json` |
| Production complexity | 15 functions, maximum CCN 6 | `evidence/final-review/arrow-complexity.txt` |
| Staged pre-commit hook | Exit 0 | `evidence/final-review/arrow-hook.txt` |
| Staged diff whitespace check | Clean | `git diff --check --cached -- src/crapkit/lizardtypescript.py tests/unit/test_lizardtypescript_expressions.py` |

The one raw upstream record change is `arr.reduce((acc, x) => acc + x, 0)`: two token-count units move from the callback to its enclosing function. The comma ends the callback before the initial-value argument. `FunctionRecord` does not store token count. All reported metrics and spans in that fixture stay unchanged.

The first defect probe preceded the production extension. The stock-reader regression replay above was recorded after implementation and proves that the final assertions fail when the extension is removed; it is not presented as an earlier pytest run.

## Portable replay

From the repository root:

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
python -B docs/architecture/2026-09-06/evidence/final-review/arrow-stock-replay.py
python -B -m pytest tests/unit/test_lizardtypescript_expressions.py -o 'addopts=--tb=short -p no:cacheprovider' -q -p no:randomly
python -B docs/architecture/2026-09-06/evidence/final-review/arrow-upstream-final.py
```

The stock replay deliberately fails the recovered-arrow assertions. Pair `evidence/final-review/arrow-stock-red.txt` with `evidence/final-review/arrow-tests-green.txt`; the former is not a final-suite failure. Upstream source fixtures are copied beside the replay. The upstream replay writes `arrow-upstream-rerun.json`, preserving the historical `evidence/final-review/arrow-upstream-final.json` and its captured log.

The archived `evidence/final-review/arrow-reader.patch` contains only the original two-file reader repair. Analyzer wiring, typed-mode cache admission and reader-version migration are later integrated changes, described above.

## Primary sources

The three copied upstream test files match revision `147b1637f246d188979d876956671ab957df4f92` byte for byte. The unmodified upstream copyright and license text is retained in `evidence/final-review/LICENSE.txt`. `evidence/final-review/upstream-sources.json` records the pinned URLs, file hashes and tested runtime version.

- [Lizard TypeScript reader](https://raw.githubusercontent.com/terryyin/lizard/147b1637f246d188979d876956671ab957df4f92/lizard_languages/typescript.py)
- [Reader history](https://github.com/terryyin/lizard/commits/master/lizard_languages/typescript.py)
- [Upstream TypeScript tests](https://raw.githubusercontent.com/terryyin/lizard/147b1637f246d188979d876956671ab957df4f92/test/test_languages/testTypeScript.py)
- [Upstream JavaScript tests](https://raw.githubusercontent.com/terryyin/lizard/147b1637f246d188979d876956671ab957df4f92/test/test_languages/testJavaScript.py)
- [Upstream TSX tests](https://raw.githubusercontent.com/terryyin/lizard/147b1637f246d188979d876956671ab957df4f92/test/test_languages/testTSX.py)
- [TypeScript 5.9.3 parser](https://raw.githubusercontent.com/microsoft/TypeScript/v5.9.3/src/compiler/parser.ts)
