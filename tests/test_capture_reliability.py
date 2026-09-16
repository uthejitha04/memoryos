import asyncio
import time

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omi_transcribe import AudioFrameAssembler, PcmStreamDecoder


def packet(counter, index, payload=b'ab'):
    return counter.to_bytes(2, 'little') + bytes([index]) + payload


class Frames(AudioFrameAssembler):
    def _decode(self, frame):
        return frame


def test_fragment_boundaries_wrap_and_duplicate():
    d = Frames()
    assert d.feed(packet(65534, 0, b'a')) is None
    assert d.feed(packet(65535, 1, b'b')) is None
    assert d.feed(packet(65535, 1, b'b')) is None
    assert d.feed(packet(0, 0, b'c')) == b'ab'
    assert d.feed(packet(1, 0, b'd')) == b'c'


def test_missing_fragment_discards_frame_and_recovers():
    d = Frames()
    d.feed(packet(0, 0, b'a'))
    d.feed(packet(2, 2, b'c'))
    assert d.feed(packet(3, 0, b'good')) is None
    assert d.feed(packet(4, 0)) == b'good'
    assert d.dropped_frames == 1


def test_missing_start_does_not_join_two_frames():
    d = Frames()
    d.feed(packet(0, 0, b'a'))
    assert d.feed(packet(2, 1, b'bad')) is None
    assert d.feed(packet(3, 0, b'good')) is None
    assert d.feed(packet(4, 0)) == b'good'


def test_pcm_odd_fragment_boundary_and_8k_rate():
    d = PcmStreamDecoder(8000)
    raw = np.array([1000, -1000], dtype='<i2').tobytes()
    d.feed(packet(0, 0, raw[:1]))
    d.feed(packet(1, 1, raw[1:]))
    result = d.feed(packet(2, 0))
    np.testing.assert_allclose(result, np.array([1000, 1000, -1000, -1000]) / 32768)


def test_second_socket_and_omi_cannot_take_recording(stub_hub):
    import server
    with TestClient(server.app) as c:
        with c.websocket_connect('/ws/audio') as first:
            owner = stub_hub.capture_owner
            server.omi_link.connected = True
            assert c.post('/omi/record/start').status_code == 409
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect('/ws/audio'):
                    pass
            assert stub_hub.capture_owner is owner
            first.send_bytes(bytes(3200))
        assert stub_hub.capture_owner is None


def test_invalid_audio_releases_capture(stub_hub):
    import server
    with TestClient(server.app) as c:
        with c.websocket_connect('/ws/audio') as ws:
            ws.send_bytes(b'x')
            with pytest.raises(WebSocketDisconnect) as err:
                ws.receive_bytes()
            assert err.value.code == 1003
        assert stub_hub.capture_owner is None


def test_idle_reconnect_does_not_reserve_capture_and_stop_cancels_it(stub_hub, monkeypatch):
    import server
    async def scenario():
        link = server.OmiLink(stub_hub)
        entered_retry = asyncio.Event()
        calls = []
        async def dropped(address):
            calls.append(address)
            entered_retry.set()
        monkeypatch.setattr(link, '_run', dropped)
        link.start(None)
        await entered_retry.wait()
        assert stub_hub.capture_owner is None
        assert not link.recording
        await link.stop()
        assert stub_hub.capture_owner is None
        assert stub_hub.omi_status == 'disconnected'
        assert len(calls) == 1
    asyncio.run(scenario())


def test_retry_budget_releases_capture(stub_hub, monkeypatch):
    import server
    async def scenario():
        link = server.OmiLink(stub_hub)
        calls = []
        async def dropped(address):
            calls.append(address)
        async def no_delay(seconds):
            pass
        monkeypatch.setattr(link, '_run', dropped)
        monkeypatch.setattr(server.asyncio, 'sleep', no_delay)
        link.start('fake-device')
        await link.task
        assert len(calls) == 6
        assert stub_hub.capture_owner is None
        assert 'retry' in stub_hub.omi_status
    asyncio.run(scenario())


def test_rapid_sessions_do_not_overwrite(stub_hub):
    first = stub_hub.start_session().name
    stub_hub.end_session()
    second = stub_hub.start_session().name
    stub_hub.end_session()
    assert first != second


def test_omi_start_stop_keeps_connection_and_creates_new_sessions(stub_hub):
    import server
    link = server.omi_link
    with TestClient(server.app) as c:
        assert c.post('/omi/record/start').status_code == 409
        link.connected = True
        assert stub_hub.session is None
        assert stub_hub.capture_owner is None
        assert c.post('/omi/record/start').status_code == 200
        stub_hub.feed_audio(np.zeros(1600, dtype=np.float32))
        first = stub_hub.session.name
        assert c.post('/omi/record/stop').status_code == 200
        assert link.connected and not link.recording
        assert stub_hub.session is None and stub_hub.capture_owner is None
        assert c.get('/status').json()['omi_connected'] is True
        assert c.post('/omi/record/start').status_code == 200
        stub_hub.feed_audio(np.zeros(1600, dtype=np.float32))
        assert stub_hub.session.name != first
        assert c.post('/omi/disconnect').status_code == 200
        assert not link.connected and not link.recording
        assert stub_hub.capture_owner is None


def test_ble_idle_audio_is_discarded_and_stop_preserves_link(stub_hub, monkeypatch):
    import sys
    import types
    import server

    async def scenario():
        ready = asyncio.Event()
        class Client:
            def __init__(self, *args, **kwargs):
                self.is_connected = True
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                self.is_connected = False
            async def read_gatt_char(self, uuid):
                return bytes([0])
            async def start_notify(self, uuid, callback):
                self.audio = callback
                ready.set()
        client = Client()
        monkeypatch.setitem(sys.modules, 'bleak', types.SimpleNamespace(
            BleakClient=lambda *a, **k: client, BleakScanner=None))
        link = server.omi_link
        link.start('fake-device')
        await ready.wait()
        assert link.connected and not link.recording
        client.audio(None, packet(0, 0, bytes(3200)))
        client.audio(None, packet(1, 0, bytes(3200)))
        assert stub_hub.session is None
        link.start_recording()
        client.audio(None, packet(2, 0, bytes(3200)))
        client.audio(None, packet(3, 0, bytes(3200)))
        assert stub_hub.session.samples == 1600
        link.stop_recording()
        assert client.is_connected and link.connected and not link.task.done()
        client.audio(None, packet(4, 0, bytes(3200)))
        assert stub_hub.session is None
        link.start_recording()
        client.audio(None, packet(5, 0, bytes(3200)))
        assert stub_hub.session is None  # no buffered audio from previous session
        client.audio(None, packet(6, 0, bytes(3200)))
        assert stub_hub.session.samples == 1600
        await link.stop()
        assert not client.is_connected and not link.recording
        assert stub_hub.session is None
    asyncio.run(scenario())
