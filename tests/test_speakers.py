"""Speaker labeling: two-voice fixture (Windows TTS David / Zira / David)."""

import pathlib
import wave

import numpy as np
import pytest

speakers = pytest.importorskip("speakers")
if not speakers.available():
    pytest.skip("resemblyzer/scipy not installed", allow_module_level=True)

DATA = pathlib.Path(__file__).parent / "data"


def load_wav(path):
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def energy_segments(audio, min_gap=0.35, min_seg=1.0):
    """Split on silence gaps — recovers the three TTS utterances."""
    frame = 160  # 10ms
    rms = np.sqrt(np.mean(
        audio[:len(audio) // frame * frame].reshape(-1, frame) ** 2, axis=1))
    voiced = rms > 0.01
    segs, start, gap = [], None, 0
    for i, v in enumerate(voiced):
        t = i * frame / 16000
        if v:
            if start is None:
                start = t
            gap = 0
        elif start is not None:
            gap += frame / 16000
            if gap >= min_gap:
                if t - gap - start >= min_seg:
                    segs.append({"start": start, "end": t - gap, "text": "x"})
                start, gap = None, 0
    if start is not None:
        segs.append({"start": start, "end": len(audio) / 16000, "text": "x"})
    return segs


def test_two_voices_get_two_speakers():
    wav = DATA / "two_voices.wav"
    audio = load_wav(wav)
    segs = energy_segments(audio)
    assert len(segs) >= 3, f"expected >=3 utterances, got {segs}"

    labeled = speakers.label_segments(wav, segs)
    labels = [s["speaker"] for s in labeled]
    assert len(set(labels)) == 2, f"expected 2 speakers, got {labels}"
    assert labels[0] == "Speaker 1", "speakers numbered by first appearance"
    # energy segmentation may split one spoken turn into several segments,
    # so compare the collapsed turn sequence: David, Zira, David
    turns = [labels[0]] + [b for a, b in zip(labels, labels[1:]) if b != a]
    assert turns == ["Speaker 1", "Speaker 2", "Speaker 1"], labels
