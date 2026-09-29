"""suite-strength: how strong the accuracy suite itself is.

Three questions, each answered against something outside crapkit's output:
does every past calculation bug go red on its before commit and green on its
fix (retro/, tools/accuracy/retro.py); does every mutant of a calculation's
code die, or sit on a keyed survivor list with its reason (mutation/,
tools/accuracy/mutation.py); and do `crapkit mutate`'s own verdicts match
fixtures whose every mutant was worked out by hand. The release gate
(tools/release/release.py) reads the receipts these checks write.
"""
