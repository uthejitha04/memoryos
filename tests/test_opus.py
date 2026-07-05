"""Round-trip the Omi BLE framing: PCM -> libopus -> fragmented BLE
notifications -> OpusStreamDecoder -> PCM."""

import av
import numpy as np

from omi_transcribe import SAMPLE_RATE, OpusStreamDecoder


def encode_opus_packets(pcm16: np.ndarray) -> list[bytes]:
    enc = av.CodecContext.create("libopus", "w")
    enc.sample_rate = SAMPLE_RATE
    enc.layout = "mono"
    enc.format = "s16"
    enc.bit_rate = 32000
    packets = []
    frame_size = 320  # 20ms, like the omi firmware
    for i in range(0, len(pcm16) - frame_size, frame_size):
        frame = av.AudioFrame.from_ndarray(pcm16[i:i + frame_size].reshape(1, -1),
                                           format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = i
        packets.extend(bytes(p) for p in enc.encode(frame))
    packets.extend(bytes(p) for p in enc.encode(None))
    return packets


def to_ble_notifications(packets: list[bytes], mtu_payload=160) -> list[bytes]:
    out = []
    for pkt_id, opus_pkt in enumerate(packets):
        for frag_idx, off in enumerate(range(0, len(opus_pkt), mtu_payload)):
            header = pkt_id.to_bytes(2, "little") + bytes([frag_idx])
            out.append(header + opus_pkt[off:off + mtu_payload])
    return out


def make_tone_pcm16(secs=2.0):
    t = np.linspace(0, secs, int(SAMPLE_RATE * secs), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")


def decode_all(notifications):
    dec = OpusStreamDecoder()
    parts = [p for n in notifications if (p := dec.feed(n)) is not None]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def test_roundtrip_preserves_duration_and_energy():
    pcm16 = make_tone_pcm16(2.0)
    notifications = to_ble_notifications(encode_opus_packets(pcm16))
    audio = decode_all(notifications)
    assert len(audio) / SAMPLE_RATE > 1.7          # most of the 2s survives
    assert float(np.sqrt(np.mean(audio**2))) > 0.1  # tone energy, not silence


def test_small_mtu_fragmentation():
    """Force every opus frame to span multiple BLE notifications."""
    pcm16 = make_tone_pcm16(1.0)
    notifications = to_ble_notifications(encode_opus_packets(pcm16), mtu_payload=20)
    frag_counts = {}
    for n in notifications:
        frag_counts[int.from_bytes(n[:2], "little")] = n[2] + 1
    assert max(frag_counts.values()) >= 2, "test should actually fragment"
    audio = decode_all(notifications)
    assert len(audio) / SAMPLE_RATE > 0.8


def test_corrupted_frame_does_not_kill_stream():
    pcm16 = make_tone_pcm16(1.0)
    notifications = to_ble_notifications(encode_opus_packets(pcm16))
    # corrupt one whole notification's payload mid-stream
    mid = len(notifications) // 2
    bad = bytearray(notifications[mid])
    bad[3:] = b"\xff" * (len(bad) - 3)
    notifications[mid] = bytes(bad)
    audio = decode_all(notifications)
    assert len(audio) / SAMPLE_RATE > 0.6  # stream continues past the bad frame


def test_short_notifications_ignored():
    dec = OpusStreamDecoder()
    assert dec.feed(b"") is None
    assert dec.feed(b"\x00\x01") is None
