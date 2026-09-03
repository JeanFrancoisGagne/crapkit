---
status: accepted
---

# Configuration is found by walking up, nearest wins, and a bare `.git` stops the walk

Before 0.5.0 every command read `crapkit.toml` from the working directory only, so a monorepo lead standing in `web/` got `no crapkit.toml` although the root configuration one level up claimed `web/`; the plugin hook already walked up on its own. 0.5.0 makes the hook's walk the rule for every command when `--repo` is not given: start at the working directory, stop at the first directory holding `crapkit.toml` (nearest wins, so a nested configuration shadows an ancestor's), and stop at a `.git` entry, file or directory, that holds no configuration, so a linked worktree or a nested repository never borrows a parent's store. When the found root is not the working directory, one stderr line names it, and relative path arguments are rebased from where the user stands. An explicit `--repo` names an exact root and walks nowhere.

Alternatives we did not take: stopping at the first `.git` regardless of an ancestor configuration (breaks the case of a repository nested under the parent that carries the config), and requiring the configuration to sit beside a `.git` (misses a monorepo root without one).

Consequence: a stray `crapkit.toml` in a non-git ancestor, such as a home directory, is adopted with only the stderr line as warning. `init` refuses to write a configuration under an ancestor that already claims the directory through a scope path, which closes the other half of that hole.
