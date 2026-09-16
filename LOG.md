# LOG.md

The founder's memory. Read this first every session. Update it before ending
any working session. Newest entries at the top.

## Status

- State: active
- Live URL: none public; runs locally, Railway config exists
- Stranger test: isolated capture-to-citation journey passes with real local models; user screenshots confirm Omi connection and speech capture; native Android stop/save/playback and short unplugged walking capture confirmed by user
- Currently working on: checkpointing verified Android transcription; summaries and action items are next
- Blocked on investor: nothing for local development; credential review remains a prerequisite before public distribution

## Session notes

- 2026-09-16: User confirmed laptop pairing, phone upload, visible transcript and accurate text from their Omi recording. Preparing reviewed PR for the transcription milestone. Self-review fixed a cleanup-error path that could retain the upload lock and a completion/status-read race; both have regression tests. Android build/unit/lint and 23 Python checks including cached real-model transcription passed; the added deterministic status-race test also passed in the final 10-test fast upload suite. Original audio and private pairing files remain excluded from Git. Summaries/action items, memory indexing and background uploads remain follow-up work.

- 2026-09-16: Added explicit Android Transcribe/Read transcript flow, HTTPS certificate-and-token pairing, checksum-idempotent WAV upload, durable local transcription results and restart/retry handling. Dedicated laptop service runs on port 8091 without interrupting desktop capture; no real audio was uploaded. Android build, six JVM tests and lint passed; 22 Python tests passed including cached local Whisper on synthetic 48 kHz speech. Authenticated HTTPS connection returned 200 and missing token 401. Phone installation/pairing and Wi-Fi acceptance pending. Imports are not yet indexed/enriched, and uploads require the app to remain open; local originals are preserved.

- 2026-09-16: User confirmed clear native Omi recording and playback on Galaxy Z Flip7, recording with the screen locked nearby, and full clear playback after walking with the phone unplugged from USB. Walking away without the phone caused a disconnect; this is not evidence of a phone-carried failure. Android PR #4 records this milestone with build/unit/lint CI. Local app/test APK builds, six JVM tests, lint and repository checks passed; self-review found no blocking preview issue. Fixed CI setup to avoid the retired SDK tools package. Automatic reconnect, long-duration reliability, upload and transcription remain follow-up work.

- 2026-09-16: Built native Android Omi preview for Galaxy Z Flip7 / Android 16. Added BLE scanning, connected-device foreground service, independent start/stop, PCM/Opus decoding, private checkpointed WAV storage, notification stop, recording wake lock and two-hour cap, and local playback. Updated AGP/Gradle for API36 and added checked-in checksum-pinned wrapper. Six JVM tests pass; debug APK build and lint pass (warnings remain). Synthetic BLE/Opus instrumentation test passed on the Galaxy Z Flip7 (Android 16); preview APK installed and launcher opened successfully. Independent review caught a 20-recording UI cap, removed before install. No live voice capture initiated. No upload/transcription integration yet; long-duration and physical BLE acceptance remain pending.

- 2026-09-15: Reproduced the pstack review finding with a deterministic browser test: an old failed Start cancelled a newer recording. Scoped callbacks to their socket and added explicit pending-start cancellation; removed broad click-handler cleanup. Regression now passes, alongside existing browser audio/control checks. No real device or recording data touched. Existing overlapping Bluetooth Connect and cross-session transcription-context risks remain separate follow-up work.

- 2026-09-15: Prepared GitHub review of accumulated capture fixes and redesign. 95 isolated fast regressions passed (3 real-model tests deselected), with browser control/audio checks. Self-review moved WebSocket acceptance inside cleanup handling so failed starts release capture ownership. Added runtime CI and untracked runtime logs while preserving local files. Requested clarification of p-stack; no installed matching tool was found. Android capture remains pending.

- 2026-09-15: Redesigned the web application as an ivory/forest-green workspace with desktop sidebar, compact mobile navigation, editorial headings across Capture/Memories/Ask, and a prominent Omi device panel. Preserved existing control IDs and capture behavior. Updated design guidance as a proposed direction pending user feedback. Node capture-control and packet/script checks passed; mocked headless Chrome screenshots inspected at desktop and 390px mobile widths, with no mobile horizontal overflow. Browser launch needed installed Chrome and sandbox escalation. No real recordings or device connections were used for visual checks. Android Omi capture implementation remains next after design review.

- 2026-09-15: Clarified the source badge and recording status as Omi microphone; renamed the separate default input to Default computer/phone microphone so it cannot be mistaken for Omi. Browser control and audio/script checks passed. Label-only change; refresh after stopping to load it.

- 2026-09-15: Implemented separate Omi Connect/Disconnect and Start/Stop recording, replacing the earlier label-only change. Local BLE and browser Bluetooth remain connected after Stop; idle audio is discarded, recording boundaries reset buffered frames, and connection loss stops capture. Added backend recording endpoints/status flags and made Omi the primary UI source with computer/phone microphone controls collapsed separately. Updated README. 26 isolated lifecycle/capture/API/security tests passed (one real-model test deselected), including simulated BLE idle/start/stop/start, plus Node browser packet/control and stop/restart checks. Initial sandbox pytest attempt hit temporary-directory permissions; rerun outside sandbox passed. Real server/device were not interrupted; backend restart and physical acceptance still required.

- 2026-09-15: User hardware screenshots confirm Omi connected and transcribed speech, but automatic capture on connect and a disabled microphone button were confusing. Renamed Omi actions Start/Stop Omi recording, distinguished microphone controls, explained source exclusivity, and made the empty state reflect connecting/recording. Transcript captions now label audio duration and report processing time (excluding queue wait). Updated README. Browser audio-helper/script syntax and five mocked capture-control states passed; whitespace check passed. Recording behavior and transcription performance were not changed; no live recording was interrupted. Physical stop/save/playback and end-to-end latency remain to verify.

- 2026-09-15: Started MemoryOS verification while preserving all pending work and private data. Found live-only citations were indexed at 0:00; now queue entries and JSONL carry captured audio offsets, and recovery/reindex preserve them. Added a disposable WebSocket capture -> WAV/transcript -> memory -> Ask journey covering leading silence, database reopen, orphan recovery and manual reindex, plus truncated-JSONL recovery. 71 selected fast tests, one real cached Whisper/MiniLM journey and the browser audio-helper checks passed with cloud keys/sync disabled and model downloads blocked. No physical pendant/microphone capture, deployment or GitHub changes. Old recordings lacking offsets still fall back to the beginning; new citations seek to the retrieved chunk start, not individual-word timing.


- 2026-09-10: Prepared a draft PR from an isolated working tree. Handoff and staged-diff checks pass. Based on the latest upstream master, preserving its newer Render configuration. Unrelated local audio/retrieval repairs are excluded. Existing history may still contain the old token; runtime acceptance was not rerun.

- 2026-09-10: Removed pairing_token.txt from Git tracking and added its ignore rule; local bytes were preserved. Historical exposure and possible credential rotation still need review before sharing. No recordings or application data were modified.


- 2026-09-10: Added repository orientation, agent handoff, setup/check instructions and a documentation CI baseline. Existing implementation and historical evidence remain in place; the repository handoff check and git diff --check passed. Runtime acceptance was not rerun unless a separate entry below explicitly records it. See DEVELOPMENT.md for verification gaps.

- 2026-08-25: Stamped during the Compass portfolio setup pass. PRD, ARCHITECTURE, ROADMAP, and DESIGN_SYSTEM docs already exist and remain the deeper record.
