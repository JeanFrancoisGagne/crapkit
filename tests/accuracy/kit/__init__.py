"""The shared kit every accuracy packet imports and none of them edits.

exact, strategies and settings import no crapkit: they compute expected values,
so a crapkit bug cannot reach them. drive, surfaces and corpus_run run crapkit
and read what it printed.
"""
