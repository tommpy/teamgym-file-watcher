"""Thread-safe container for the latest sorted tag snapshot for one competition.

The watcher fetches ALL tags for the configured competition every ``tag_refresh_seconds``
(PLAN.md's batching strategy) instead of querying the API per photo. :class:`TagCache` holds
that snapshot and swaps it atomically so a background refresher thread can update it while the
watcher thread reads it concurrently. Matching itself lives in ``matcher.py`` (step 07) — this
module is intentionally a dumb container.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from teamgym_watcher.api import Tag


class _TagFetcher(Protocol):
    """Structural type for what :meth:`TagCache.refresh` needs from an API client.

    Matched by :class:`teamgym_watcher.api.ApiClient` without a direct dependency on it, so
    tests can pass a lightweight stand-in instead of a real HTTP client.
    """

    def fetch_all_tags(self, competition_id: int) -> list[Tag]: ...


class TagCache:
    """Holds the latest sorted tag snapshot, refreshed periodically from the API.

    ``clock`` is injectable so tests can control staleness without real sleeps.
    """

    def __init__(
        self,
        competition_id: int,
        *,
        tag_refresh_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._competition_id = competition_id
        self._tag_refresh_seconds = tag_refresh_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._tags: list[Tag] = []
        self._last_refreshed: float | None = None

    @property
    def tags_sorted(self) -> list[Tag]:
        """A copy of the current sorted tag snapshot. Safe to call from any thread."""
        with self._lock:
            return list(self._tags)

    @property
    def competition_name(self) -> str | None:
        """Display name of the watched competition, from the first cached tag that has one.

        ``None`` until the cache holds at least one tag with a name -- callers deriving a
        hierarchy path (which needs a competition name unconditionally, even for unmatched
        photos) should fall back to a placeholder in that case.
        """
        with self._lock:
            for tag in self._tags:
                if tag.competition.name is not None:
                    return tag.competition.name
            return None

    def refresh(self, client: _TagFetcher) -> None:
        """Fetch all tags for the competition and atomically swap them into the cache.

        The fetch happens outside the lock — only the swap is locked — so a slow API call
        never blocks concurrent readers of :attr:`tags_sorted`.
        """
        tags = client.fetch_all_tags(self._competition_id)
        with self._lock:
            self._tags = tags
            self._last_refreshed = self._clock()

    def stale(self, now: float) -> bool:
        """True if never refreshed, or ``tag_refresh_seconds`` have elapsed since the last."""
        if self._last_refreshed is None:
            return True
        return (now - self._last_refreshed) >= self._tag_refresh_seconds
