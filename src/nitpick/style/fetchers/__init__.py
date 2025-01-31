"""Style fetchers with protocol support."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import auto
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple
from urllib.parse import urlparse, uses_netloc, uses_relative

from requests_cache import CachedSession
from strenum import LowercaseStrEnum

from nitpick.enums import CachingEnum
from nitpick.generic import is_url
from nitpick.style import parse_cache_option

if TYPE_CHECKING:
    from nitpick.style.fetchers.base import FetchersType

StyleInfo = Tuple[Optional[Path], str]


class Scheme(LowercaseStrEnum):
    """URL schemes."""

    HTTP = auto()
    HTTPS = auto()
    PY = auto()
    PYPACKAGE = auto()
    GH = auto()
    GITHUB = auto()


@dataclass(repr=True)
class StyleFetcherManager:
    """Manager that controls which fetcher to be used given a protocol."""

    offline: bool
    cache_dir: str
    cache_option: str

    session: CachedSession = field(init=False)
    fetchers: FetchersType = field(init=False)

    def __post_init__(self):
        """Initialize dependant properties."""
        caching, expire_after = parse_cache_option(self.cache_option)
        # honour caching headers on the response when an expiration time has
        # been set meaning that the server can dictate cache expiration
        # overriding the local expiration time. This may need to become a
        # separate configuration option in future.
        cache_control = caching is CachingEnum.EXPIRES
        self.session = CachedSession(self.cache_dir / "styles", expire_after=expire_after, cache_control=cache_control)
        self.fetchers = _get_fetchers(self.session)

    def fetch(self, url) -> StyleInfo:
        """Determine which fetcher to be used and fetch from it.

        Try a fetcher by domain first, then by protocol scheme.
        """
        domain, scheme = self._get_domain_scheme(url)
        fetcher = None
        if domain:
            fetcher = self.fetchers.get(domain)
        if not fetcher:
            fetcher = self.fetchers.get(scheme)
        if not fetcher:
            raise RuntimeError(f"URI protocol {scheme!r} is not supported")

        if self.offline and fetcher.requires_connection:
            return None, ""

        return fetcher.fetch(url)

    @staticmethod
    def _get_domain_scheme(url: str) -> tuple[str, str]:
        r"""Get domain and scheme from an URL or a file.

        >>> StyleFetcherManager._get_domain_scheme("/abc")
        ('', 'file')
        >>> StyleFetcherManager._get_domain_scheme("file:///abc")
        ('', 'file')
        >>> StyleFetcherManager._get_domain_scheme(r"c:\abc")
        ('', 'file')
        >>> StyleFetcherManager._get_domain_scheme("c:/abc")
        ('', 'file')
        >>> StyleFetcherManager._get_domain_scheme("http://server.com/abc")
        ('server.com', 'http')
        """
        if is_url(url):
            parsed_url = urlparse(url)
            return parsed_url.hostname or "", parsed_url.scheme
        return "", "file"


def _get_fetchers(session: CachedSession) -> FetchersType:
    # pylint: disable=import-outside-toplevel
    from nitpick.style.fetchers.base import StyleFetcher
    from nitpick.style.fetchers.file import FileFetcher
    from nitpick.style.fetchers.github import GitHubFetcher
    from nitpick.style.fetchers.http import HttpFetcher
    from nitpick.style.fetchers.pypackage import PythonPackageFetcher

    def _factory(klass: type[StyleFetcher]) -> StyleFetcher:
        return klass(session) if klass.requires_connection else klass()

    fetchers = (_factory(FileFetcher), _factory(HttpFetcher), _factory(GitHubFetcher), _factory(PythonPackageFetcher))
    pairs = _fetchers_to_pairs(fetchers)
    return dict(pairs)


def _fetchers_to_pairs(fetchers):
    for fetcher in fetchers:
        for protocol in fetcher.protocols:
            _register_on_urllib(protocol)
            yield protocol, fetcher
        for domain in fetcher.domains:
            yield domain, fetcher


@lru_cache()
def _register_on_urllib(protocol: str) -> None:
    """Register custom protocols on urllib, only once, if it's not already there.

    This is necessary so urljoin knows how to deal with custom protocols.
    """
    if protocol not in uses_relative:
        uses_relative.append(protocol)

    if protocol not in uses_netloc:
        uses_netloc.append(protocol)
