"""Disposable microphone-protocol journey through persistence and citations."""
import json
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient


def wait_until(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('Capture-to-citation stage did not finish')


def run_journey(monkeypatch, tmp_path, speech_audio, real_models=False):
    import server
    from memory import MemoryStore
    from tests.test_memory import fake_embedder

    monkeypatch.setattr(server, 'SESSIONS', tmp_path)
    monkeypatch.setattr(server, 'DATA', tmp_path)
    monkeypatch.setattr(server, 'VOCAB_FILE', tmp_path / 'vocab.txt')
    monkeypatch.setenv('MEMORYOS_SYNC_URL', '')
    monkeypatch.setenv('MEMORYOS_SYNC_TOKEN', '')
    if not real_models:
        monkeypatch.setattr(server.Hub, 'live_transcribe', lambda self, chunk:
            ('The quick brown fox visited the coffee shop to discuss the project roadmap.', 'fixture'))
    embedder = None if real_models else fake_embedder
    store = MemoryStore(tmp_path / 'journey.db', embed_fn=embedder)
    monkeypatch.setattr(server, 'memstore', store)
    hub = server.Hub('small' if real_models else 'none', auto_polish=False)
    monkeypatch.setattr(server, 'hub', hub)
    monkeypatch.setattr(server, 'omi_link', server.OmiLink(hub))
    monkeypatch.setattr(server, 'polisher', None)
    try:
        wait_until(lambda: hub.model_ready, timeout=120)
        # Silence is saved but not transcribed: subsequent citations must retain
        # that offset rather than restarting the audio clock at the first word.
        audio = np.concatenate([np.zeros(24 * 16000, dtype=np.float32),
                                speech_audio, np.zeros(2 * 16000, dtype=np.float32)])
        pcm = (audio * 32767).astype('<i2')
        with TestClient(server.app) as client:
            with client.websocket_connect('/ws/audio') as ws:
                for i in range(0, len(pcm), 1600):
                    ws.send_bytes(pcm[i:i + 1600].tobytes())
                wait_until(lambda: hub.session is not None)
                name = hub.session.name
            wait_until(lambda: bool(store.recording(name) and store.recording(name)['enriched']))
            hub.wait_background(timeout=10)
            entries = [json.loads(line) for line in (tmp_path / f'{name}.jsonl').read_text().splitlines()]
            assert any('coffee shop' in e['text'].lower() for e in entries)
            assert all(e['start'] > 0 for e in entries)
            with wave.open(str(tmp_path / f'{name}.wav')) as wav:
                assert wav.getnframes() == len(pcm)
            assert client.get(f'/sessions/{name}/audio').status_code == 200

            def check_citation():
                response = client.post('/ask', json={'question': 'coffee shop project roadmap'})
                assert response.status_code == 200
                body = response.json()
                assert body['mode'] == 'retrieval'
                citation = next(c for c in body['citations'] if 'coffee shop' in c['text'].lower())
                assert citation['recording'] == name
                assert citation['start'] in {e['start'] for e in entries}
                assert 0 < citation['start'] < len(pcm) / 16000
                return citation['start']

            original_start = check_citation()
            store.close()
            store = MemoryStore(tmp_path / 'journey.db', embed_fn=embedder)
            monkeypatch.setattr(server, 'memstore', store)
            assert check_citation() == original_start
            store.delete_recording(name)
            server.recover_unindexed_sessions()
            assert check_citation() == original_start
            assert client.post(f'/memory/reindex/{name}').status_code == 200
            assert check_citation() == original_start
    finally:
        hub.wait_background(timeout=10)
        hub.chunks.put(None)
        store.close()


def test_capture_to_citation_without_polish(monkeypatch, tmp_path, speech_audio):
    run_journey(monkeypatch, tmp_path, speech_audio)


@pytest.mark.e2e
def test_real_local_capture_to_citation(monkeypatch, tmp_path, speech_audio):
    run_journey(monkeypatch, tmp_path, speech_audio, real_models=True)


def test_truncated_jsonl_keeps_completed_audio_offsets(stub_hub):
    import server
    name = 'session_20260915_120000'
    (server.SESSIONS / f'{name}.txt').write_text('[12:01:30] fixture words\n')
    (server.SESSIONS / f'{name}.jsonl').write_text(
        json.dumps({'start': 42.5, 'text': 'fixture words'}) + '\n{"start":')
    assert server.live_transcript_segments(name) == [
        {'start': 42.5, 'speaker': '', 'text': 'fixture words'}]
