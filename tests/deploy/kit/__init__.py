"""The shared kit every deploy cell builds on.

A cell installs crapkit the way a user does and drives it through the surface
the user touches. The kit owns everything a cell must not re-invent:

  sandbox       an environment that holds nothing from the machine running it:
                the pinned toolchain on PATH, a fresh HOME, offline pip and uv
  transcript    every command a cell ran, its exit code and output, written
                to <out>/transcripts/ for the reader of a red cell
  cells         @cell: the markers run.py selects on and the JUnit properties
  wheels        the wheelhouse, the candidate and N-1, read from the locks
  fakewheel     a released wheel repacked under another version number
  docsnip       the install lines, read verbatim from the stamped docs
  installers    one installer per channel, each running the line the docs
                print for it
  gitmirror     github.com/JeanFrancoisGagne/crapkit answered by a local mirror
  gitsurf       the git surfaces a user wires crapkit into, each ending in a
                real `git commit` or `git merge`
  act           GitHub Actions jobs run offline by act, for the Action cells
  stub_gh/      the `gh` those jobs call, answered from a state directory
  httpstub      a server on 127.0.0.1 that records every request; pyindex
                and the model stubs are handlers on it
  pyindex       a PEP 503 index on 127.0.0.1 with PyPI's cache headers
  shim          a `crapkit` launcher that records how a harness started it
  shim_pkg/     the package that launcher's wheel is built from
  mcp_client    a stdio MCP client that waits on replies, never on sleeps;
                mcp_node_client.mjs drives a server through the TypeScript SDK
  sdk_status    the MCP servers a Claude Agent SDK session connects, as the
                SDK reports them; sdk_status.mjs asks the TypeScript SDK
  profiles      what each agent harness does when it starts crapkit, one TOML
                file per harness under tests/deploy/profiles/
  writers       crapkit's server written into a harness's own config file,
                read back the way that harness reads it
  hooks_rules   what a harness does with the plugin's hooks/hooks.json
  vscode_probe_ext/
                a test-only VS Code extension that starts the MCP servers and
                reports the tools VS Code then offers its chat
  repos         fixture repos built once per session behind a file lock
  state         a repo as an older crapkit left it, and the upgrade guide's
                steps that bring it to the candidate
  state_manifest
                what a crapkit repo keeps on disk, taken before and after an
                upgrade
  stub_anthropic, stub_openai
                scripted model APIs that record every request body
  clock         what run.py --faketime changes for a cell: the environment
                name libfaketime adds, the releases it cannot start, and the
                programs that keep the real clock

The environment a run.py invocation hands the kit:

  CRAPKIT_DEPLOY_TOOLCHAIN   toolchain.json: where the pinned tools live
  CRAPKIT_DEPLOY_CANDIDATE   the candidate.py output dir (staged/, dist/)
  CRAPKIT_DEPLOY_MIRROR      a bare mirror cloned from the exported bundle
  CRAPKIT_DEPLOY_OUT         where JUnit and transcripts go
  CRAPKIT_DEPLOY_SRC         the source tree wheels reads the locks from;
                             this checkout when unset
  CRAPKIT_DEPLOY_IMAGE_DIGEST
                             the digest of the image a cell ran in, for its
                             JUnit record; "native" when unset
"""
