"""Fixture coverage generator: writes an istanbul-format artifact for src/app.ts.

Stands in for a real vitest --coverage run so the e2e lane stays hermetic; the
schema matches @vitest/coverage-v8's AST-remapped coverage-final.json.
"""
import json
import os

root = os.getcwd()
app = os.path.join(root, "src", "app.ts")
artifact = {
    app: {
        "path": app,
        "fnMap": {
            "0": {"name": "dispatch", "decl": {"start": {"line": 1}}, "loc": {"start": {"line": 1}, "end": {"line": 10}}},
        },
        "f": {"0": 3},
        "branchMap": {
            "0": {"loc": {"start": {"line": 2}}, "locations": [{"start": {"line": 2}}, {"start": {"line": 3}}]},
            "1": {"loc": {"start": {"line": 4}}, "locations": [{"start": {"line": 4}}, {"start": {"line": 5}}]},
        },
        "b": {"0": [1, 1], "1": [1, 0]},
    }
}
os.makedirs(os.path.join(root, "coverage"), exist_ok=True)
with open(os.path.join(root, "coverage", "coverage-final.json"), "w", encoding="utf-8") as f:
    json.dump(artifact, f)
print("wrote coverage/coverage-final.json")
