# Android Omi capture preview

This native launcher records the Omi pendant directly over BLE. It does not request phone microphone permission. The older WebView launcher remains internal and is not part of this preview flow.

## Build

Use JDK 17 or later and Android SDK platform 36. The checked-in Gradle wrapper pins Gradle 8.13 with its distribution checksum. Android Gradle Plugin is 8.11.1 and Kotlin is 2.1.20.

```powershell
$env:JAVA_HOME = 'C:/Program Files/Android/Android Studio/jbr'
$env:ANDROID_HOME = 'C:/Users/navee/AppData/Local/Android/Sdk'
cd android
.\gradlew.bat assembleDebug testDebugUnitTest lintDebug
```

## Phone test

1. Disconnect Omi from the official app and the laptop capture service.
2. Open MemoryOS, tap Connect Omi, and allow Nearby devices and notifications.
3. Choose the pendant. Connecting does not record.
4. Start recording, speak, and confirm the saved-byte counter increases.
5. Stop recording. Omi should remain connected. Play the saved WAV.
6. Repeat with the screen locked for one minute, then a longer walk after that passes.

Audio is private in the app's recordings directory. Uploads happen only when you tap Transcribe; local Whisper on the paired laptop processes the audio without cloud API keys. Uninstalling clears these files. Recordings stop at two hours. Bluetooth loss saves the received audio and requires manual reconnect; missed audio is not recovered from pendant storage. Decode and disk writes currently run on the main looper, so performance, battery and long-duration behavior need physical testing before daily use.

## Verification

JVM tests cover packet fragmentation, loss, counter wrap, duplicates, recording boundaries and WAV headers. The instrumentation test feeds a generated two-second 440Hz tone through fragmented Omi Opus packets into Android's decoder and checks WAV duration/energy. It uses a temporary cache file, deletes it afterwards, and never accesses a microphone or pendant.

```powershell
.\gradlew.bat connectedDebugAndroidTest
```

`app/src/androidTest/assets/omi-tone.bin` contains repeated little-endian uint16 lengths followed by synthetic BLE notifications, generated using the existing Python helpers in tests/test_opus.py. It is synthetic data, not a personal recording.

Verified on 2026-09-16: debug build, lint and six JVM tests passed; the synthetic decoder instrumentation test passed on a Galaxy Z Flip7 running Android 16. The preview was installed successfully. The user subsequently confirmed real pendant capture and clear playback, a nearby locked-screen recording, and full playback after an unplugged walk carrying the phone. Long-duration and interruption recovery tests remain pending.


## Phone transcription (0.3 preview)

1. On the laptop, use the same Wi-Fi as the phone and run `.venv/Scripts/python mobile_server.py --url https://YOUR_LAPTOP_IP:8091`. The cached `small` faster-whisper model must already be installed. No models are downloaded by this service.
2. Install the updated debug APK with `adb install -r` (never uninstall: that deletes local recordings). Stop recording before installing.
3. Copy the generated `mobile-pairing.json` privately to the phone over USB. In MemoryOS, choose **Pair laptop** and select that file. It contains the laptop HTTPS certificate and a private token: do not commit/share it. Delete the transferred copy from Downloads after successful pairing.
4. Unplug USB. Stop recording, then tap **Transcribe** beside a saved recording. Keep MemoryOS open and the laptop awake while uploading. Tap **Read transcript** when ready. Silence produces an explicit no-speech message.
5. If the network or app closes, tap **Transcribe** again. The SHA-256 ID prevents duplicate server recordings; completed results survive a server restart. Interrupted processing can be retried. Uploads restart from the beginning if incomplete, rather than resuming byte ranges.

The TLS client trusts only the imported laptop certificate and still verifies its hostname. It never disables certificate checks or follows redirects. Changed laptop IP/certificate requires a new pairing file. Windows firewall must allow the dedicated port from the phone's LAN; phone-to-laptop reachability still needs physical validation.

The separate backend preserves the desktop server. It stores WAVs and durable result JSON under `MEMORYOS_DATA/mobile`, with a single processing job at a time and a 700 MB/two-hour input limit. Only mono PCM16 WAV is accepted. Whisper reads the WAV sample rate, including Android's 48 kHz output. The phone retains its original audio and caches text privately. These imports are not yet indexed into the desktop memory database or summarized. No automatic upload or background upload service is implemented; a terminated app can retry safely.

Validation: Android build, six JVM tests and lint passed. Eight synthetic upload/authentication/retry tests and existing API/security checks passed, plus an end-to-end synthetic speech test using the cached local model (22 Python tests total). The running HTTPS service passed authenticated and unauthenticated connection checks. The update was installed without uninstalling; the user confirmed pairing, upload and accurate transcript display on the phone.


## Summary preview (0.4, not yet enabled on the running service)

After transcription, **Create summary** checks the laptop's AI settings and asks for confirmation. **Read summary** displays a draft and suggested action items with exact supporting transcript quotes. Nothing is automatically marked complete or sent elsewhere. The app stores the result privately for offline reading.

Summary generation defaults to disabled. Set `MEMORYOS_SUMMARY_PROVIDER` in the service environment to `ollama`, `gemini`, `groq` or `openrouter`, then restart the dedicated service when no jobs are active. For Ollama also set `MEMORYOS_SUMMARY_MODEL` to an installed local model and run Ollama locally; the adapter uses fixed loopback, disables ambient proxies and rejects cloud-labelled/remote models. No model is downloaded by this feature. The protocol follows https://docs.ollama.com/api/chat.

Cloud mode requires the matching provider API key in the laptop process environment. Existing provider model constants are reused; there is no automatic fallback to other providers. The phone names the provider and asks before sending transcript text (never raw audio). Consent is bound to provider and model. Keys never enter the APK or pairing file. The dedicated service does not automatically load `.env`.

The preview rejects empty transcripts or text over 24,000 characters rather than silently summarizing only a prefix. Results are persisted separately as `mobile/<recording-id>.summary.json`; transcripts are unchanged. Failed/interrupted jobs can retry; completed results are cached. Quotes establish that the evidence exists, not that the model interpreted it correctly: users must review drafts. No task execution, reminders or cross-recording indexing is included.

Synthetic tests cover configuration/authentication, cloud consent, caching, retry/restart, empty/oversized transcripts and quote validation. Actual model output quality and physical summary-screen acceptance remain pending selection/configuration of an inference provider.
