"""Release readback requires the canonical published MCP server and package."""
import json

import pytest

from test_release_tool import _tree, release


SEARCH = "https://registry.modelcontextprotocol.io/v0/servers?search=crapkit"
VERSION = "0.7.1"


def entry():
    return {"server": {"name": "io.github.JeanFrancoisGagne/crapkit", "version": VERSION,
                       "repository": {"url": "https://github.com/JeanFrancoisGagne/crapkit"},
                       "packages": [{"registryType": "pypi", "identifier": "crapkit", "version": VERSION}]},
            "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}}}


def readback(tmp_path, pages):
    root = _tree(tmp_path, version=VERSION, heading="0.7.2")
    asked = []

    def fetch(url):
        asked.append(url)
        if url == "https://pypi.org/pypi/crapkit/0.7.1/json":
            return json.dumps({"info": {"version": VERSION}})
        return json.dumps(pages[url])

    rows = release.verify(root, VERSION, fetch=fetch, git_tag=lambda: "v0.7.1",
                          gh_release=lambda version: "https://github.com/JeanFrancoisGagne/crapkit/releases/tag/v0.7.1")
    return rows, asked


@pytest.mark.parametrize("field,value", [
    ("name", "io.github.someone-else/crapkit"),
    ("repository", {"url": "https://github.com/someone-else/crapkit"}),
    ("version", "0.7.0"),
    ("packages", [{"registryType": "pypi", "identifier": "other", "version": VERSION}]),
    ("packages", [{"registryType": "npm", "identifier": "crapkit", "version": VERSION}]),
    ("packages", [{"registryType": "pypi", "identifier": "crapkit", "version": "0.7.0"}]),
    ("packages", []),
])
def test_release_readback_refuses_wrong_registry_identity_or_package(tmp_path, field, value):
    candidate = entry()
    candidate["server"][field] = value
    rows, asked = readback(tmp_path, {SEARCH: {"servers": [candidate]}})
    failed = [row for row in rows if not row.ok]
    assert failed and all(row.surface.startswith("registry") for row in failed)


def test_release_readback_accepts_the_exact_registry_identity_and_package(tmp_path):
    rows, asked = readback(tmp_path, {SEARCH: {"servers": [entry()]}})
    assert all(row.ok for row in rows), rows


def test_release_readback_follows_encoded_cursor_past_other_projects(tmp_path):
    other = entry()
    other["server"].update(name="io.github.someone-else/crapkit", version="0.6.0")
    second = SEARCH + "&cursor=next%2B%2F%3D%3F"
    pages = {SEARCH: {"servers": [other], "metadata": {"nextCursor": "next+/=?"}},
             second: {"servers": [entry()]}}
    rows, asked = readback(tmp_path, pages)
    assert all(row.ok for row in rows), rows
    assert [url for url in asked if url.startswith(SEARCH)] == [SEARCH, second]


def test_release_readback_refuses_duplicate_latest_records_across_pages(tmp_path):
    second = SEARCH + "&cursor=next"
    pages = {SEARCH: {"servers": [entry()], "metadata": {"nextCursor": "next"}},
             second: {"servers": [entry()]}}
    rows, asked = readback(tmp_path, pages)
    assert any(not row.ok and row.surface == "registry" for row in rows)


def test_release_readback_refuses_a_cursor_cycle_after_a_matching_record(tmp_path):
    second = SEARCH + "&cursor=again"
    pages = {SEARCH: {"servers": [entry()], "metadata": {"nextCursor": "again"}},
             second: {"servers": [], "metadata": {"nextCursor": "again"}}}
    rows, asked = readback(tmp_path, pages)
    assert any(not row.ok and "cursor" in row.observed.lower() for row in rows)
    assert [url for url in asked if url.startswith(SEARCH)] == [SEARCH, second]


@pytest.mark.parametrize("page", [{}, {"servers": None}, {"servers": [{"server": None}]}])
def test_release_readback_refuses_malformed_registry_pages(tmp_path, page):
    rows, asked = readback(tmp_path, {SEARCH: page})
    assert any(not row.ok and row.surface == "registry" for row in rows)


def test_release_readback_requires_pagination_to_finish_within_its_bound(tmp_path):
    pages = {SEARCH: {"servers": [entry()], "metadata": {"nextCursor": "1"}}}
    for number in range(1, 100):
        pages[SEARCH + f"&cursor={number}"] = {"servers": [], "metadata": {"nextCursor": str(number + 1)}}
    rows, asked = readback(tmp_path, pages)
    assert any(not row.ok and "100 pages" in row.observed for row in rows)
    assert len([url for url in asked if url.startswith(SEARCH)]) == 100
