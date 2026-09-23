"""Release readback requires the canonical published MCP server and package."""
import io
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
        if url == release.PAGES_LATEST:
            return json.dumps({"status": "built", "commit": "c0ffee1234567890"})
        if url == release.GLAMA_SERVER:
            return "README pin: uses: JeanFrancoisGagne/crapkit@v" + VERSION
        return json.dumps(pages[url])

    rows = release.verify(root, VERSION, fetch=fetch, git_tag=lambda: "v0.7.1",
                          tag_commit=lambda: "c0ffee1234567890",
                          contains=lambda ancestor, built: ancestor == built,
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


COLD_SEARCH_SECONDS = 87.1  # the slowest cold registry search measured on 2026-09-23


def cold_registry(url_of, answers):
    """urllib.request.urlopen for a network whose registry search answers after
    COLD_SEARCH_SECONDS: a read with a shorter timeout gets urllib's own error."""
    def urlopen(request, timeout):
        url = url_of(request)
        if url.startswith(SEARCH) and timeout < COLD_SEARCH_SECONDS:
            raise TimeoutError("The read operation timed out")
        body = next(text for prefix, text in answers.items() if url.startswith(prefix))
        return io.BytesIO(body.encode("utf-8"))
    return urlopen


def test_verify_waits_out_a_registry_search_whose_cache_is_cold(tmp_path, monkeypatch):
    """The registry search took 78 to 87 s on a cold cache, and verify read it
    once with a 20 s timeout, so every verify called a correct entry unconfirmed."""
    root = _tree(tmp_path, version=VERSION, heading="0.7.2")
    answers = {SEARCH: json.dumps({"servers": [entry()]}),
               "https://pypi.org/": json.dumps({"info": {"version": VERSION}}),
               release.PAGES_LATEST: json.dumps({"status": "built", "commit": "c0ffee1234567890"}),
               release.GLAMA_SERVER: "uses: JeanFrancoisGagne/crapkit@v" + VERSION}
    monkeypatch.setattr(release, "_gh_token", lambda: "")
    monkeypatch.setattr(release.urllib.request, "urlopen", cold_registry(lambda r: r.full_url, answers))

    rows = release.verify(root, VERSION, git_tag=lambda: "v0.7.1", tag_commit=lambda: "c0ffee1234567890",
                          contains=lambda ancestor, built: ancestor == built,
                          gh_release=lambda version: "https://github.com/r/releases/tag/v0.7.1")

    registry = [(row.surface, row.observed) for row in rows if row.surface.startswith("registry")]
    assert [surface for surface, _ in registry] == ["registry", "registry repository", "registry package"], registry
    assert all(row.ok for row in rows), rows
