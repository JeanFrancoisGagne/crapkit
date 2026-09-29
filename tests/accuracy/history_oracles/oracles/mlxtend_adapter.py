"""Co-change counts from mlxtend 0.25.0's apriori over the walk's change sets.
No crapkit.

Each commit is a transaction of the files it changed. apriori's support of an
itemset is the share of transactions holding it; times the transaction count,
that is the count crapkit reads. Two runs, one named transform (ruling H7):
the files' own counts come from every transaction, and the pairs' counts from
the transactions of 30 files or fewer, because a bulk commit counts toward
each file it changed and couples no pair (README.md coupling).
"""
from __future__ import annotations

from collections import Counter

BULK = 30


def _supports(transactions: list[frozenset], max_len: int) -> Counter:
    import pandas as pd
    from mlxtend.frequent_patterns import apriori
    from mlxtend.preprocessing import TransactionEncoder

    rows = [sorted(t) for t in transactions if t]
    if not rows:
        return Counter()
    encoder = TransactionEncoder()
    frame = pd.DataFrame(encoder.fit(rows).transform(rows), columns=encoder.columns_)
    found = apriori(frame, min_support=1 / len(rows), use_colnames=True, max_len=max_len)
    return Counter({tuple(sorted(items)): round(support * len(rows))
                    for items, support in zip(found["itemsets"], found["support"])})


def counts(change_sets: list[frozenset]) -> tuple[Counter, Counter]:
    """(commits per file over every commit, shared commits per pair over non-bulk ones)."""
    files = Counter({items[0]: n for items, n in _supports(change_sets, 1).items()})
    return files, _pairs(_supports([s for s in change_sets if len(s) <= BULK], 2))


def _pairs(itemsets: Counter) -> Counter:
    return Counter({items: n for items, n in itemsets.items() if len(items) == 2})


def rule_confidence(change_sets: list[frozenset], pair: tuple[str, str]) -> float:
    """The larger confidence of the two rules a->b and b->a that
    association_rules finds over the transactions, as mlxtend reads them."""
    import pandas as pd
    from mlxtend.frequent_patterns import apriori, association_rules
    from mlxtend.preprocessing import TransactionEncoder

    rows = [sorted(t) for t in change_sets if t]
    encoder = TransactionEncoder()
    frame = pd.DataFrame(encoder.fit(rows).transform(rows), columns=encoder.columns_)
    found = apriori(frame, min_support=1 / len(rows), use_colnames=True, max_len=2)
    rules = association_rules(found, len(rows), metric="confidence", min_threshold=0)
    wanted = {frozenset({pair[0]}), frozenset({pair[1]})}
    hits = rules[rules["antecedents"].isin(wanted) & rules["consequents"].isin(wanted)]
    return float(hits["confidence"].max())
