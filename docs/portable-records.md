# Portable records

Inventory exports, scored exports, portable baselines and ratchets use the same
row encoding. Ordinary rows keep their existing tab-separated bytes and column
counts. Fields preserve literal backslashes; readers never treat them as escapes.

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

Older Crapkit versions cannot read encoded rows. Upgrade readers before sharing
exports containing these filenames. JSON payloads keep `schema: 1`.
