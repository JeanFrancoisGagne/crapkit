"""Change coupling counted from the commits a walk read, the way the docs define it.
No crapkit.

- docs/agent-json.md (`coupling --json`): `support` is shared commits and
  `confidence` is the max-direction ratio: the shared commits over the
  commits of either file, whichever ratio is larger.
- README.md (`coupling`): bulk commits never couple pairs. The bulk size is
  code-maat's: a change set of more than 30 files takes no part in pairing
  (code-maat --max-changeset-size, default 30). Such a commit still counts
  toward each file's own commits (ruling H7).
- docs/agent-json.md#the-coupling-cache: ranking drops any pair naming a file
  `git ls-files` no longer lists.
- The rank order is support times confidence, highest first, then the two
  paths (ruling H8: the docs name no order).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from itertools import combinations

from accuracy.kit import exact

BULK = 30


@dataclass(frozen=True)
class Pair:
    files: tuple[str, str]
    support: int
    confidence: Decimal  # half-even to 4 places

    @property
    def rank(self):
        return (-self.support * self.confidence, self.files)


def counts(file_sets: list[frozenset]) -> tuple[Counter, Counter]:
    """(commits per file, shared commits per sorted pair) over the change sets."""
    files, pairs = Counter(), Counter()
    for changed in file_sets:
        files.update(changed)
        if len(changed) <= BULK:
            pairs.update(combinations(sorted(changed), 2))
    return files, pairs


def confidence(files: Counter, pair: tuple[str, str], support: int) -> Fraction:
    return max(Fraction(support, files[pair[0]]), Fraction(support, files[pair[1]]))


def ranked(file_sets: list[frozenset], tracked: set[str], min_support: int = 1,
           min_confidence: Fraction = Fraction(0)) -> list[Pair]:
    """Every pair of tracked files at or over both thresholds, in rank order."""
    files, pairs = counts(file_sets)
    kept = [Pair(pair, support, exact.half_even(confidence(files, pair, support), 4))
            for pair, support in pairs.items()
            if set(pair) <= tracked and support >= min_support
            and confidence(files, pair, support) >= min_confidence]
    return sorted(kept, key=lambda pair: pair.rank)
