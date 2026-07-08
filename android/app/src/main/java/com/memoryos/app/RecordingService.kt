package com.memoryos.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import java.util.concurrent.TimeUnit

/**
 * Foreground microphone service. Android permits mic capture with the screen
 * off only from a foreground service typed "microphone" with a visible
 * notification — that's what makes pocket recording possible.
 *
 * Captures 16 kHz mono PCM16 (the exact format the MemoryOS server's
 * /ws/audio endpoint expects) and streams it over a websocket.
 */
class RecordingService : Service() {

    private var thread: Thread? = null
    @Volatile private var recording = false
    private var ws: WebSocket? = null
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val url = intent?.getStringExtra("url").orEmpty()
        val token = intent?.getStringExtra("token").orEmpty()
        startForegroundNotice()
        running = true
        startStreaming(url, token)
        return START_STICKY
    }

    private fun startForegroundNotice() {
        val chanId = "memoryos_rec"
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(chanId, "Recording", NotificationManager.IMPORTANCE_LOW))
        val n: Notification = Notification.Builder(this, chanId)
            .setContentTitle("MemoryOS")
            .setContentText("Recording — open the app to stop")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(1, n)
        }
    }

    private fun startStreaming(url: String, token: String) {
        val pm = getSystemService(PowerManager::class.java)
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "memoryos:rec").also {
            it.acquire(12 * 60 * 60 * 1000L)  // safety cap: 12h
        }

        val wsUrl = url.trim().removeSuffix("/")
            .replaceFirst("https://", "wss://")
            .replaceFirst("http://", "ws://") + "/ws/audio?token=" + token

        val client = OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()

        ws = client.newWebSocket(
            Request.Builder().url(wsUrl).build(),
            object : WebSocketListener() {
                override fun onFailure(webSocket: WebSocket, t: Throwable, r: Response?) {
                    Log.w("MemoryOS", "ws failure: ${t.message}")
                }
            })

        recording = true
        thread = Thread { captureLoop() }.also { it.start() }
    }

    private fun captureLoop() {
        val rate = 16000
        val minBuf = AudioRecord.getMinBufferSize(
            rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val bufSize = maxOf(minBuf, rate * 2)
        val recorder = try {
            AudioRecord(
                MediaRecorder.AudioSource.MIC, rate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufSize)
        } catch (e: SecurityException) {
            Log.e("MemoryOS", "mic permission missing", e)
            return
        }
        val buf = ByteArray(3200)  // 100 ms @ 16 kHz mono 16-bit
        recorder.startRecording()
        while (recording) {
            val n = recorder.read(buf, 0, buf.size)
            if (n > 0) ws?.send(buf.copyOf(n).toByteString())
        }
        recorder.stop()
        recorder.release()
    }

    override fun onDestroy() {
        recording = false
        running = false
        thread?.join(1000)
        ws?.close(1000, "stopped")
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    companion object {
        @Volatile var running = false
    }
}
