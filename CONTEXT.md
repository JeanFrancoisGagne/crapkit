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
The one-word action attached to a scored row: cover, decompose, and their kin.

### The corpus

**Scope**:
A named set of path prefixes and languages that shares one ceiling and one set of lanes.

**Lane**:
One configured test command that writes one coverage artifact for one scope.

**Artifact**:
The coverage file a lane writes and crapkit reads.
_Avoid_: report (a report is crapkit's own HTML page)

**Exclude**:
A glob that removes files from the corpus before inventory.

### Runs

**Run**:
One scored snapshot: an inventory joined with the artifacts of the lanes that ran.

**Partial run**:
A run in which some declared lanes did not run; never a baseline.

**Baseline**:
The trusted earlier run a verdict compares against.

**Verdict**:
The outcome of `verify`: the gate result, ratchet regressions and new test failures against the baseline.

**Gate**:
The rule that a new or changed function may not exceed its ceiling; enforced by the pre-commit hook, `verify` and the Action.

### Debt

**Ratchet mark**:
A committed record that one function is allowed to sit at a known CRAP; it may only tighten.
_Avoid_: exemption, baseline entry, whitelist

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
