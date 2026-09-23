# crapkit

A per-function CRAP scorer: it reads the coverage artifact a repository's own test suite writes, inventories every function's complexity, joins the two into one score per function, ranks the worst by how often their files change, and gates new code against a committed record of accepted debt.

## Language

### The score

**CRAP**:
The per-function score, complexity squared times uncovered risk cubed plus complexity. The number every view exists to show.
_Avoid_: crap score, risk score, grade (a grade is a letter over a scope)

**Ceiling**:
The highest CRAP a function may carry before it is over; one per repository, overridable per scope. Since coverage can at best collapse CRAP to complexity, a ceiling is also a complexity limit.
_Avoid_: target (that is the configuration key that sets a ceiling, not the concept), threshold, limit

**Coverage**:
The share of a function's branches the suite ran, read from the artifact; never measured by crapkit itself.

**Risk**:
What ranks the worklist: complexity times recency-weighted churn. Not the CRAP score.

**Remedy**:
The one-word action attached to a scored row: `decompose`, `split-lines`, `add-tests` or `ok`. brief and next-item judge it against the ceiling crapkit.toml holds when they read the row; worklist prints the verdict the run stored.

### Naming a function

**Twin**:
One of several functions that one file gives the same long name. Each takes its own ratchet key: the first keeps the bare name, and later ones take `#2`, `#3` in file order. A bare twin name selects the worst twin.
_Avoid_: duplicate (a near-copy that shingles find)

**Handle**:
The short name a payload prints for a function: the bare identifier, `NAME#N` for a twin, or `(anonymous)#N` for a function lizard could not name. It survives an edit above the function; a start line does not.

### The corpus

**Scope**:
A named set of path prefixes and languages that shares one ceiling and one set of lanes.

**Lane**:
One configured test command that writes one coverage artifact for one scope.

**Inputs**:
The root-relative paths a lane declares its command reads. While none of them changed since the artifact's commit, `--reuse-unchanged` reuses the lane instead of rerunning it.
_Avoid_: dependencies, sources (a scope's paths are its sources)

**Artifact**:
The coverage file a lane writes and crapkit reads.
_Avoid_: report (a report is crapkit's own HTML page)

**Exclude**:
A glob that removes files from the corpus before inventory.

**Unanalyzable file**:
A source file the analysis names on stderr and scores as zero functions, because lizard failed on it or a Python def in it was read no further than its signature. Every run tries it again.
_Avoid_: skipped file (nothing about it is silent)

### Runs

**Run**:
One scored snapshot: an inventory joined with the artifacts of the lanes that ran.

**Partial run**:
A run in which some declared lanes did not run; never a baseline.

**Legacy run**:
A stored run written before crapkit recorded where same-line functions sit. Its same-line twins cannot be told apart: a function's history leaves the run out, and a command that must read the twins from it, such as a seed, refuses and names the run.

**Baseline**:
The trusted earlier run a verdict compares against.

**Named baseline**:
A run that `--baseline ID` names for verify, ratchet seed or ratchet prune. It steps past the rule that a failed verify taints later runs, and nothing else: a failed verify, a hook run, a partial run or an inventory run is still refused.
_Avoid_: forced baseline, override baseline

**Verdict**:
The outcome of `verify`: the gate result, ratchet regressions and new test failures against the baseline.

**Forgiven failure**:
A test failure the fresh run and the baseline both have. It is not new, so it fails no verdict; the OK line counts it.
_Avoid_: known failure, ignored failure

**Flake retry**:
verify's rerun of its new failures through a lane's `retest_command`, before it decides exit 8.

**Retried pass**:
A new test failure that passed its flake retry. It fails no verdict, the OK line and `retried_passes` name it, and a later verify never forgives it as a baseline failure.
_Avoid_: flaky failure, forgiven failure

**Gate**:
The rule that a new or changed function may not exceed its ceiling; enforced by the pre-commit hook, `verify` and the Action.

### Debt

**Ratchet mark**:
A committed record that one function is allowed to sit at a known CRAP; it may only tighten.
_Avoid_: exemption, baseline entry, whitelist

**Metric stamp**:
The marks file's first comment line, `crapkit-analysis=N lizard=X.Y.Z`: the rules that produced the marks' numbers. `ratchet seed` sets it to the metric of the run it read, and verify refuses marks whose stamp differs from the running metric.
_Avoid_: version header

**Pardon**:
A ratchet mark lifting a gate breach. `rescore --gate` and `verify` pardon a changed function only while its CRAP sits at or under its mark; the pre-commit hook pardons any marked function, because a staged blob has no coverage to score.

**Ratchet regression**:
A marked function whose CRAP rose above its mark; never overridable.

**Claim**:
A session's hold on a function it is refactoring, so two sessions do not take the same item.

**Override**:
A written reason attached to a verdict that accepts a gate violation once; recorded in the audit trail.

### The worklist

**Worklist**:
The ranked view of every admitted function: active rows first, dormant rows after.

**Floor**:
The minimum complexity for admission to the worklist.

**Hot promotion**:
Admission under the floor because the file changes often.

**Active / dormant**:
Active rows are ranked by risk; dormant rows have no recent churn.

**Churn window**:
The months of history churn reads (`churn_window_months`). A commit counts while its commit date is at or after the window's cutoff; its recency weight reads the author date.
_Avoid_: floor for the window's start (Floor is worklist admission); call it the cutoff
