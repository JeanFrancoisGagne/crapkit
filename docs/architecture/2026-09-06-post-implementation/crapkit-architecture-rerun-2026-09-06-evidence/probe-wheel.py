"""Build the reviewed source in a disposable archive; never install it globally."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
result = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
          "python": sys.version, "global_install": False}
with tempfile.TemporaryDirectory(prefix="crapkit-wheel-review-") as temp:
    checkout = Path(temp) / "source"
    archive = subprocess.check_output(["git", "archive", "--format=zip", "HEAD"], cwd=root)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        z.extractall(checkout)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    command = [sys.executable, "-m", "build", "--wheel", "--no-isolation"]
    done = subprocess.run(command, cwd=checkout, env=env, capture_output=True, text=True, timeout=120)
    (out / "wheel-build.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    result["build"] = {"command": command, "exit": done.returncode}
    wheels = list((checkout / "dist").glob("*.whl"))
    if done.returncode == 0 and len(wheels) == 1:
        wheel = wheels[0]
        result["wheel"] = {"name": wheel.name, "bytes": wheel.stat().st_size,
                           "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        install = Path(temp) / "wheel-files"
        with zipfile.ZipFile(wheel) as z:
            result["members"] = z.namelist()
            z.extractall(install)
        env["PYTHONPATH"] = str(install)
        smoke = subprocess.run([sys.executable, "-c", "import crapkit; from crapkit.cli import main; print(crapkit.__file__); main(['--version'])"],
                               cwd=temp, env=env, capture_output=True, text=True, timeout=30)
        result["smoke"] = {"exit": smoke.returncode, "stdout": smoke.stdout, "stderr": smoke.stderr,
                           "qualification": "Archive extraction checks wheel source and imports; dependencies come from existing interpreter, not a clean installation."}
(out / "wheel-proof.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k:v for k,v in result.items() if k != "members"}, indent=2))
