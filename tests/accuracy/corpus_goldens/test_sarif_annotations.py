"""SARIF and GitHub annotations: the log validates against the published SARIF
2.1.0 schema, every artifact uri is the RFC 3986 percent-encoding of its repo
path, and every workflow command escapes its data and properties the way the
GitHub Actions toolkit does.

Hand cases: files whose names hold a space, a `#`, a comma, a percent sign and
a non-ASCII letter, each with one ccn-7 function in a cc-only scope, so each is
a `crapkit/over-target` result (CRAP 7.0 over ceiling 6). The expected strings
are written here from the sources, not read from crapkit:

- RFC 3986 section 2.1-2.3: a URI keeps unreserved characters (ALPHA DIGIT
  - . _ ~) and `/` as path separators, and percent-encodes every other byte of
  the UTF-8 name with uppercase hex.
- actions/toolkit packages/core/src/command.ts: escapeData turns % CR LF into
  %25 %0D %0A; escapeProperty also turns : and , into %3A and %2C.
- SARIF 2.1.0 (OASIS, errata01) section 3.13.3: `$schema` is an absolute URI
  from which the schema can be obtained. schemas/sarif-schema-2.1.0.json is
  that schema, fetched on 2026-09-24 from
  https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json
  (sha256 c3b4bb2d6093897483348925aaa73af03b3e3f4bd4ca38cef26dcb4212a2682e).
"""
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
import rfc3986
from rfc3986 import exceptions, validators

from accuracy.kit import drive, repos, rulings, surfaces

pytestmark = pytest.mark.process

SCHEMA = Path(__file__).resolve().parent / "schemas" / "sarif-schema-2.1.0.json"
SCHEMA_SHA256 = "c3b4bb2d6093897483348925aaa73af03b3e3f4bd4ca38cef26dcb4212a2682e"
# Where the SARIF 2.1.0 schema can be obtained (HTTP 200 on 2026-09-24): the
# OASIS errata01 location and the SchemaStore copy.
SCHEMA_URIS = ("https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/"
               "sarif-schema-2.1.0.json", "https://json.schemastore.org/sarif-2.1.0.json")
UNRESERVED = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~/")
BODY = ('(v):\n    if v == 1:\n        return 1\n    if v == 2:\n        return 2\n'
        '    if v == 3:\n        return 3\n    if v == 4:\n        return 4\n'
        '    if v == 5:\n        return 5\n    if v == 6:\n        return 6\n    return 0\n')
# (repo path, the def line's name part): each file holds one ccn-7 function.
CASES = (
    ("src/a b.py", "def spaced"),
    ("src/x#y.py", "def hashed"),
    ("src/a,b.py", "def comma"),
    ("src/p%q.py", "def percent"),
    ("src/été.py", "def summer"),
)
CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
          'languages = ["python"]\ncoverage_optional = true\n')


def percent_encoded(path: str) -> str:
    """RFC 3986 2.1: every byte outside the unreserved set and `/`, as %XX."""
    return "".join(chr(byte) if byte in UNRESERVED else f"%{byte:02X}"
                   for byte in path.encode("utf-8"))


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


@pytest.fixture(scope="module")
def emitted(tmp_path_factory):
    """One coverage run over the hand cases, with --sarif and --github."""
    files = {path: f"{head}{BODY}" for path, head in CASES}
    spec = repos.Spec(steps=(repos.Commit(files={"crapkit.toml": CONFIG, **files}),))
    built = repos.build(spec, tmp_path_factory.mktemp("sarif") / "repo")
    out = built.root / "out.sarif"
    result = drive.Driver(built.root, date_now=repos.EPOCH + 86_400).run(
        "coverage", "--sarif", str(out), "--github")
    assert result.code == 0, result.stderr
    commands, _ = surfaces.split_workflow_commands(result.stdout)
    return json.loads(out.read_text(encoding="utf-8")), commands


def test_the_vendored_schema_is_the_published_one():
    assert hashlib.sha256(SCHEMA.read_bytes()).hexdigest() == SCHEMA_SHA256


def test_the_log_validates_against_the_sarif_schema(emitted):
    sarif, _ = emitted
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft4Validator(schema, format_checker=jsonschema.FormatChecker())

    assert [error.message for error in validator.iter_errors(sarif)] == []


def _uris(sarif: dict) -> list[str]:
    return [location["physicalLocation"]["artifactLocation"]["uri"]
            for result in sarif["runs"][0]["results"] for location in result["locations"]]


def test_every_uri_is_the_percent_encoding_of_its_path(emitted):
    sarif, _ = emitted

    assert sorted(_uris(sarif)) == sorted(percent_encoded(path) for path, _ in CASES)
    assert [uri for uri in _uris(sarif) if not _valid_reference(uri)] == []


def _valid_reference(uri: str) -> bool:
    """RFC 3986 grammar for a relative reference's path, per the rfc3986 package."""
    validator = validators.Validator().check_validity_of("path")
    try:
        validator.validate(rfc3986.uri_reference(uri))
    except exceptions.ValidationError:
        return False
    return True


def test_every_result_says_crap_7_over_ceiling_6(emitted):
    sarif, _ = emitted
    messages = sorted(result["message"]["text"] for result in sarif["runs"][0]["results"])

    assert messages == sorted(f"{head[4:]}( v ): CRAP 7.0 over ceiling 6 (ccn 7, cov 0%) -> "
                              "decompose" for _, head in CASES)


def _annotation(level: str, path: str, head: str) -> str:
    """The workflow command for one finding. The level is not documented for an
    over-target finding, so it is the one the SARIF result for it carries."""
    message = f"{head[4:]}( v ): CRAP 7.0 over ceiling 6 (ccn 7, cov 0%) -> decompose"
    return (f"::{level} file={escape_property(path)},line=1,"
            f"title={escape_property('crapkit/over-target')}::{escape_data(message)}")


def _levels(sarif: dict) -> set[str]:
    return {result["level"] for result in sarif["runs"][0]["results"]}


def test_every_annotation_escapes_as_the_toolkit_does(emitted):
    sarif, commands = emitted
    [level] = _levels(sarif)

    assert sorted(commands.splitlines()) == sorted(_annotation(level, path, head)
                                                   for path, head in CASES)


def _schema_uri(sarif: dict) -> str:
    return sarif["$schema"]


@rulings.applies("CG2")
def test_the_schema_uri_names_a_place_the_schema_can_be_obtained(emitted):
    sarif, _ = emitted
    printed = _schema_uri(sarif)

    rulings.pin_ruling("CG2", crapkit=printed if printed not in SCHEMA_URIS else "resolvable",
                       oracle="resolvable")
