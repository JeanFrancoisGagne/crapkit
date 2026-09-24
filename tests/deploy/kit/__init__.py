"""The shared kit every deploy cell builds on.

A cell installs crapkit the way a user does and drives it through the surface
the user touches. The kit owns everything a cell must not re-invent:

  sandbox       an environment that holds nothing from the machine running it:
                the pinned toolchain on PATH, a fresh HOME, offline pip and uv
  transcript    every command a cell ran, its exit code and output, written
                to <out>/transcripts/ for the reader of a red cell
  cells         @cell: the markers run.py selects on and the JUnit properties
  wheels        the wheelhouse, the candidate and N-1, read from the locks
  docsnip       the install lines, read verbatim from the stamped docs
  gitmirror     github.com/JeanFrancoisGagne/crapkit answered by a local mirror
  pyindex       a PEP 503 index on 127.0.0.1 with PyPI's cache headers
  shim          a `crapkit` launcher that records how a harness started it
  mcp_client    a stdio MCP client that waits on replies, never on sleeps
  repos         fixture repos built once per session behind a file lock
  stub_anthropic, stub_openai
                scripted model APIs that record every request body

The environment a run.py invocation hands the kit:

  CRAPKIT_DEPLOY_TOOLCHAIN   toolchain.json: where the pinned tools live
  CRAPKIT_DEPLOY_CANDIDATE   the candidate.py output dir (staged/, dist/)
  CRAPKIT_DEPLOY_MIRROR      a bare mirror cloned from the exported bundle
  CRAPKIT_DEPLOY_OUT         where JUnit and transcripts go
"""
