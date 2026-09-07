# Implementation of the fresh architecture audit

Base: `c2d3515581d410d8c14849063dac27af843a3729`. The user authorized all 17
opportunities and a separate test/CI optimization workstream.

| Owner | Candidates | Integration contract |
|---|---|---|
| Execution | EX1, EX2, STATE-03 | Own command descendants, worker filesystem identity and literal packet execution |
| Measurements/state | CORE-02, CORE-03, CORE-04, MEASURE-01, STATE-01, STATE-02 | Admit artifacts once; settle one final verdict; preserve records and bound duplication allocation |
| Tests/CI | Additional optimization job | Preserve cases, assertions, coverage and wheel identity while reducing measured cost |
| Integration | CORE-01, CORE-S01, CORE-S02, UI1 through UI5 | Exact Git/path transport, interface behavior, shared guidance and cross-owner verification |

The first three owners work in detached checkouts; integration owns the main
checkout. A Git-derived symbol map proves source files disjoint. Shared test
fixtures and workflow files belong to Tests/CI. Existing test files are reserved
before editing; each implementation adds workstream-specific regression files.
Shared documentation has one integration owner.

The public seams and failure cases in the audit are the test contract. Each fix
keeps its failing baseline output, then passes the same test. Full suites run
through one integration schedule after focused checks. Performance claims require
the same cases, interpreter and inputs, with cold results separate from warm
medians. Source and installed-wheel runs must preserve subprocess coverage and
complete JUnit evidence.

Cross-owner checks include normal lane JUnit admission, packet execution through
test-scoped, mutation input capture through Git, historical ratchet record reads,
and the next trusted baseline after verify. Each caller migration lands before
its old path is removed.

## Initial design decisions

Git patch formatting belongs to one option set used by staged, prestarted,
baseline and history reads. Parsed patches must ignore presentation settings,
external diff programs and text conversion; configuration inspection still reads
the user's actual setting.

Command completion owns descendant cleanup. An untimed command remains untimed;
its children cannot outlive completion and write into resources another run owns.
Mutation must establish regular-file identity before worker writes or refuse the
input with a useful error.

Portable record changes must leave ordinary output byte-stable and read existing
formats. A format identifier is required when escaping changes interpretation.
The final verdict owns every refusal before output or persistence applies it.

## Completion checklist

- [x] Every candidate has an implemented result and regression evidence.
- [x] Test/CI bottlenecks have measured remedies and case/coverage parity.
- [x] Independent review covers the integrated changes and cross-owner calls.
- [ ] Complete source and installed-wheel checks pass with full JUnit evidence.
- [ ] Crapkit analyzes and verifies the integrated project against the preserved baseline.
- [ ] Results, remaining platform limits and reproduction commands are committed.
