"""Unit tests for the upstream-manifest verifier.

Network is never hit — the public ``fetcher`` parameter is mocked. The cache
write path is exercised in isolation by pointing at a tmp dir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from cc_plugin_manager.data import Marketplace
from cc_plugin_manager.manifest_verifier import (
    UpstreamManifest,
    _parse_manifest_payload,
    fetch_manifest_cached,
    github_raw_manifest_url,
    verify_marketplaces,
)


# --------------------------------------------------------- URL normalization


@pytest.mark.parametrize(
    "source,expected",
    [
        ("forrestchang/andrej-karpathy-skills", True),
        ("https://github.com/foo/bar", True),
        ("https://github.com/foo/bar.git", True),
        ("https://github.com/foo/bar/", True),
        ("HTTPS://GITHUB.COM/foo/bar", True),
        ("/local/path", False),
        ("foo", False),  # no slash
        ("", False),
        ("a/b/c", False),  # too many parts
        ("https://gitlab.com/foo/bar", False),
        ("https://example.com/owner/repo", False),
        # SSH-style URLs must be rejected — they're not auto-fetchable via
        # raw.githubusercontent.com and would otherwise produce a garbage
        # URL that 404s silently (audit M-3).
        ("git@github.com:owner/repo.git", False),
        ("ssh://git@github.com/owner/repo", False),
        # Pathological owner/repo names with disallowed characters.
        ("owner/repo with spaces", False),
        ("../../etc/passwd", False),
    ],
)
def test_github_raw_manifest_url_acceptance(source, expected):
    url = github_raw_manifest_url(source)
    if expected:
        assert url is not None
        assert url.startswith("https://raw.githubusercontent.com/")
        assert ".claude-plugin/marketplace.json" in url
        # The ".git" suffix on the source must be stripped from the resulting
        # path component (the hostname legitimately contains "github" so we
        # check the path segment specifically).
        path = url[len("https://raw.githubusercontent.com/") :]
        assert ".git/" not in path
    else:
        assert url is None


def test_github_raw_manifest_url_resolves_to_HEAD():
    url = github_raw_manifest_url("forrestchang/andrej-karpathy-skills")
    assert url == (
        "https://raw.githubusercontent.com/"
        "forrestchang/andrej-karpathy-skills/HEAD/.claude-plugin/marketplace.json"
    )


# --------------------------------------------------------- Manifest parsing


def test_parse_manifest_payload_happy_path():
    payload = """{
        "name": "karpathy-skills",
        "id": "karpathy-skills",
        "plugins": [
            {"name": "andrej-karpathy-skills"},
            {"name": "other-plugin"}
        ]
    }"""
    m = _parse_manifest_payload("forrestchang/andrej-karpathy-skills", payload)
    assert m is not None
    assert m.source == "forrestchang/andrej-karpathy-skills"
    assert m.name == "karpathy-skills"
    assert m.plugin_names == ("andrej-karpathy-skills", "other-plugin")


def test_parse_manifest_payload_strips_whitespace_in_names():
    payload = '{"name": "  m  ", "plugins": [{"name": "  p  "}]}'
    m = _parse_manifest_payload("o/r", payload)
    assert m is not None
    assert m.name == "m"
    assert m.plugin_names == ("p",)


def test_parse_manifest_payload_rejects_bad_json():
    assert _parse_manifest_payload("o/r", "not json") is None


def test_parse_manifest_payload_rejects_non_object_root():
    assert _parse_manifest_payload("o/r", "[]") is None


def test_parse_manifest_payload_rejects_missing_name():
    assert _parse_manifest_payload("o/r", '{"plugins": []}') is None


def test_parse_manifest_payload_tolerates_missing_plugins():
    m = _parse_manifest_payload("o/r", '{"name": "x"}')
    assert m is not None
    assert m.plugin_names == ()


def test_parse_manifest_payload_skips_malformed_plugin_entries():
    payload = '{"name": "x", "plugins": [{"name": "ok"}, "string", {}, {"name": ""}]}'
    m = _parse_manifest_payload("o/r", payload)
    assert m is not None
    assert m.plugin_names == ("ok",)


# --------------------------------------------------------- verify_marketplaces


def test_verify_marketplaces_skips_marketplaces_without_source():
    declared = [
        Marketplace(name="builtin", source=None),
        Marketplace(name="external", source="o/r"),
    ]
    fetcher_calls: list[str] = []

    def fake_fetch(source: str) -> Optional[UpstreamManifest]:
        fetcher_calls.append(source)
        return UpstreamManifest(source=source, name="external", plugin_names=("p",))

    results = verify_marketplaces(declared, fetcher=fake_fetch)
    assert fetcher_calls == ["o/r"]
    assert len(results) == 1
    assert results[0].matches is True


def test_verify_marketplaces_flags_name_mismatch():
    declared = [Marketplace(name="we-called-it-foo", source="o/r")]

    def fake_fetch(source: str) -> Optional[UpstreamManifest]:
        return UpstreamManifest(source=source, name="upstream-says-bar", plugin_names=())

    results = verify_marketplaces(declared, fetcher=fake_fetch)
    assert len(results) == 1
    r = results[0]
    assert r.matches is False
    assert r.declared_name == "we-called-it-foo"
    assert r.canonical_name == "upstream-says-bar"
    assert r.error is None


def test_verify_marketplaces_records_fetch_failure():
    declared = [Marketplace(name="foo", source="some-non-github-source")]

    def fake_fetch(_source: str) -> Optional[UpstreamManifest]:
        return None  # auto-verify not possible

    results = verify_marketplaces(declared, fetcher=fake_fetch)
    assert len(results) == 1
    r = results[0]
    assert r.matches is False
    assert r.canonical_name is None
    assert r.error == "could not fetch upstream manifest"


# --------------------------------------------------------- Cache


def test_fetch_manifest_cached_hits_cache_within_ttl(tmp_path, monkeypatch):
    """Second call returns the cached manifest without invoking the fetcher."""
    monkeypatch.setattr("cc_plugin_manager.manifest_verifier.cache_root", lambda: tmp_path)

    call_count = {"n": 0}

    def fake_fetch(source: str) -> Optional[UpstreamManifest]:
        call_count["n"] += 1
        return UpstreamManifest(source=source, name="m", plugin_names=("p",))

    a = fetch_manifest_cached("o/r", fetcher=fake_fetch)
    b = fetch_manifest_cached("o/r", fetcher=fake_fetch)
    assert a is not None and b is not None
    assert a == b
    assert call_count["n"] == 1  # second call is a cache hit


def test_fetch_manifest_cached_skips_stale_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("cc_plugin_manager.manifest_verifier.cache_root", lambda: tmp_path)

    versions = ["v1", "v2"]

    def fake_fetch(source: str) -> Optional[UpstreamManifest]:
        return UpstreamManifest(source=source, name=versions.pop(0), plugin_names=())

    a = fetch_manifest_cached("o/r", ttl=0.0, fetcher=fake_fetch)  # ttl=0 forces refetch
    b = fetch_manifest_cached("o/r", ttl=0.0, fetcher=fake_fetch)
    assert a is not None and b is not None
    assert a.name == "v1"
    assert b.name == "v2"


def test_fetch_manifest_cached_returns_none_when_fetcher_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("cc_plugin_manager.manifest_verifier.cache_root", lambda: tmp_path)

    def fake_fetch(_source: str) -> Optional[UpstreamManifest]:
        return None

    assert fetch_manifest_cached("o/r", fetcher=fake_fetch) is None
    # Nothing was cached, so a follow-up call still hits the fetcher.
    cache_files = list(Path(tmp_path).rglob("*.json"))
    assert cache_files == []
