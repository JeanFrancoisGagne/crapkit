"""The corpus-goldens packet's checks: the small corpus, its goldens, cross-surface
agreement, the MCP and CLI pairs, cross-platform digests, printed commands, the
Action scenarios, the wheel diff, the store upgrade and the corpus tool.

Seconds are serial ubuntu estimates for the push tier; the shared small-corpus
measurement is paid once per session by whichever check reaches it first.
"""
SHARD = "corpus"
_PACKET = "tests/accuracy/corpus_goldens/"
CHECKS = [
    {"name": "small corpus and goldens", "seconds": 20,
     "pytest": [_PACKET + "test_goldens.py"]},
]
