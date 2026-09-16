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

Audio is private in the app's recordings directory. No uploads, API keys, or transcription are used yet. Uninstalling clears these files. Recordings stop at two hours. Bluetooth loss saves the received audio and requires manual reconnect; missed audio is not recovered from pendant storage. Decode and disk writes currently run on the main looper, so performance, battery and long-duration behavior need physical testing before daily use.

## Verification

JVM tests cover packet fragmentation, loss, counter wrap, duplicates, recording boundaries and WAV headers. The instrumentation test feeds a generated two-second 440Hz tone through fragmented Omi Opus packets into Android's decoder and checks WAV duration/energy. It uses a temporary cache file, deletes it afterwards, and never accesses a microphone or pendant.

```powershell
.\gradlew.bat connectedDebugAndroidTest
```

`app/src/androidTest/assets/omi-tone.bin` contains repeated little-endian uint16 lengths followed by synthetic BLE notifications, generated using the existing Python helpers in tests/test_opus.py. It is synthetic data, not a personal recording.

Verified on 2026-09-16: debug build, lint and six JVM tests passed; the synthetic decoder instrumentation test passed on a Galaxy Z Flip7 running Android 16. The preview was installed successfully. The user subsequently confirmed real pendant capture and clear playback, a nearby locked-screen recording, and full playback after an unplugged walk carrying the phone. Long-duration and interruption recovery tests remain pending.
