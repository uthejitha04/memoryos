from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_opus import encode_opus_packets, make_tone_pcm16, to_ble_notifications

packets = to_ble_notifications(encode_opus_packets(make_tone_pcm16(2)), mtu_payload=20)
root = Path(__file__).resolve().parents[1]
target = root / 'android/app/src/androidTest/assets/omi-tone.bin'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(b''.join(len(packet).to_bytes(2, 'little') + packet for packet in packets))
print('Generated synthetic two-second Omi tone fixture')
