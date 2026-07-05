"""Optional speaker labeling for polished transcripts.

Uses resemblyzer voice embeddings + agglomerative clustering to assign
"Speaker N" to each transcript segment. Fully optional: if the ML stack is
not installed (or fails on this Python), `available()` returns False and
polish output is simply unlabeled.
"""

import wave

import numpy as np

MIN_SEG_SECS = 0.4       # too short to embed reliably
SIM_THRESHOLD = 0.75     # cosine similarity above this = same speaker

_encoder = None


def available() -> bool:
    try:
        import resemblyzer  # noqa: F401
        import scipy  # noqa: F401
        return True
    except Exception:
        return False


def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder

        _encoder = VoiceEncoder("cpu")
    return _encoder


def _read_wav(wav_path) -> np.ndarray:
    with wave.open(str(wav_path)) as w:
        rate = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    if rate != 16000:
        idx = (np.arange(int(len(audio) * 16000 / rate)) * rate / 16000).astype(int)
        audio = audio[np.clip(idx, 0, len(audio) - 1)]
    return audio


def _cluster(embeddings: np.ndarray) -> list[int]:
    """Agglomerative clustering with average linkage on cosine distance."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist

    if len(embeddings) == 1:
        return [0]
    dists = pdist(embeddings, metric="cosine")
    tree = linkage(dists, method="average")
    labels = fcluster(tree, t=1.0 - SIM_THRESHOLD, criterion="distance")
    return [int(l) - 1 for l in labels]


def label_segments(wav_path, segments: list[dict]) -> list[dict]:
    """Add a 'speaker' key ("Speaker 1", ...) to each segment.

    Speakers are numbered in order of first appearance. Segments too short to
    embed inherit the previous segment's speaker.
    """
    if not segments:
        return segments
    audio = _read_wav(wav_path)
    encoder = _get_encoder()

    embeds, embed_idx = [], []
    for i, seg in enumerate(segments):
        a = audio[int(seg["start"] * 16000):int(seg["end"] * 16000)]
        if len(a) / 16000 >= MIN_SEG_SECS:
            embeds.append(encoder.embed_utterance(a))
            embed_idx.append(i)
    if not embeds:
        return segments

    raw_labels = _cluster(np.stack(embeds))

    # renumber in order of first appearance
    order: dict[int, int] = {}
    for lab in raw_labels:
        order.setdefault(lab, len(order) + 1)

    labeled = dict(zip(embed_idx, raw_labels))
    last = None
    for i, seg in enumerate(segments):
        if i in labeled:
            last = f"Speaker {order[labeled[i]]}"
        seg["speaker"] = last or "Speaker 1"
    return segments
