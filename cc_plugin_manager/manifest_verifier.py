"""Best-effort fetch of upstream marketplace manifests.

Why this module exists:

The CLI registers a marketplace under whatever ``name`` is declared in the
upstream repo's ``.claude-plugin/marketplace.json`` — NOT the name we wrote
in ``plugins.json``. A mismatch leaves every plugin in that marketplace as
``MARKETPLACE_MISSING`` forever, with no obvious diagnostic. Manually opening
each repo's manifest to verify the name is the kind of toil that gets
skipped, so we automate it.

Design decisions:

- **Stdlib only.** ``urllib.request`` is enough; ``requests`` would bloat
  the PyInstaller bundle for ~50 lines.
- **Best-effort.** A failed fetch returns ``None``; callers degrade
  gracefully. We never block app launch on a network call.
- **Cached.** Results land in a per-user cache directory with a 24h TTL,
  so a "Verify" command becomes free on subsequent runs.
- **GitHub-only auto-resolution.** We construct a raw-content URL from
  ``owner/repo`` or ``https://github.com/owner/repo[.git]``. Other source
  formats (local paths, GitLab, custom URLs) return ``None`` — those
  marketplaces simply skip auto-verification.

This module is loader-free and UI-free: it can be unit-tested without a wx
app and without network access (the fetcher is overridable).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .data import Marketplace


_CACHE_TTL_SECONDS = 24 * 60 * 60
_USER_AGENT = "cc-plugin-manager/1.0 (+https://github.com/akashkakkar/cc-plugin-manager)"


@dataclass(frozen=True)
class UpstreamManifest:
    """Parsed, validated upstream marketplace manifest."""

    source: str
    name: str
    plugin_names: tuple[str, ...]


@dataclass(frozen=True)
class VerifierResult:
    """One verification attempt against a declared marketplace."""

    source: str
    declared_name: str
    canonical_name: Optional[str]
    plugin_names: tuple[str, ...]
    error: Optional[str] = None

    @property
    def matches(self) -> bool:
        return self.canonical_name is not None and self.canonical_name == self.declared_name


# --------------------------------------------------------- URL helpers

_GITHUB_PREFIX_RE = re.compile(r"^https?://github\.com/", re.IGNORECASE)


_GH_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def github_raw_manifest_url(source: str) -> Optional[str]:
    """Map a source string to its raw GitHub manifest URL, or ``None``.

    Accepts ``owner/repo`` and ``https://github.com/owner/repo[.git]``.
    Anything else (local paths, non-GitHub URLs, SSH-style ``git@github.com:owner/repo.git``)
    → ``None``. owner/repo segments are also character-validated so a
    pathological string can't sneak in (audit M-3).
    """
    s = source.strip().rstrip("/")
    if not s:
        return None
    if _GITHUB_PREFIX_RE.match(s):
        path = _GITHUB_PREFIX_RE.sub("", s)
    elif "://" in s:
        return None  # non-GitHub URL, can't auto-resolve
    elif "@" in s or ":" in s:
        return None  # SSH-style (git@host:path) — not a valid owner/repo form
    elif s.count("/") == 1 and not s.startswith("/"):
        path = s
    else:
        return None
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    owner, repo = parts
    if not _GH_NAME_RE.match(owner) or not _GH_NAME_RE.match(repo):
        return None
    return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/.claude-plugin/marketplace.json"


# --------------------------------------------------------- Fetcher


def fetch_manifest_http(source: str, *, timeout: float = 5.0) -> Optional[UpstreamManifest]:
    """Fetch and parse a marketplace manifest from upstream. Returns ``None`` on any error.

    Network failures, JSON errors, non-GitHub sources, missing fields — all
    map to ``None``. Callers should treat ``None`` as "auto-verification not
    possible" rather than as a hard error.
    """
    url = github_raw_manifest_url(source)
    if url is None:
        return None
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed scheme
            # Cap the read so a misconfigured/malicious source streaming a
            # huge body can't blow up RAM (audit M-7). 2 MiB is generous —
            # real manifests are a few KB at most.
            payload = resp.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
    # urllib can raise ValueError for malformed URLs after construction —
    # broaden the catch (audit I-7) so one bad source can't kill verification
    # for the rest of the marketplaces.
    except (URLError, OSError, TimeoutError, ValueError):
        return None
    return _parse_manifest_payload(source, payload)


def _parse_manifest_payload(source: str, payload: str) -> Optional[UpstreamManifest]:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    plugin_names: list[str] = []
    raw_plugins = data.get("plugins")
    if isinstance(raw_plugins, list):
        for entry in raw_plugins:
            if isinstance(entry, dict):
                pname = entry.get("name")
                if isinstance(pname, str) and pname.strip():
                    plugin_names.append(pname.strip())
    return UpstreamManifest(
        source=source,
        name=name.strip(),
        plugin_names=tuple(plugin_names),
    )


# --------------------------------------------------------- Cache


def cache_root() -> Path:
    """Where this app stores per-user cache (manifests, etc.)."""
    if os.name == "nt":
        base = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or str(Path.home() / "AppData" / "Local")
        )
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "cc-plugin-manager"


def _cache_path_for(source: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", source)
    return cache_root() / "manifests" / f"{safe}.json"


def _read_cache(source: str, ttl: float) -> Optional[UpstreamManifest]:
    p = _cache_path_for(source)
    try:
        st = p.stat()
    except OSError:
        return None
    if time.time() - st.st_mtime >= ttl:
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return UpstreamManifest(
            source=str(data["source"]),
            name=str(data["name"]),
            plugin_names=tuple(str(x) for x in data["plugin_names"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _write_cache(manifest: UpstreamManifest) -> None:
    p = _cache_path_for(manifest.source)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "source": manifest.source,
                    "name": manifest.name,
                    "plugin_names": list(manifest.plugin_names),
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def fetch_manifest_cached(
    source: str,
    *,
    ttl: float = _CACHE_TTL_SECONDS,
    timeout: float = 5.0,
    fetcher: Optional[Callable[[str], Optional[UpstreamManifest]]] = None,
) -> Optional[UpstreamManifest]:
    """Read from cache if fresh; otherwise fetch and cache the result.

    ``fetcher`` is overridable for tests so we never hit the network in CI.
    """
    cached = _read_cache(source, ttl)
    if cached is not None:
        return cached
    fetch = fetcher or (lambda s: fetch_manifest_http(s, timeout=timeout))
    fresh = fetch(source)
    if fresh is not None:
        _write_cache(fresh)
    return fresh


# --------------------------------------------------------- Verification


def verify_marketplaces(
    marketplaces: Iterable[Marketplace],
    *,
    fetcher: Optional[Callable[[str], Optional[UpstreamManifest]]] = None,
) -> list[VerifierResult]:
    """Compare each declared marketplace's name against its upstream manifest.

    Marketplaces without a ``source`` (built-ins) are skipped. The returned
    list contains one entry per attempted verification.

    ``fetcher`` is overridable for tests; the default uses the on-disk cache
    backed by HTTP.
    """
    fetch = fetcher or (lambda s: fetch_manifest_cached(s))
    results: list[VerifierResult] = []
    for m in marketplaces:
        if not m.source:
            continue
        manifest = fetch(m.source)
        if manifest is None:
            results.append(
                VerifierResult(
                    source=m.source,
                    declared_name=m.name,
                    canonical_name=None,
                    plugin_names=(),
                    error="could not fetch upstream manifest",
                )
            )
        else:
            results.append(
                VerifierResult(
                    source=m.source,
                    declared_name=m.name,
                    canonical_name=manifest.name,
                    plugin_names=manifest.plugin_names,
                )
            )
    return results
