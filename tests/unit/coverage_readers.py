"""Text fixtures sent through the public file readers, never a second implementation."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from crapkit import covstream


def _read(text, reader, **kwargs):
    with TemporaryDirectory() as directory:
        path = Path(directory) / 'coverage.json'
        path.write_bytes(text.encode('utf-8'))
        return reader(path, **kwargs)


def parse_istanbul(text, **kwargs):
    return _read(text, covstream.parse_istanbul_file, **kwargs)[0]


def parse_istanbul_missing(text, **kwargs):
    return _read(text, covstream.parse_istanbul_missing_file, **kwargs)


def parse_coveragepy(text, **kwargs):
    return _read(text, covstream.parse_coveragepy_file, **kwargs)[0]


def parse_coveragepy_missing(text, **kwargs):
    return _read(text, covstream.parse_coveragepy_missing_file, **kwargs)


def parse_coveragepy_contexts(text, *, path_prefix):
    prefix = covstream.lane_prefix(path_prefix)
    results = {}
    for raw in json.loads(text).get('files', {}):
        source_path = prefix + raw.replace('\\', '/')
        contexts = _read(text, covstream.parse_coveragepy_contexts_file,
                         path_prefix=path_prefix, source_path=source_path)
        if contexts:
            results[source_path] = contexts
    return results


def split_top_level(text):
    return covstream.split_window(covstream._Window(BytesIO(text.encode('utf-8'))))
