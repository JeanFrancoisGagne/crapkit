# Portable records

Inventory exports, scored exports, portable baselines and ratchets use the same
row encoding. Ordinary rows keep their existing tab-separated bytes and column
counts. Fields preserve literal backslashes; readers never treat them as escapes.

Use these records for `inventory --export`, `coverage --export`,
`verify --emit-baseline` and the configured ratchet file. JSON command payloads
use the separate [JSON field contract](agent-json.md#the-schema-field).

A row containing a tab or line separator, or starting with `#`, uses two fields:
`@crapkit-record-v1`, a tab, then a JSON array of string fields. For example:

```text
@crapkit-record-v1	["src/a\nb.py","f( )","12.0000"]
```

The separator between the marker and JSON is a literal tab. The `\n` inside JSON
represents a newline in the filename. Each encoded row describes itself, so Git
history can decode an isolated added or removed row without its file header.

Readers split physical rows at LF or CRLF. Unicode line separators inside legacy
raw rows remain field data. A raw three-column ratchet row starting with `#` is
data; a `#` line without tab fields is a comment. Existing metric and key stamps,
headers, 16/17-column scored exports and three-column ratchets remain readable.
Unknown encoding versions and malformed encoded fields are refused. Read-only
ratchet salvage reports a complaint for each unreadable row.

## The portable baseline's first line

`verify --emit-baseline` writes one comment line before the scored export:

```text
# commit=<sha> run_kind=<kind> failures=<id>,<id>
```

`commit` is the baseline run's commit, the one the diff is measured from, and
`run_kind` is that run's kind. `failures` names the tests that run failed, sorted
and joined with commas, leaving out any that passed their flake retry in a verify
run. `failures=` with nothing after it says the run failed none. `verify
--baseline-tsv` forgives those tests the way a verify against the stored run does:
a failure the baseline already had does not count (README, exit 8).

Each id is percent-encoded as UTF-8, except ASCII letters, digits and `_.-~/:[]()`,
so a space, comma, `=` or `%` in a test name cannot split the line. Any RFC 3986
decoder reads it back, `urllib.parse.unquote` among them.

A file written by crapkit 0.8.0 or older has no `failures` field and cannot say
which tests its run failed. verify forgives none of them, as those versions did,
and when it reports a new failure against such a file it warns that the failure
may be one the baseline already had. Re-emit the file on the default branch.
Re-emitting through `--baseline-tsv` copies the field as it found it, so a missing
field stays missing. Readers skip stamp fields they do not know, so 0.8.0 reads a
newer file and ignores `failures`.

The file carries no lane test counts, so a verify against it cannot warn that a
lane runs fewer tests or skips more than the baseline did. A verify against the
stored run does.

## Reading exports in another tool

Split the file at LF, remove one trailing CR from each physical row, and recognize
the version marker before interpreting its columns. Decode the marked JSON array
as strings, then apply the file's column types. A generic `splitlines()` can split
Unicode separators inside a legacy filename and corrupt its identity. A plain
TSV reader cannot decode marked rows.

The writer encodes tabs, LF, CR, vertical tab, form feed, U+001C through U+001E,
U+0085, U+2028 and U+2029, plus a leading `#` in the first field. Legacy raw
backslashes remain literal; decoding `\\n` in an ordinary row would change the
filename. Encoding preserves fields, but it cannot make a filename valid on a
filesystem that rejects it.

Older Crapkit versions cannot read encoded rows. Upgrade readers before sharing
exports containing these filenames. JSON payloads keep `schema: 1`.
