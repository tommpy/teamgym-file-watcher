"""Port of ``BulkPhotoTagger.lua``'s photo->tag matching: ``performPhotoTagMatching`` and
its ``isPhotoInTagTimeWindow`` window test.

One entry point, built on the window test:

- :func:`match_batch` — the sorted-merge scan for a drained batch of photos, a direct port
  of the Lua's step-4 algorithm: a persistent tag index that only ever advances forward as
  photos are processed in increasing capture-time order, giving O(photos + tags) total work.

Parity notes:

- The Lua parses tag timestamps from ISO 8601 strings to POSIX time for comparison
  (``isoToUnixTimestamp``). ``api.parse_tag`` already normalises ``Tag.start_time``/
  ``end_time`` to aware UTC :class:`~datetime.datetime` objects, so this port compares
  those directly instead of re-parsing strings.
- Window test: inclusive both ends; an open-ended tag (``end_time is None``) matches any
  photo at or after its start, exactly as ``isPhotoInTagTimeWindow`` does when
  ``tagEndTime`` is absent.
- First match wins; a photo matches at most one tag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from datetime import datetime

    from teamgym_watcher.api import Tag


def _in_window(capture_time: datetime, tag: Tag) -> bool:
    """Port of ``isPhotoInTagTimeWindow``'s comparison, given already-parsed timestamps."""
    if tag.end_time is None:
        return capture_time >= tag.start_time
    return tag.start_time <= capture_time <= tag.end_time


def match_batch[PhotoT](
    photos: Iterable[tuple[PhotoT, datetime]],
    tags_sorted: Sequence[Tag],
) -> Iterator[tuple[PhotoT, Tag | None]]:
    """Port of ``performPhotoTagMatching``'s sequential scan (step 4).

    ``photos`` need not be pre-sorted -- they are sorted internally by capture time,
    exactly as the Lua's ``table.sort(sortedPhotos, ...)`` does. ``tags_sorted`` MUST
    already be ascending by ``start_time``, as :meth:`ApiClient.fetch_tags_window` provides.
    Yields one ``(photo, matched tag or None)`` pair per input photo, in ascending
    capture-time order.
    """
    ordered = sorted(photos, key=lambda pair: pair[1])
    tag_index = 0
    tag_count = len(tags_sorted)

    for photo, capture_time in ordered:
        matched: Tag | None = None
        while tag_index < tag_count:
            tag = tags_sorted[tag_index]
            if _in_window(capture_time, tag):
                matched = tag
                break
            if capture_time < tag.start_time:
                # Photo is before this tag starts: no match possible yet. Photos are
                # processed in increasing time order, so no earlier tag will match a
                # later photo either -- stop here and leave the index for next time.
                break
            tag_index += 1
        yield photo, matched
