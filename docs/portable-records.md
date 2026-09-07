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
