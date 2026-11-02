"""Tests for the viewer-event stream returned by `rr.spawn(on_event=...)`."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import TYPE_CHECKING, Any

import rerun as rr
from rerun._event import (
    EntitySelectionItem,
    PauseEvent,
    SelectionChangeEvent,
    ViewerEventStream,
)
from rerun._image_clicks import note_viewer_event, reset_image_clicks

import rerun_bindings  # noqa: TID251

if TYPE_CHECKING:
    import pytest
    from rerun._event import ViewerEvent


def _event(event_type: str) -> dict[str, Any]:
    return {
        "application_id": "test_app",
        "recording_id": "test_recording",
        "segment_id": None,
        "type": event_type,
    }


def _selection(x: float, y: float) -> dict[str, Any]:
    return {
        **_event("selection_change"),
        "items": [{"type": "entity", "entity_path": "/camera/rgb", "position": [x, y, 0.0]}],
    }


class _Recorder:
    """Collects callbacks and lets a test wait for a number of them."""

    def __init__(self) -> None:
        self.events: list[ViewerEvent] = []
        self._condition = threading.Condition()

    def __call__(self, event: ViewerEvent) -> None:
        with self._condition:
            self.events.append(event)
            self._condition.notify_all()

    def wait(self, count: int = 1, timeout: float = 5.0) -> list[ViewerEvent]:
        with self._condition:
            assert self._condition.wait_for(lambda: len(self.events) >= count, timeout), (
                f"only received {len(self.events)} of {count} events"
            )
        return self.events


def _send(stream: ViewerEventStream, *lines: dict[str, Any] | bytes) -> None:
    """Write newline-delimited events to the viewer side of the socket."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(os.fspath(stream.path))
        for line in lines:
            encoded = line if isinstance(line, bytes) else json.dumps(line).encode()
            client.sendall(encoded + b"\n")


def test_viewer_events_are_decoded_into_typed_objects() -> None:
    received = _Recorder()
    stream = ViewerEventStream(received)
    try:
        _send(stream, _event("pause"), _selection(12.7, 8.2))
        events = received.wait(2)
    finally:
        stream.close()

    assert isinstance(events[0], PauseEvent)
    assert isinstance(events[1], SelectionChangeEvent)
    assert events[1].items == [EntitySelectionItem(entity_path="/camera/rgb", position=[12.7, 8.2, 0.0])]


def test_malformed_lines_are_ignored() -> None:
    received = _Recorder()
    stream = ViewerEventStream(received)
    try:
        _send(
            stream,
            b"not json",
            b'{"type": "unknown"}',
            json.dumps(_event("pause")).encode(),
        )
        events = received.wait(1)
    finally:
        stream.close()

    assert len(events) == 1
    assert isinstance(events[0], PauseEvent)


def test_callback_errors_do_not_stop_the_stream() -> None:
    calls = []
    two_calls = threading.Event()

    def failing_callback(event: ViewerEvent) -> None:
        calls.append(event)
        if len(calls) == 2:
            two_calls.set()
        raise RuntimeError("callback failed")

    stream = ViewerEventStream(failing_callback)
    try:
        _send(stream, _event("pause"), _event("play"))
        assert two_calls.wait(5.0), f"only received {len(calls)} of 2 events"
    finally:
        stream.close()

    assert isinstance(calls[0], PauseEvent)


def test_close_is_idempotent_and_removes_the_socket() -> None:
    stream = ViewerEventStream(lambda _event: None)
    path = stream.path
    assert path.exists()

    stream.close()
    stream.close()

    assert not path.exists()


def test_spawn_returns_the_stream_and_injects_the_socket_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_spawn(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 123

    monkeypatch.setattr(rerun_bindings, "spawn", fake_spawn)
    monkeypatch.delenv("_RERUN_TEST_FORCE_SAVE", raising=False)

    rr.init("rerun_example_viewer_events_test", spawn=False)
    received = _Recorder()
    stream = rr.spawn(connect=False, on_event=received)
    assert isinstance(stream, ViewerEventStream)
    try:
        env = dict(captured["extra_env"])
        assert env["RERUN_APP_ONLY"] == "true"
        assert env["RERUN_EVENT_SOCKET"] == os.fspath(stream.path)

        _send(stream, _event("pause"))
        assert isinstance(received.wait(1)[0], PauseEvent)
    finally:
        stream.close()

    reset_image_clicks()
    assert rr.spawn(connect=False) is None
    from rerun._image_clicks import active_event_stream, close_listener

    listener = active_event_stream()
    assert listener is not None
    try:
        assert dict(captured["extra_env"])["RERUN_EVENT_SOCKET"] == os.fspath(listener.path)
        _send(listener, _selection(12.7, 8.2))
        deadline = time.monotonic() + 5
        while not rr.has_image_click("camera/rgb"):
            assert time.monotonic() < deadline, "image click was not recorded"
            time.sleep(0.01)
        assert rr.poll_image_click("camera/rgb") == (12, 8)
        assert rr.has_image_click("camera/rgb") is False
    finally:
        close_listener()


def test_poll_image_click_reads_the_latest_pixel() -> None:
    reset_image_clicks()
    note_viewer_event(
        SelectionChangeEvent(
            application_id="test_app",
            recording_id="test_recording",
            segment_id=None,
            items=[EntitySelectionItem(entity_path="/camera/rgb", position=[12.7, 8.2, 0.0])],
        )
    )
    note_viewer_event(
        SelectionChangeEvent(
            application_id="test_app",
            recording_id="test_recording",
            segment_id=None,
            items=[EntitySelectionItem(entity_path="camera/rgb", position=[3.2, 4.9, 0.0])],
        )
    )
    assert rr.has_image_click("/camera/rgb", width=640, height=480) is True
    assert rr.poll_image_click("camera/rgb", width=640, height=480) == (3, 4)
    assert rr.poll_image_click("camera/rgb", width=640, height=480) is None


def test_poll_image_click_uses_instance_id_and_drops_other_entities() -> None:
    reset_image_clicks()
    note_viewer_event(
        SelectionChangeEvent(
            application_id="test_app",
            recording_id="test_recording",
            segment_id=None,
            items=[
                EntitySelectionItem(entity_path="/world/mesh", position=[1.0, 2.0, 0.0]),
                EntitySelectionItem(entity_path="/camera/rgb", instance_id=640 * 3 + 5),
            ],
        )
    )
    assert rr.poll_image_click("camera/rgb", width=640, height=480) == (5, 3)
    assert rr.poll_image_click("/world/mesh", width=8, height=8) == (1, 2)


def test_poll_image_click_rejects_positions_outside_the_image() -> None:
    reset_image_clicks()
    note_viewer_event(
        SelectionChangeEvent(
            application_id="test_app",
            recording_id="test_recording",
            segment_id=None,
            items=[EntitySelectionItem(entity_path="camera/rgb", position=[640.0, 0.0, 0.0])],
        )
    )
    assert rr.has_image_click("camera/rgb", width=640, height=480) is False
    assert rr.poll_image_click("camera/rgb", width=640, height=480) is None
