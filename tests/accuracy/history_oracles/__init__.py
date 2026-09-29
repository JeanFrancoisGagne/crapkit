"""The history packet: churn and its recency weight, change coupling, changed line
ranges, identity across renames, the ratchet burn-down, and explain's commit list.

Every expected value comes from git read another way (numstat records, a raw
object walk, `git show` of each version), from an outside tool (PyDriller,
bugspots, code-maat, mlxtend, pygit2, unidiff), or from a hand table worked
from the docs. oracles/ holds the readers; repos/ holds the git histories the
tests build.
"""
