"""Configuration field facts shared by admission, editor hints and doctor.

Only this module's fixed field vocabulary is interpreted. Unknown fields remain
doctor findings so configurations can survive version skew.
"""
from copy import deepcopy
import math

from .errors import ConfigError

_SCHEMA = {'$schema': 'http://json-schema.org/draft-07/schema#',
 '$id': 'https://raw.githubusercontent.com/JeanFrancoisGagne/crapkit/main/crapkit.schema.json',
 'title': 'crapkit.toml',
 'description': 'crapkit configuration: scopes, coverage lanes, exclusions, targets',
 'type': 'object',
 'additionalProperties': False,
 'required': ['scope'],
 'properties': {'crapkit': {'type': 'object',
                            'additionalProperties': False,
                            'properties': {'target': {'type': 'integer',
                                                      'minimum': 1,
                                                      'description': 'repo-wide CRAP ceiling; above it '
                                                                     'a function cannot be saved by '
                                                                     'coverage (default 6)'},
                                           'churn_window_months': {'type': 'integer',
                                                                   'minimum': 1,
                                                                   'description': 'git log window for '
                                                                                  'churn weighting '
                                                                                  '(default 12)'},
                                           'worklist_floor': {'type': 'integer',
                                                              'minimum': 1,
                                                              'description': 'minimum ccn for worklist '
                                                                             'admission (default 5)'},
                                           'worklist_top': {'type': 'integer',
                                                            'minimum': 1,
                                                            'description': 'worklist active-list cap '
                                                                           '(default 50)'},
                                           'ratchet_file': {'type': 'string',
                                                            'description': 'committed marks file '
                                                                           '(default '
                                                                           'crapkit-ratchet.tsv)'},
                                           'alert_command': {'type': 'string',
                                                             'description': 'command that receives '
                                                                            'digest/override alerts on '
                                                                            'stdin'},
                                           'notes': {'type': 'array',
                                                     'items': {'type': 'string'},
                                                     'description': 'repo-wide operational traps, '
                                                                    'quoted verbatim to whoever is '
                                                                    'about to edit this repo'},
                                           'scoped_tests': {'type': 'object',
                                                            'additionalProperties': {'type': 'string'},
                                                            'description': 'scope name -> isolated test '
                                                                           'command template ({files} '
                                                                           'placeholder)'},
                                           'mutation_command': {'type': 'string',
                                                                'description': 'suite run once per '
                                                                               'mutant; nonzero exit = '
                                                                               'killed'},
                                           'mutation_timeout_seconds': {'type': 'integer',
                                                                        'minimum': 1,
                                                                        'description': 'per-mutant '
                                                                                       'timeout; expiry '
                                                                                       'counts as '
                                                                                       'killed (default '
                                                                                       '300)'},
                                           'mutation_workers': {'type': 'integer',
                                                                'minimum': 1,
                                                                'description': 'mutants run in '
                                                                               'parallel, one detached '
                                                                               'git worktree each '
                                                                               '(default 1)'},
                                           'diff_uncovered_max': {'type': 'integer',
                                                                  'minimum': 0,
                                                                  'description': 'verify exits 9 when '
                                                                                 'more changed lines '
                                                                                 'than this never ran '
                                                                                 '(absent = warn only)'},
                                           'debt_max_age_months': {'type': 'integer',
                                                                   'minimum': 0,
                                                                   'description': 'ratchet report '
                                                                                  '--enforce flags '
                                                                                  'marks older than '
                                                                                  'this'},
                                           'repayment_min_per_30d': {'type': 'integer',
                                                                     'minimum': 0,
                                                                     'description': 'ratchet report '
                                                                                    '--enforce flags '
                                                                                    'fewer repayments '
                                                                                    'than this while '
                                                                                    'debt is open'},
                                           'max_parallel_lanes': {'type': 'integer',
                                                                  'minimum': 1,
                                                                  'description': 'lanes running at once '
                                                                                 'during '
                                                                                 'coverage/verify; 1 = '
                                                                                 'strictly serial '
                                                                                 '(default 1)'},
                                           'analysis_workers': {'type': 'integer',
                                                                'minimum': 0,
                                                                'description': 'lizard process pool '
                                                                               'size; 0 = one worker '
                                                                               'per core (default 0)'},
                                           'tighten_max_jump': {'type': 'number',
                                                                'minimum': 1,
                                                                'description': 'verify holds a mark '
                                                                               'whose CRAP moved by '
                                                                               'more than this factor '
                                                                               'between two runs of the '
                                                                               'same commit (default '
                                                                               '2.0)'}}},
                'scope': {'type': 'array',
                          'minItems': 1,
                          'items': {'type': 'object',
                                    'additionalProperties': False,
                                    'required': ['name', 'paths', 'languages'],
                                    'properties': {'name': {'type': 'string'},
                                                   'paths': {'type': 'array',
                                                             'items': {'type': 'string'},
                                                             'minItems': 1},
                                                   'languages': {'type': 'array',
                                                                 'items': {'enum': ['typescript',
                                                                                    'tsx',
                                                                                    'javascript',
                                                                                    'python',
                                                                                    'swift',
                                                                                    'go',
                                                                                    'rust',
                                                                                    'shell',
                                                                                    'cpp',
                                                                                    'objectivec',
                                                                                    'vue',
                                                                                    'java',
                                                                                    'zig',
                                                                                    'powershell']},
                                                                 'minItems': 1},
                                                   'target': {'type': 'integer',
                                                              'minimum': 1,
                                                              'description': 'per-scope ceiling '
                                                                             'overriding the repo '
                                                                             'target'},
                                                   'coverage_optional': {'type': 'boolean',
                                                                         'description': 'code no test '
                                                                                        'can reach: '
                                                                                        'scored cc-only '
                                                                                        '(crap = ccn), '
                                                                                        'needs no lane'},
                                                   'notes': {'type': 'array',
                                                             'items': {'type': 'string'},
                                                             'description': 'operational traps specific '
                                                                            'to this scope, quoted '
                                                                            'verbatim to whoever edits '
                                                                            'its files'}}}},
                'lane': {'type': 'array',
                         'items': {'type': 'object',
                                   'additionalProperties': False,
                                   'required': ['name', 'command', 'artifact', 'parser', 'scopes'],
                                   'properties': {'name': {'type': 'string'},
                                                  'command': {'type': 'string'},
                                                  'artifact': {'type': 'string',
                                                               'description': 'repo-relative coverage '
                                                                              'artifact the command '
                                                                              'writes'},
                                                  'parser': {'enum': ['istanbul', 'coveragepy']},
                                                  'scopes': {'type': 'array',
                                                             'items': {'type': 'string'}},
                                                  'cwd': {'type': 'string'},
                                                  'path_prefix': {'type': 'string',
                                                                  'description': 'prefix joined onto '
                                                                                 'coverage.py relative '
                                                                                 'paths'},
                                                  'env': {'type': 'object',
                                                          'additionalProperties': {'type': 'string'}},
                                                  'full_suite': {'type': 'boolean',
                                                                 'description': 'false permits '
                                                                                'positional narrowing '
                                                                                'in a pytest coverage '
                                                                                'command'},
                                                  'container_ok': {'type': 'boolean'},
                                                  'results_artifact': {'type': 'string',
                                                                       'description': 'junit XML '
                                                                                      'feeding the '
                                                                                      'no-NEW-failures '
                                                                                      'check'},
                                                  'timeout_seconds': {'type': 'integer',
                                                                      'minimum': 0,
                                                                      'description': 'crapkit kills the '
                                                                                     'command past this '
                                                                                     '(0 = no timeout)'},
                                                  'no_progress_seconds': {'type': 'integer',
                                                                          'minimum': 0,
                                                                          'description': 'crapkit kills '
                                                                                         'the command '
                                                                                         'when its log '
                                                                                         'has not grown '
                                                                                         'for this many '
                                                                                         'seconds (0 = '
                                                                                         'no progress '
                                                                                         'watch)'},
                                                  'retries': {'type': 'integer',
                                                              'minimum': 0,
                                                              'description': 'reruns after a timeout or '
                                                                             'missing artifact'},
                                                  'retest_command': {'type': 'string',
                                                                     'description': '{tests} template '
                                                                                    'rerunning just the '
                                                                                    'newly-failed ids '
                                                                                    'before exit 8'}}}},
                'exclude': {'type': 'object',
                            'additionalProperties': False,
                            'properties': {'globs': {'type': 'array', 'items': {'type': 'string'}},
                                           'max_file_bytes': {'type': 'integer',
                                                              'minimum': 0,
                                                              'description': 'files larger than this '
                                                                             'many bytes leave the '
                                                                             'corpus, minified blobs '
                                                                             'included (absent = no '
                                                                             'limit)'}}}}}


_TYPES = {"object": (dict,), "array": (list,), "string": (str,),
          "integer": (int,), "number": (int, float), "boolean": (bool,)}


def schema() -> dict:
    """The complete editor schema, independent of caller mutations."""
    return deepcopy(_SCHEMA)


def known_keys() -> dict[str, set[str]]:
    """Doctor's table vocabulary from the fields admission reads."""
    properties = _SCHEMA["properties"]
    tables = {name: rule.get("items", rule) for name, rule in properties.items()}
    return {"": set(properties), **{name: set(rule["properties"])
                                  for name, rule in tables.items()}}


def enum_values(table: str, field: str) -> tuple[str, ...]:
    """Supported field values used by configuration consumers."""
    rule = _SCHEMA["properties"][table]["items"]["properties"][field]
    return tuple(rule.get("items", rule)["enum"])


def admit(raw: dict) -> None:
    """Reject malformed known fields before any configuration is constructed."""
    _value(raw, _SCHEMA, "crapkit.toml")


def _value(value, rule: dict, label: str) -> None:
    _scalar(value, rule, label)
    if rule.get("type") == "object":
        _object(value, rule, label)
    if rule.get("type") == "array":
        _array(value, rule, label)


def _scalar(value, rule: dict, label: str) -> None:
    kind = rule.get("type")
    if kind and type(value) not in _TYPES[kind]:
        raise ConfigError(f"{label} must be {kind}, got {value!r}")
    if "enum" in rule and value not in rule["enum"]:
        raise ConfigError(f"{label}: unsupported value {value!r}; expected {rule['enum']}")
    _finite_number(value, kind, label)
    _minimum(value, rule, label)


def _finite_number(value, kind: str | None, label: str) -> None:
    if kind != "number":
        return
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ConfigError(f"{label} must fit a finite number, got {value!r}")


def _minimum(value, rule: dict, label: str) -> None:
    if "minimum" not in rule:
        return
    if value < rule["minimum"]:
        raise ConfigError(f"{label} must be >= {rule['minimum']}, got {value!r}")


def _object(value: dict, rule: dict, label: str) -> None:
    missing = set(rule.get("required", ())) - value.keys()
    if missing:
        raise ConfigError(f"{label} is missing a required key: {sorted(missing)}")
    properties = rule.get("properties", {})
    extra = rule.get("additionalProperties")
    for key, item in value.items():
        child = properties.get(key, extra)
        if isinstance(child, dict):
            _value(item, child, f"{label}.{key}")


def _array(value: list, rule: dict, label: str) -> None:
    if len(value) < rule.get("minItems", 0):
        raise ConfigError(f"{label} must contain at least {rule['minItems']} item(s)")
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if isinstance(item, dict) and "name" in item:
            item_label = f"{label} {item['name']!r}"
        _value(item, rule["items"], item_label)
