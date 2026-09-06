# Advisory latency comparison

No source regression was established. Current and release code both exceeded the 40 ms opt-in allowance during the same fixed-count run. Interpreter startup varied enough to obscure a small source difference. The original 1.04 ms miss remains a failed timing check; these measurements do not turn it into a pass.

## Paired results

Fifty interleaved blocks compared the current source with release `20f00e1371334f84aa70bba6f7b23bfc4bdae0f6`. Each block ran both hooks and a separate interpreter floor for each source. Source order and hook/floor order alternated. Three calls warmed each command before measurement. No samples were discarded or repeated to obtain a passing result.

| Measurement | Current | Release |
| --- | ---: | ---: |
| Median public hook | 119.41 ms | 134.14 ms |
| Median interpreter floor | 57.38 ms | 61.65 ms |
| Median paired hook minus floor | 67.09 ms | 66.03 ms |
| Minimum hook minus minimum floor | 51.31 ms | 45.92 ms |
| Five-sample groups exceeding 40 ms | 10 of 10 | 10 of 10 |

The median of the paired current-minus-release overhead differences was **+0.45 ms**. Its paired bootstrap 95% interval was -15.00 to +10.56 ms. Raw hook differences within pairs favored current by a median 5.19 ms. These results do not isolate a repeatable source slowdown and cannot rule out a small one.

Interpreter floors across these source pairs ranged from 37.70 to 275.24 ms. The release hook itself ranged from 84.47 to 250.26 ms. This variability is much larger than the original 1.04 ms threshold miss.

Twelve further pairs compared the live root with its byte-identical private snapshot. Their median adjusted difference was +0.55 ms, with a -12.79 to +25.46 ms interval. This check found no resolved effect from the source directory location. It ran after the source comparison and is not pooled into those results.

## Source and invocation controls

- Both versions used the same Python 3.11.2 executable and recorded `no_toml` golden payload, with an actual disposable Git root and no `crapkit.toml`.
- Current and release snapshots occupy adjacent, equal-length directory names under `work/implementation/advisory-latency`. Release bytes came from `git archive` at the exact reference above. Current bytes came from root source. SHA-256 manifests prove the current snapshot and live root matched after measurement.
- Every child received an explicit source-specific `PYTHONPATH`. Separate child assertions proved `crapkit.__file__` resolved to each expected tree. The editable install was unchanged.
- The measured hook command was `python -m crapkit claude-hook --protocol 1`; its golden JSON arrived on stdin. The floor command was `python -c pass`. Both returned exit 0 with empty output.
- Root and the analysis agent paused test and coverage work for the measurement window, 2026-09-06 21:47:31.650 through 21:47:59.445 UTC. This controls our jobs, not unrelated machine activity.

The import audit loaded the same seven crapkit modules in both versions: package, CLI facade, parser, advisory, errors, invocation, and root discovery. The complete module sets were identical. Neither version loaded lizard, analysis, ratchet, or the store. The only changed function call in the traced path was the new small CLI `main` wrapper replacing the old facade's `__getattr__` dispatch.

## Commands and evidence

The controller ran from `<repo>`:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python <review-work>\evidence-execution\measure-advisory-latency.py
python <review-work>\evidence-execution\probe-advisory-imports.py
```

`advisory-latency.json` contains every raw timing, full child argv, stdin payload, source path, import proof, source manifest, and summary. `advisory-latency.log` contains the printed summary. `advisory-imports.json` records loaded modules and traced calls; `advisory-imports.log` contains their differences. The scripts and private source snapshots remain available beside these records. The timing script creates its snapshot directory once and refuses to overwrite it.

No production file, test, threshold, or editable install was changed. The evidence supports machine variability as an explanation for the threshold miss; it does not establish that the 40 ms allowance is met on this machine.
