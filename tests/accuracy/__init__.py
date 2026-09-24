"""crapkit's calculation-accuracy suite.

Every number, label, ranking and pass/fail crapkit prints is checked here
against an expected value that does not come from crapkit's own output: a hand
row with an outside source, an outside tool, a clean-room model written from
the docs, or a relation the value must keep. kit/ holds what every packet
shares; each other directory is one packet. docs/accuracy.md says how to run a
tier and how to add a calculation.
"""
