"""Omi -> better transcription, Phase 1 (laptop).

Connects to an Omi pendant over BLE, decodes its Opus audio stream,
and transcribes locally with faster-whisper.

Usage:
    python omi_transcribe.py                       # scan for Omi, transcribe live
    python omi_transcribe.py --address AA:BB:...   # connect to a known device
    python omi_transcribe.py --model small         # faster, less accurate
    python omi_transcribe.py --list                # just scan and list BLE devices
"""

import argparse
import asyncio
import datetime as dt
import queue
import sys
import threading
import time

import av
import numpy as np
from bleak import BleakClient, BleakScanner

# --- Omi BLE protocol (from BasedHardware/omi firmware) -----------------
OMI_SERVICE_UUID = "19b10000-e8f2-537e-4f6c-d104768a1214"
AUDIO_DATA_UUID = "19b10001-e8f2-537e-4f6c-d104768a1214"
CODEC_TYPE_UUID = "19b10002-e8f2-537e-4f6c-d104768a1214"

CODEC_NAMES = {0: "PCM 16-bit 16kHz", 1: "PCM 16-bit 8kHz", 20: "Opus 16kHz", 21: "Opus 16kHz (FS320)"}

SAMPLE_RATE = 16000

# --- Chunking: cut on silence so Whisper gets natural phrases -----------
# Whisper pads every call to a 30s window, so short chunks waste compute.
# Bigger chunks = better throughput, slightly higher latency.
SILENCE_RMS = 0.008          # below this = silence (float32 audio)
SILENCE_SECS = 0.7           # this much silence ends a chunk
MIN_CHUNK_SECS = 6.0
MAX_CHUNK_SECS = 20.0


class AudioFrameAssembler:
    """Reassembles Omi BLE notifications into Opus frames and decodes to PCM.

    Each notification: [packet_id u16 LE][frame_index u8][payload...].
    The packet counter advances per notification; index zero starts a frame.
    A missing notification invalidates the buffered frame until the next start.
    """

    def __init__(self):
        self._buf = bytearray()
        self._last_pkt = None
        self._last_index = None
        self.dropped_frames = 0

    def feed(self, data: bytes) -> np.ndarray | None:
        if len(data) < 4:
            return None
        pkt_id = int.from_bytes(data[:2], "little")
        index = data[2]
        payload = data[3:]
        out = None
        contiguous = self._last_pkt is None or pkt_id == (self._last_pkt + 1) % 65536
        if pkt_id == self._last_pkt:
            return None  # duplicate notification
        if index == 0:
            if self._buf and contiguous:
                out = self._decode(bytes(self._buf))
            elif self._buf:
                self.dropped_frames += 1
            self._buf = bytearray(payload)
        elif (self._buf and contiguous and self._last_index is not None
              and index == self._last_index + 1):
            self._buf.extend(payload)
        else:
            self.dropped_frames += 1
            self._buf.clear()
        self._last_pkt = pkt_id
        self._last_index = index
        return out

    def flush(self):
        # The wire format has no end flag. Do not decode a potentially partial
        # final frame on disconnect; at most one short Opus frame is discarded.
        self._buf.clear()
        self._last_pkt = self._last_index = None

class OpusStreamDecoder(AudioFrameAssembler):
    def __init__(self):
        super().__init__()
        self.codec = av.CodecContext.create("opus", "r")
        self.resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

    def _decode(self, frame: bytes) -> np.ndarray | None:
        try:
            packet = av.Packet(frame)
            samples = []
            for decoded in self.codec.decode(packet):
                for resampled in self.resampler.resample(decoded):
                    arr = resampled.to_ndarray().flatten()
                    samples.append(arr.astype(np.float32) / 32768.0)
            if samples:
                return np.concatenate(samples)
        except Exception as e:  # corrupted frame: skip, keep stream alive
            print(f"  [opus decode skipped a frame: {e}]", file=sys.stderr)
        return None


class PcmStreamDecoder(AudioFrameAssembler):
    """Fallback for firmware streaming raw PCM16."""

    def __init__(self, rate: int):
        super().__init__()
        self.rate = rate

    def _decode(self, payload: bytes) -> np.ndarray | None:
        if len(payload) % 2:
            return None
        pcm = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
        if self.rate != SAMPLE_RATE:  # crude upsample 8k -> 16k
            pcm = np.repeat(pcm, SAMPLE_RATE // self.rate)
        return pcm


class Transcriber(threading.Thread):
    """Owns the Whisper model; consumes audio chunks from a queue."""

    def __init__(self, model_name: str, transcript_path: str):
        super().__init__(daemon=True)
        self.chunks: queue.Queue[np.ndarray | None] = queue.Queue()
        self.model_name = model_name
        self.transcript_path = transcript_path
        self.ready = threading.Event()

    def run(self):
        from faster_whisper import WhisperModel

        print(f"Loading Whisper model '{self.model_name}' (first run downloads it)...")
        import os
        model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                             cpu_threads=max(4, (os.cpu_count() or 4) - 2))
        self.ready.set()
        print("Model ready. Listening...\n")
        while True:
            chunk = self.chunks.get()
            if chunk is None:
                break
            t0 = time.time()
            segments, _info = model.transcribe(chunk, language=None, vad_filter=True, beam_size=1,
                                               condition_on_previous_text=False)
            text = " ".join(s.text.strip() for s in segments).strip()
            if not text:
                continue
            stamp = dt.datetime.now().strftime("%H:%M:%S")
            line = f"[{stamp}] {text}"
            print(line)
            with open(self.transcript_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            lag = time.time() - t0
            if lag > len(chunk) / SAMPLE_RATE:
                print(f"  [transcription slower than realtime ({lag:.1f}s) — consider a smaller --model]",
                      file=sys.stderr)


class Chunker:
    """Accumulates PCM and emits chunks at silence boundaries."""

    def __init__(self, on_chunk):
        self.on_chunk = on_chunk
        self._audio: list[np.ndarray] = []
        self._samples = 0
        self._silent_samples = 0

    def feed(self, pcm: np.ndarray):
        self._audio.append(pcm)
        self._samples += len(pcm)
        rms = float(np.sqrt(np.mean(pcm**2))) if len(pcm) else 0.0
        self._silent_samples = self._silent_samples + len(pcm) if rms < SILENCE_RMS else 0

        secs = self._samples / SAMPLE_RATE
        silent_secs = self._silent_samples / SAMPLE_RATE
        if secs >= MAX_CHUNK_SECS or (secs >= MIN_CHUNK_SECS and silent_secs >= SILENCE_SECS):
            chunk = np.concatenate(self._audio)
            self._audio.clear()
            self._samples = 0
            self._silent_samples = 0
            # skip chunks that are all silence
            if float(np.sqrt(np.mean(chunk**2))) >= SILENCE_RMS * 0.5:
                self.on_chunk(chunk)

    def flush(self):
        if self._audio and self._samples / SAMPLE_RATE >= 0.5:
            chunk = np.concatenate(self._audio)
            if float(np.sqrt(np.mean(chunk**2))) >= SILENCE_RMS * 0.5:
                self.on_chunk(chunk)
        self._audio.clear()
        self._samples = 0
        self._silent_samples = 0


async def find_omi(address: str | None) -> str:
    if address:
        return address
    print("Scanning for Omi device (10s)...")
    devices = await BleakScanner.discover(timeout=10.0)
    for d in devices:
        name = (d.name or "").lower()
        if "omi" in name or "friend" in name:
            print(f"Found: {d.name} [{d.address}]")
            return d.address
    names = ", ".join(sorted({d.name for d in devices if d.name})) or "(none with names)"
    raise SystemExit(f"No Omi found. Devices seen: {names}\n"
                     f"Make sure the pendant is charged, nearby, and NOT connected to the Omi phone app\n"
                     f"(BLE allows only one central at a time). Or pass --address.")


async def run(args):
    if args.list:
        devices = await BleakScanner.discover(timeout=10.0)
        for d in sorted(devices, key=lambda d: d.name or "~"):
            print(f"{d.address}  {d.name or '(unnamed)'}")
        return

    transcript_path = dt.datetime.now().strftime("transcript_%Y%m%d_%H%M%S.txt")
    transcriber = Transcriber(args.model, transcript_path)
    transcriber.start()

    address = await find_omi(args.address)
    transcriber.ready.wait()

    chunker = Chunker(on_chunk=transcriber.chunks.put)

    async with BleakClient(address, timeout=30.0) as client:
        print(f"Connected to {address}")
        codec_id = 20
        try:
            raw = await client.read_gatt_char(CODEC_TYPE_UUID)
            codec_id = raw[0]
        except Exception:
            print("Could not read codec characteristic; assuming Opus 16kHz.")
        print(f"Audio codec: {CODEC_NAMES.get(codec_id, f'unknown ({codec_id})')}")

        if codec_id in (20, 21):
            decoder = OpusStreamDecoder()
        elif codec_id == 0:
            decoder = PcmStreamDecoder(16000)
        elif codec_id == 1:
            decoder = PcmStreamDecoder(8000)
        else:
            raise SystemExit(f"Unsupported codec id {codec_id}")

        stats = {"packets": 0, "seconds": 0.0}

        def on_audio(_char, data: bytearray):
            stats["packets"] += 1
            pcm = decoder.feed(bytes(data))
            if pcm is not None and len(pcm):
                stats["seconds"] += len(pcm) / SAMPLE_RATE
                chunker.feed(pcm)

        await client.start_notify(AUDIO_DATA_UUID, on_audio)
        print(f"Streaming audio. Transcript -> {transcript_path}  (Ctrl+C to stop)\n")
        try:
            while client.is_connected:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            chunker.flush()
            print(f"\nSession: {stats['packets']} BLE packets, {stats['seconds']:.0f}s of audio.")
            print(f"Transcript saved to {transcript_path}")


def main():
    p = argparse.ArgumentParser(description="Transcribe Omi pendant audio with local Whisper")
    p.add_argument("--address", help="BLE address of the Omi (skips scanning)")
    p.add_argument("--model", default="small",
                   help="faster-whisper model: tiny/base/small/medium/distil-large-v3/large-v3 "
                        "(small keeps up live on CPU; larger = more accurate but slower)")
    p.add_argument("--list", action="store_true", help="scan and list BLE devices, then exit")
    args = p.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
