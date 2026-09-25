"""Disposable and in-memory checks for the current architecture review."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(root / "src"))
from crapkit.config import load_config_text
import jsonschema

schema = json.loads((root / "crapkit.schema.json").read_text())
base = '\n[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n'
shapes = {
    "ratchet_file_boolean": '[crapkit]\nratchet_file=false\n',
    "exclude_globs_string": '[exclude]\nglobs="tests/**"\n',
    "scoped_tests_number": '[crapkit.scoped_tests]\nsrc=123\n',
    "mutation_command_number": '[crapkit]\nmutation_command=123\n',
    "alert_command_boolean": '[crapkit]\nalert_command=false\n',
}
import tomllib
checks = []
for name, fragment in shapes.items():
    text = fragment + base
    errors = list(jsonschema.Draft7Validator(schema).iter_errors(tomllib.loads(text)))
    try:
        cfg = load_config_text(text)
        runtime = "accepted"
        value = {k: getattr(cfg, k) for k in ("ratchet_file", "exclude_globs", "scoped_tests", "mutation_command", "alert_command")}
    except Exception as exc:
        runtime = f"{type(exc).__name__}: {exc}"
        value = None
    checks.append({"case": name, "toml": text, "schema": "rejected" if errors else "accepted", "runtime": runtime, "value": value})
(out / "config-shapes.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8")

spec = importlib.util.spec_from_file_location("review_release", root / "tools/release/release.py")
release = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release
spec.loader.exec_module(release)
calls = []
release._preflight = lambda *args: {"head": "fixed-head", "version": "9.9.9"}
release._run_or_untag = lambda step, *args: calls.append({"step": step.name, "commands": step.commands})
for _ in range(2):
    release.run("stage2b", "9.9.9", root)
(out / "release-replay.json").write_text(json.dumps({"method": "In-memory adapters replace preflight and all external execution; no command executed", "calls": calls}, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"config_shapes": checks, "release_steps_twice": [c["step"] for c in calls]}, indent=2))
