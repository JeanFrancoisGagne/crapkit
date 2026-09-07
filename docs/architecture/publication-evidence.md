# Public copies of historical evidence

Two evidence archives have separate public copies. Some historical source/wheel
inputs are withheld for repository anonymity. Every retained member keeps its
original bytes. The complete originals and omitted inputs remain preserved
privately with unchanged SHA256 hashes.

| Report | Public archive | Retained original members | Omitted members | Publication receipt |
| --- | --- | ---: | ---: | --- |
| [18 improvements](2026-09-06-improvements/implementation.md) | [focused-evidence-public.zip](2026-09-06-improvements/focused-evidence-public.zip) | 604 | 10 | [manifest](2026-09-06-improvements/focused-evidence-public.json) |
| [17 candidates and CI](2026-09-07-implementation/REPORT.md) | [evidence-public.zip](2026-09-07-implementation/evidence-public.zip) | 299 | 1 | [manifest](2026-09-07-implementation/evidence-public.json) |

Each public archive adds one `PUBLICATION.json`. It identifies the original
archive by name, size and SHA256, lists every retained and omitted member with
its original size and SHA256, and explains the publication scope. The external
receipt also records the public archive's own size and SHA256.

Original verification manifests, logs, reports and receipts inside these copies
remain unchanged. They describe the original complete archive and measured
commits. They are not current member inventories. The publication manifest is the
inventory for the public copy. Current report pages add this publication note;
the historical report copies inside the archives keep their recorded bytes.

## Omitted inputs

The 18-improvement archive withholds four historical wheel files and six startup
source files. The later implementation archive withholds one duplication input
that embeds historical source text. Exact member paths and original hashes are
listed in the publication receipts. No test result, coverage artifact, saved
ledger, timing result, or verification verdict was replaced or remeasured.

The public evidence still supports inspection of recorded results and all
retained checks. Replaying a check that needs an omitted wheel, source file or
duplication input requires the private original. A hash identifies that input;
it does not substitute for access to its bytes.

## Verify or reproduce the publication copy

From the repository root, check both public archives and their receipts:

```sh
python tools/evidence/publication.py --check
```

This checks exact member inventories, every retained member's bytes and SHA256,
the embedded publication manifest, and the public archive's receipt hash. It
does not claim a new test or verification run.

To reproduce these copies from the exact private originals, preserve their
repository-relative paths under a private directory and run:

```sh
python tools/evidence/publication.py --originals <private-directory>
```

The generator requires the recorded original archive hashes. It refuses to
overwrite either original, omits only the listed members, and creates
deterministic public copies. Original archive collectors and validators still
apply to the original private archives; their complete-inventory requirements
were not relaxed for these derivatives.

## Existing history

The originals entered commits `b11e2c1` and `eb848c1` and had reached public
`main` at `6ddef3d` before this correction. They are removed from the current
documentation tree, not from historical Git commits. This publication change
does not claim retroactive deletion of previously public bytes. Earlier package
versions are also historical artifacts and are not rebuilt or overwritten.
