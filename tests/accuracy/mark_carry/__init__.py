"""The mark-carry packet: a mark keeps its value when its key moves.

Every writer that changes a mark's key goes through one rewrite. The calcs
here check that rewrite against values worked without crapkit: hand_rekey.tsv
holds a marks file's bytes before and after each move, a property holds the
value strings as one multiset across any move set, and a move followed by its
inverse gives back the file's exact bytes. The merge driver keeps a mark one
branch moved as one mark: hand_merge.tsv holds the merged file for each case,
and an oracle that edits each side from the base marks agrees with it. The
tests read crapkit's answer through a module loaded by name, never an import
statement.
"""
