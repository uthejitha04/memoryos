import numpy as np

from omi_transcribe import (
    MAX_CHUNK_SECS,
    MIN_CHUNK_SECS,
    SAMPLE_RATE,
    SILENCE_SECS,
    Chunker,
)


def tone(secs, freq=300.0, amp=0.3):
    t = np.linspace(0, secs, int(SAMPLE_RATE * secs), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(secs):
    return np.zeros(int(SAMPLE_RATE * secs), dtype=np.float32)


def feed_in_frames(chunker, audio, frame=320):
    for i in range(0, len(audio), frame):
        chunker.feed(audio[i:i + frame])


def test_cuts_at_silence_after_min_length():
    chunks = []
    c = Chunker(on_chunk=chunks.append)
    feed_in_frames(c, np.concatenate([
        tone(MIN_CHUNK_SECS + 1), silence(SILENCE_SECS + 0.3),
        tone(MIN_CHUNK_SECS + 1), silence(SILENCE_SECS + 0.3),
    ]))
    assert len(chunks) == 2
    for ch in chunks:
        secs = len(ch) / SAMPLE_RATE
        assert MIN_CHUNK_SECS <= secs <= MAX_CHUNK_SECS


def test_force_cut_at_max_length_without_silence():
    chunks = []
    c = Chunker(on_chunk=chunks.append)
    feed_in_frames(c, tone(MAX_CHUNK_SECS * 2.5))
    assert len(chunks) >= 2
    assert all(len(ch) / SAMPLE_RATE <= MAX_CHUNK_SECS + 0.1 for ch in chunks)


def test_pure_silence_is_dropped():
    chunks = []
    c = Chunker(on_chunk=chunks.append)
    feed_in_frames(c, silence(MAX_CHUNK_SECS * 2))
    c.flush()
    assert chunks == []


def test_flush_emits_short_remainder():
    chunks = []
    c = Chunker(on_chunk=chunks.append)
    feed_in_frames(c, tone(2.0))
    assert chunks == []          # below min, silence not reached
    c.flush()
    assert len(chunks) == 1
    assert abs(len(chunks[0]) / SAMPLE_RATE - 2.0) < 0.1
