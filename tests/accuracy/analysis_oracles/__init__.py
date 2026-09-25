"""analysis-oracles: what crapkit reads off source before any coverage joins.

The calculations here are the file universe, source decoding, function
discovery and spans, ccn_std, ccn_mod and the gated ccn, cognitive complexity,
nesting depth, nloc, the parameter count, unanalyzable files and twin notes,
the analysis cache, the inventory export, near-duplicate functions, `init` and
`watch`. Each is checked against a value that does not come from crapkit:

- probes/<lang>/probes.tsv: hand values, each row citing the McCabe or
  NIST SP 500-235 text, the Sonar cognitive complexity paper, or the
  language reference it was worked from;
- equivalence.tsv: one shape written in every language, which must read the
  same numbers everywhere;
- oracles/: outside tools (Python's ast and tokenize, radon, mccabe,
  complexipy, pylint, the TypeScript compiler, ESLint, sonarjs, tree-sitter
  counters written from the McCabe and Sonar texts, and the binaries in the
  accuracy image), each behind an adapter whose transforms are rulings rows;
- metamorphic relations: an edit that must leave a number alone, or move it
  by a known amount.

crapkit is read through its CLI (analysis_inventory.py) in every test that
calls.tsv names, so no expected value can come from crapkit's own code.
"""
