"""Latest image click from a viewer spawned by this process.

`rerun.spawn` listens for viewer events and records the latest click on each
entity. Scripts read that with `rerun.poll_image_click` instead of opening a
socket or decoding selection events themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from threading import Lock
from typing import TYPE_CHECKING

from ._event import EntitySelectionItem, SelectionChangeEvent, ViewerEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._event import ViewerEventStream


def _key(entity_path: str) -> str:
    return entity_path.lstrip("/")


@dataclass
class _Pick:
    position: list[float] | None
    instance_id: int | None


def _pixel(pick: _Pick, width: int | None, height: int | None) -> tuple[int, int] | None:
    """Integer pixel for one pick, or None when it cannot be placed on the image."""
    if pick.position is not None and len(pick.position) >= 2:
        x, y = floor(pick.position[0]), floor(pick.position[1])
        if width is None and height is None:
            return x, y
        if width is not None and height is not None and 0 <= x < width and 0 <= y < height:
            return x, y
        return None
    instance = pick.instance_id
    if (
        instance is not None
        and width is not None
        and height is not None
        and 0 <= instance < width * height
    ):
        return instance % width, instance // width
    return None


class _ImageClicks:
    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: dict[str, _Pick] = {}

    def note(self, event: ViewerEvent) -> None:
        if not isinstance(event, SelectionChangeEvent):
            return
        updates: dict[str, _Pick] = {}
        for item in event.items:
            if not isinstance(item, EntitySelectionItem):
                continue
            updates[_key(item.entity_path)] = _Pick(item.position, item.instance_id)
        if not updates:
            return
        with self._lock:
            self._pending.update(updates)

    def poll(
        self,
        entity_path: str,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> tuple[int, int] | None:
        key = _key(entity_path)
        with self._lock:
            pick = self._pending.pop(key, None)
        if pick is None:
            return None
        return _pixel(pick, width, height)

    def has_pending(
        self,
        entity_path: str,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> bool:
        key = _key(entity_path)
        with self._lock:
            pick = self._pending.get(key)
            if pick is None:
                return False
            if _pixel(pick, width, height) is None:
                self._pending.pop(key, None)
                return False
            return True

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()


_clicks = _ImageClicks()
_listener: ViewerEventStream | None = None


def note_viewer_event(event: ViewerEvent) -> None:
    """Record an image click from one decoded viewer event, if it contains one."""
    _clicks.note(event)


def reset_image_clicks() -> None:
    """Drop recorded clicks. Tests use this to isolate cases."""
    _clicks.clear()


def listen(on_event: Callable[[ViewerEvent], None]) -> ViewerEventStream:
    """Replace the process-wide viewer-event listener."""
    global _listener

    from ._event import ViewerEventStream

    if _listener is not None:
        _listener.close()
    _listener = ViewerEventStream(on_event)
    return _listener


def close_listener() -> None:
    """Stop the process-wide viewer-event listener."""
    global _listener

    if _listener is not None:
        _listener.close()
        _listener = None


def active_event_stream() -> ViewerEventStream | None:
    """The listener `rerun.spawn` opened, if any."""
    return _listener


def poll_image_click(
    entity_path: str,
    *,
    width: int | None = None,
    height: int | None = None,
) -> tuple[int, int] | None:
    """Latest click on `entity_path` since the previous call, as integer pixels.

    `rerun.spawn` and `rerun.init(..., spawn=True)` record clicks automatically.
    The coordinates are the 2D position of that entity in the view, floored to
    a pixel. A newer click replaces one that has not been polled yet.

    Pass `width` and `height` to drop clicks outside the image. When the viewer
    reports only an instance id, that id is decoded as `y * width + x`.

    Parameters
    ----------
    entity_path:
        Entity that was clicked, for example `camera/rgb`. A leading slash is
        ignored.
    width:
        Image width in pixels. Set together with `height`.
    height:
        Image height in pixels. Set together with `width`.

    Returns
    -------
    `(x, y)` or `None` when no click is waiting.

    """
    return _clicks.poll(entity_path, width=width, height=height)


def has_image_click(
    entity_path: str,
    *,
    width: int | None = None,
    height: int | None = None,
) -> bool:
    """Whether `poll_image_click` would return a click for `entity_path`."""
    return _clicks.has_pending(entity_path, width=width, height=height)
