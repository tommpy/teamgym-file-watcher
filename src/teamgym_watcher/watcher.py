"""Filesystem watching: a watchdog ``Observer`` feeding a thread-safe, stability-gated queue.

No Lua equivalent (like ``mover.py``) -- the plugin never watches a directory. Behaviour is
driven by PLAN.md's "Unattended operation" section and prompts/09.

Two independent pieces, kept separately testable per prompts/09 ("keep watchdog itself out
of unit tests; test the queue/stability logic directly"):

- :class:`StabilityQueue` -- a dedup-by-path queue with a size-stability gate. A file is only
  "ready" once its size has been observed unchanged across ``stability_seconds``; anything
  else stays tracked (effectively re-queued) until it stops changing or disappears.
- :class:`Watcher` -- owns one :class:`StabilityQueue` plus the real-time watchdog
  ``Observer`` and the periodic full-directory rescan sweep (belt-and-braces fallback, and
  required on network shares whose events aren't always delivered). :meth:`Watcher.rescan`
  and :meth:`Watcher.poll_ready` work without ever starting the real ``Observer``, so tests
  can exercise the discovery/stability logic directly; :meth:`Watcher.start` wires up the
  real OS-level watching for the CLI.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from teamgym_watcher.mover import JPEG_EXTENSIONS, RAW_EXTENSIONS, SIDECAR_EXTENSIONS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from watchdog.observers.api import BaseObserver

logger = logging.getLogger(__name__)

PHOTO_EXTENSIONS: Final[frozenset[str]] = RAW_EXTENSIONS | JPEG_EXTENSIONS | SIDECAR_EXTENSIONS
_HIDDEN_PREFIXES: Final = (".", "~")
_TEMP_SUFFIX: Final = ".tmp"

DEFAULT_RESCAN_INTERVAL_SECONDS: Final = 30


def is_watchable_file(path: Path) -> bool:
    """True if ``path`` should be tracked: a known photo/sidecar extension, and not
    hidden/temp (leading ``.``/``~``, or a ``.tmp`` suffix -- in-progress card-writer or
    editor artefacts)."""
    name = path.name
    if name.startswith(_HIDDEN_PREFIXES):
        return False
    suffix = path.suffix.lower()
    if suffix == _TEMP_SUFFIX:
        return False
    return suffix in PHOTO_EXTENSIONS


def scan_directory(directory: Path, *, recursive: bool) -> list[Path]:
    """List every watchable file directly (or recursively) under ``directory``."""
    pattern = "**/*" if recursive else "*"
    return [p for p in directory.glob(pattern) if p.is_file() and is_watchable_file(p)]


@dataclass(slots=True)
class _TrackedFile:
    size: int
    since: float


class StabilityQueue:
    """Thread-safe dedup-by-path queue with a size-stability gate.

    A path is tracked from the moment it's :meth:`enqueue`\\ d; :meth:`poll_ready` is called
    periodically and only returns (and stops tracking) paths whose size has been unchanged
    for ``stability_seconds`` -- a slow card-writer still appending to a file is simply left
    tracked until it stops changing, which satisfies the "unstable files are re-queued"
    requirement without any explicit re-queue step.
    """

    def __init__(
        self,
        *,
        stability_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        size_of: Callable[[Path], int] | None = None,
    ) -> None:
        self._stability_seconds = stability_seconds
        self._clock = clock
        self._size_of = size_of if size_of is not None else (lambda p: p.stat().st_size)
        self._lock = threading.Lock()
        self._tracked: dict[Path, _TrackedFile | None] = {}

    def enqueue(self, path: Path) -> None:
        """Start tracking ``path``. A no-op if it is already tracked (dedup by path)."""
        with self._lock:
            self._tracked.setdefault(path, None)

    def enqueue_many(self, paths: Iterable[Path]) -> None:
        for path in paths:
            self.enqueue(path)

    def __len__(self) -> int:
        with self._lock:
            return len(self._tracked)

    def poll_ready(self) -> list[Path]:
        """Check every tracked path; return those stable for >= ``stability_seconds``.

        A path that has disappeared (moved or deleted since being tracked) is dropped
        silently -- it is no longer this queue's concern. A path whose size changed since
        the last check has its stability timer reset.
        """
        with self._lock:
            paths = list(self._tracked.keys())

        ready: list[Path] = []
        for path in paths:
            try:
                size = self._size_of(path)
            except OSError:
                with self._lock:
                    self._tracked.pop(path, None)
                continue

            now = self._clock()
            with self._lock:
                if path not in self._tracked:
                    continue  # concurrently removed by another poll_ready call
                previous = self._tracked[path]
                if previous is None or previous.size != size:
                    self._tracked[path] = _TrackedFile(size=size, since=now)
                    continue
                if now - previous.since >= self._stability_seconds:
                    ready.append(path)
                    del self._tracked[path]

        return ready


class _EventHandler(FileSystemEventHandler):
    """Forwards watchable file-creation/modification/move events into a :class:`StabilityQueue`."""

    def __init__(self, queue: StabilityQueue) -> None:
        self._queue = queue

    def _consider(self, raw_path: bytes | str) -> None:
        path = Path(os.fsdecode(raw_path))
        if is_watchable_file(path):
            self._queue.enqueue(path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._consider(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._consider(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._consider(event.dest_path)


class Watcher:
    """Owns the stability queue, the watchdog ``Observer``, and the periodic rescan sweep for
    one ``import_dir``. The ``Observer`` is a thin real-time producer; the rescan sweep is the
    belt-and-braces fallback that guarantees anything unjournaled eventually gets enqueued
    even if the ``Observer`` was down (or missed an event) when it arrived.
    """

    def __init__(
        self,
        import_dir: Path,
        *,
        recursive: bool = False,
        stability_seconds: int,
        rescan_interval_seconds: int = DEFAULT_RESCAN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._import_dir = import_dir
        self._recursive = recursive
        self._rescan_interval_seconds = rescan_interval_seconds
        self.queue = StabilityQueue(stability_seconds=stability_seconds, clock=clock)
        self._observer: BaseObserver | None = None
        self._rescan_thread: threading.Thread | None = None
        self._rescan_stop: threading.Event | None = None

    def rescan(self, already_processed: set[str]) -> int:
        """Scan ``import_dir`` once and enqueue any watchable file not already journaled.

        ``already_processed`` holds full source paths (as journaled), not filenames -- two
        different photos that happen to share a filename must each still be enqueued.
        Returns the number of newly tracked paths (already-tracked paths are a dedup no-op).
        """
        found = 0
        for path in scan_directory(self._import_dir, recursive=self._recursive):
            if str(path) in already_processed:
                continue
            before = len(self.queue)
            self.queue.enqueue(path)
            if len(self.queue) > before:
                found += 1
        return found

    def poll_ready(self) -> list[Path]:
        return self.queue.poll_ready()

    def start(self, already_processed: Callable[[], set[str]]) -> None:
        """Start the real-time ``Observer`` and the periodic rescan thread.

        ``already_processed`` is called fresh on every rescan tick so newly journaled names
        are respected. Not exercised by unit tests (integration covers it) -- see the module
        docstring.
        """
        handler = _EventHandler(self.queue)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._import_dir), recursive=self._recursive)
        self._observer.start()

        rescan_stop = threading.Event()
        self._rescan_stop = rescan_stop

        def _loop() -> None:
            while not rescan_stop.wait(self._rescan_interval_seconds):
                try:
                    self.rescan(already_processed())
                except OSError as exc:
                    logger.warning("Rescan of %s failed: %s", self._import_dir, exc)

        self._rescan_thread = threading.Thread(
            target=_loop, name="teamgym-watcher-rescan", daemon=True
        )
        self._rescan_thread.start()

    def stop(self) -> None:
        if self._rescan_stop is not None:
            self._rescan_stop.set()
        if self._rescan_thread is not None:
            self._rescan_thread.join(timeout=5)
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
