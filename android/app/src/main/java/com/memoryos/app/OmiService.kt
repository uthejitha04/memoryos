package com.memoryos.app

import android.annotation.SuppressLint
import android.app.*
import android.bluetooth.*
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.*
import java.io.File
import java.util.UUID

/** Connection survives Stop; all connection and recording state belongs to the main looper. */
@SuppressLint("MissingPermission")
class OmiService : Service() {
    companion object {
        const val ACTION_CONNECT = "com.memoryos.app.OMI_CONNECT"
        const val ACTION_START = "com.memoryos.app.OMI_START"
        const val ACTION_STOP = "com.memoryos.app.OMI_STOP"
        const val ACTION_DISCONNECT = "com.memoryos.app.OMI_DISCONNECT"
        const val EXTRA_ADDRESS = "address"
        @Volatile var status = "Omi disconnected"; private set
        @Volatile var connected = false; private set
        @Volatile var recording = false; private set
        @Volatile var bytesRecorded = 0L; private set
        @Volatile var lastFile: String? = null; private set
        private val SERVICE = UUID.fromString("19b10000-e8f2-537e-4f6c-d104768a1214")
        private val AUDIO = UUID.fromString("19b10001-e8f2-537e-4f6c-d104768a1214")
        private val CODEC = UUID.fromString("19b10002-e8f2-537e-4f6c-d104768a1214")
        private val CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }
    private val handler = Handler(Looper.getMainLooper())
    private var gatt: BluetoothGatt? = null
    private var generation = 0
    private var codecId = -1
    private var frames = OmiFrames()
    private var writer: OmiWav? = null
    private var decoder: OmiOpus? = null
    @Volatile private var audioSession = 0L
    private var wakeLock: PowerManager.WakeLock? = null
    private var foreground = false
    override fun onBind(intent: Intent?) = null
    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel("omi", "Omi connection", NotificationManager.IMPORTANCE_LOW))
    }
    private fun notification(): Notification {
        val launch = PendingIntent.getActivity(this, 0, Intent(this, OmiActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val action = if (recording) ACTION_STOP else ACTION_DISCONNECT
        val stop = PendingIntent.getService(this, 7, Intent(this, OmiService::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return Notification.Builder(this, "omi").setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle(if (recording) "Recording Omi" else "MemoryOS · Omi")
            .setContentText(status).setContentIntent(launch).setOngoing(true)
            .addAction(Notification.Action.Builder(null, if (recording) "Stop recording" else "Disconnect", stop).build()).build()
    }
    private fun publish(text: String) {
        status = text
        if (foreground) getSystemService(NotificationManager::class.java).notify(2002, notification())
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        try {
            when (intent?.action) {
                ACTION_CONNECT -> {
                    if (!foreground) {
                        if (Build.VERSION.SDK_INT >= 29) startForeground(2002, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
                        else startForeground(2002, notification())
                        foreground = true
                    }
                    connect(intent.getStringExtra(EXTRA_ADDRESS) ?: error("Select an Omi device first"))
                }
                ACTION_START -> startRecording()
                ACTION_STOP -> stopRecording()
                ACTION_DISCONNECT -> { disconnect(); publish("Omi disconnected"); stopSelf() }
            }
        } catch (e: Exception) { fail(e.message ?: "Omi operation failed") }
        return START_NOT_STICKY
    }
    private fun connect(address: String) {
        check(BluetoothAdapter.checkBluetoothAddress(address)) { "Invalid Omi address" }
        disconnect()
        val adapter = getSystemService(BluetoothManager::class.java).adapter
        check(adapter != null && adapter.isEnabled) { "Turn on Bluetooth" }
        publish("Connecting to Omi…")
        val token = generation
        gatt = adapter.getRemoteDevice(address).connectGatt(this, false, callback(token), BluetoothDevice.TRANSPORT_LE)
        check(gatt != null) { "Bluetooth connection could not start" }
        handler.postDelayed({ if (token == generation && !connected) fail("Omi connection timed out; retry Connect") }, 25_000)
    }
    private fun callback(token: Int) = object : BluetoothGattCallback() {
        private fun dispatch(g: BluetoothGatt, block: () -> Unit) {
            handler.post {
                if (token == generation && g === gatt) {
                    try { block() } catch (e: Exception) { fail(e.message ?: "Omi Bluetooth error") }
                }
            }
        }
        override fun onConnectionStateChange(g: BluetoothGatt, result: Int, state: Int) = dispatch(g) {
            if (result != BluetoothGatt.GATT_SUCCESS || state == BluetoothProfile.STATE_DISCONNECTED) {
                fail("Omi disconnected; received audio saved. Reconnect to continue.")
            } else if (state == BluetoothProfile.STATE_CONNECTED) {
                publish("Reading Omi services…")
                check(g.discoverServices()) { "Unable to discover Omi services" }
            }
        }
        override fun onServicesDiscovered(g: BluetoothGatt, result: Int) = dispatch(g) {
            check(result == BluetoothGatt.GATT_SUCCESS) { "Omi service discovery failed ($result)" }
            val service = g.getService(SERVICE) ?: error("This device does not expose the Omi audio service")
            checkNotNull(service.getCharacteristic(AUDIO)) { "Omi audio characteristic missing" }
            val characteristic = service.getCharacteristic(CODEC) ?: error("Omi codec characteristic missing")
            check(g.readCharacteristic(characteristic)) { "Unable to read Omi audio codec" }
        }
        @Deprecated("Compatibility callback for Android 12 and earlier")
        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, result: Int) {
            if (Build.VERSION.SDK_INT < 33) readCodec(g, c.uuid, c.value?.clone() ?: byteArrayOf(), result)
        }
        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray, result: Int) = readCodec(g, c.uuid, value.clone(), result)
        private fun readCodec(g: BluetoothGatt, uuid: UUID, value: ByteArray, result: Int) = dispatch(g) {
            if (uuid != CODEC) return@dispatch
            check(result == BluetoothGatt.GATT_SUCCESS && value.isNotEmpty()) { "Could not read Omi codec" }
            codecId = value[0].toInt() and 255
            check(codecId in listOf(0, 1, 20, 21)) { "Unsupported Omi audio codec $codecId" }
            val audio = g.getService(SERVICE).getCharacteristic(AUDIO)
            check(g.setCharacteristicNotification(audio, true)) { "Could not enable Omi audio notifications" }
            val descriptor = audio.getDescriptor(CCCD) ?: error("Omi audio notification descriptor missing")
            if (Build.VERSION.SDK_INT >= 33) {
                check(g.writeDescriptor(descriptor, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE) == BluetoothStatusCodes.SUCCESS) { "Could not subscribe to Omi audio" }
            } else {
                @Suppress("DEPRECATION")
                descriptor.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                @Suppress("DEPRECATION")
                check(g.writeDescriptor(descriptor)) { "Could not subscribe to Omi audio" }
            }
        }
        override fun onDescriptorWrite(g: BluetoothGatt, descriptor: BluetoothGattDescriptor, result: Int) = dispatch(g) {
            if (descriptor.uuid != CCCD) return@dispatch
            check(result == BluetoothGatt.GATT_SUCCESS) { "Omi audio subscription failed ($result)" }
            connected = true
            publish("Omi connected · ready to record")
        }
        @Deprecated("Compatibility callback for Android 12 and earlier")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            if (Build.VERSION.SDK_INT < 33) audio(g, c.uuid, c.value?.clone() ?: byteArrayOf())
        }
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) = audio(g, c.uuid, value.clone())
        private fun audio(g: BluetoothGatt, uuid: UUID, value: ByteArray) {
            val receivedSession = audioSession
            val receivedWhileRecording = recording
            dispatch(g) {
                if (!receivedWhileRecording || receivedSession != audioSession || !recording || uuid != AUDIO) return@dispatch
                val frame = frames.feed(value) ?: return@dispatch
                try {
                    if (codecId == 20 || codecId == 21) decoder?.feed(frame)
                    else writer?.append(frame)
                    bytesRecorded = writer?.size ?: 0L
                } catch (e: Exception) {
                    stopRecording()
                    publish("Recording stopped: ${e.message}. Received audio saved.")
                }
            }
        }
    }
    private fun startRecording() {
        check(connected) { "Connect Omi before recording" }
        if (recording) return
        val directory = File(filesDir, "recordings")
        check(directory.isDirectory || directory.mkdirs()) { "Cannot create recording folder" }
        val file = File(directory, "omi_${System.currentTimeMillis()}_${UUID.randomUUID().toString().take(8)}.wav")
        val wav = OmiWav(file, if (codecId == 1) 8000 else if (codecId >= 20) 48000 else 16000)
        try { decoder = if (codecId >= 20) OmiOpus(wav) else null }
        catch (e: Exception) { wav.close(); file.delete(); throw IllegalStateException("Opus decoder unavailable: ${e.message}", e) }
        writer = wav
        audioSession++
        frames = OmiFrames()
        bytesRecorded = 0L
        wakeLock = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MemoryOS:OmiCapture").apply { acquire(2 * 60 * 60 * 1000L + 60_000L) }
        recording = true
        val session = audioSession
        handler.postDelayed({
            if (recording && audioSession == session) {
                stopRecording()
                publish("Saved recording at two-hour limit · Omi stays connected")
            }
        }, 2 * 60 * 60 * 1000L)
        publish("Recording Omi · stored privately on this phone")
    }
    private fun stopRecording() {
        if (writer == null) return
        recording = false
        audioSession++
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        frames = OmiFrames() // Discard the unproven final fragment, never carry it into next recording.
        var problem: String? = null
        try { decoder?.close() } catch (e: Exception) { problem = e.message } finally { decoder = null }
        val wav = writer!!
        bytesRecorded = wav.size
        try { wav.close(); lastFile = wav.file.absolutePath } catch (e: Exception) { problem = e.message }
        writer = null
        publish(if (problem == null) "Saved recording · Omi stays connected" else "Saved received audio; finish warning: $problem")
    }
    private fun disconnect() {
        stopRecording()
        generation++
        connected = false
        codecId = -1
        val old = gatt
        gatt = null
        try { old?.disconnect() } catch (_: Exception) { }
        try { old?.close() } catch (_: Exception) { }
    }
    private fun fail(message: String) {
        disconnect()
        publish(message)
        stopSelf()
    }
    override fun onDestroy() {
        disconnect()
        foreground = false
        stopForeground(STOP_FOREGROUND_REMOVE)
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }
}
