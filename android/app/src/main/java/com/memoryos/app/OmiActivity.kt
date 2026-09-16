package com.memoryos.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.media.MediaPlayer
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.content.ContextCompat
import java.io.File
import java.util.UUID

class OmiActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var status: TextView
    private lateinit var evidence: TextView
    private lateinit var connect: Button
    private lateinit var record: Button
    private lateinit var library: LinearLayout
    private var scanning: ScanCallback? = null
    private var scanDialog: AlertDialog? = null
    private var player: MediaPlayer? = null
    private var librarySignature = ""
    private val green = Color.rgb(32, 74, 52)
    private val poll = object : Runnable {
        override fun run() { render(); handler.postDelayed(this, 500) }
    }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun text(value: String, size: Float = 16f) = TextView(this).apply {
        text = value; textSize = size; setTextColor(green); setPadding(0, dp(8), 0, dp(8))
    }
    private fun button(label: String, action: () -> Unit) = Button(this).apply {
        text = label; isAllCaps = false; setTextColor(Color.WHITE)
        backgroundTintList = android.content.res.ColorStateList.valueOf(green)
        setOnClickListener { action() }
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(24), dp(24), dp(24))
        }
        val scroll = ScrollView(this).apply {
            setBackgroundColor(Color.rgb(245, 244, 239)); addView(root)
            setOnApplyWindowInsetsListener { view, insets ->
                if (Build.VERSION.SDK_INT >= 30) {
                    val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                    view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
                } else {
                    @Suppress("DEPRECATION")
                    view.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                        insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
                }
                insets
            }
        }
        setContentView(scroll)
        root.addView(text("MemoryOS", 18f))
        root.addView(text("Be here.\nRemember later.", 34f).apply { typeface = Typeface.create("serif", Typeface.NORMAL) })
        root.addView(text("Omi microphone", 22f))
        root.addView(text("Audio comes from your pendant. Your phone microphone is not used.", 14f))
        status = text("Not connected"); root.addView(status)
        evidence = text("", 14f); root.addView(evidence)
        connect = button("Connect Omi") {
            if (OmiService.connected) command(OmiService.ACTION_DISCONNECT) else prepareScan()
        }; root.addView(connect)
        record = button("Start recording") {
            stopPlayback()
            command(if (OmiService.recording) OmiService.ACTION_STOP else OmiService.ACTION_START)
        }; root.addView(record)
        root.addView(text("Stop saves the recording and keeps Omi connected. You can lock your screen while recording. This preview stops each recording after two hours.", 14f))
        root.addView(text("Saved on this phone", 22f))
        root.addView(text("This first build saves audio locally. Transcription and upload are not connected yet. Uninstalling the app removes these recordings.", 13f))
        root.addView(button("Stop playback") { stopPlayback() })
        library = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }; root.addView(library)
    }
    override fun onResume() { super.onResume(); handler.post(poll) }
    override fun onPause() { handler.removeCallbacks(poll); stopScan(); stopPlayback(); super.onPause() }
    private fun permissions(): Array<String> {
        val result = if (Build.VERSION.SDK_INT >= 31) mutableListOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
            else mutableListOf(Manifest.permission.ACCESS_FINE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) result.add(Manifest.permission.POST_NOTIFICATIONS)
        return result.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }.toTypedArray()
    }
    private fun prepareScan() {
        val missing = permissions()
        if (missing.isNotEmpty()) { requestPermissions(missing, 40); return }
        scan()
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 40) {
            val bluetoothDenied = permissions.indices.any {
                (Build.VERSION.SDK_INT < 33 || permissions[it] != Manifest.permission.POST_NOTIFICATIONS) && grantResults[it] != PackageManager.PERMISSION_GRANTED
            }
            if (bluetoothDenied) message("Nearby devices permission is needed to connect to Omi.") else scan()
        }
    }
    private fun scan() {
        stopScan()
        val adapter = getSystemService(BluetoothManager::class.java).adapter
        if (adapter == null || !adapter.isEnabled) { message("Turn on Bluetooth, then tap Connect Omi."); return }
        val devices = linkedMapOf<String, String>()
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(16), 0, dp(16), 0) }
        val hint = text("Searching for Omi…\nDisconnect it from the official Omi app and your laptop first.", 14f)
        list.addView(hint)
        scanDialog = AlertDialog.Builder(this).setTitle("Choose your Omi").setView(ScrollView(this).apply { addView(list) })
            .setNegativeButton("Cancel") { _, _ -> stopScan() }.create()
        scanDialog?.setOnDismissListener { stopScan() }; scanDialog?.show()
        val callback = object : ScanCallback() {
            override fun onScanResult(type: Int, result: ScanResult) {
                handler.post {
                    if (scanning !== this) return@post
                    val name = result.scanRecord?.deviceName ?: ""
                    val isOmi = name.contains("omi", true) || name.contains("friend", true) ||
                        result.scanRecord?.serviceUuids?.any { it.uuid == UUID.fromString("19b10000-e8f2-537e-4f6c-d104768a1214") } == true
                    if (!isOmi || devices.containsKey(result.device.address)) return@post
                    devices[result.device.address] = name
                    hint.text = "Tap your pendant to connect."
                    list.addView(button("${name.ifBlank { "Omi" }} · ${result.device.address.takeLast(5)}") {
                        val address = result.device.address
                        stopScan(); scanDialog?.dismiss()
                        command(OmiService.ACTION_CONNECT, address)
                    })
                }
            }
            override fun onScanFailed(errorCode: Int) { handler.post { stopScan(); hint.text = "Search failed ($errorCode). Close this window and try again." } }
        }
        scanning = callback
        try {
            adapter.bluetoothLeScanner.startScan(null, ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), callback)
            handler.postDelayed({
                if (scanning === callback) {
                    stopScan()
                    if (devices.isEmpty()) hint.text = "No Omi found. Check that it is on and disconnected from other apps, then try again."
                }
            }, 12000)
        } catch (_: SecurityException) { stopScan(); message("Bluetooth permission is missing. Tap Connect Omi to grant it.") }
    }
    private fun stopScan() {
        val callback = scanning ?: return
        scanning = null
        try { getSystemService(BluetoothManager::class.java).adapter?.bluetoothLeScanner?.stopScan(callback) } catch (_: SecurityException) { }
    }
    private fun command(action: String, address: String? = null) {
        try {
            val intent = Intent(this, OmiService::class.java).setAction(action)
            if (address != null) intent.putExtra("address", address)
            if (action == OmiService.ACTION_CONNECT) ContextCompat.startForegroundService(this, intent)
            else startService(intent)
        } catch (e: Exception) { message("Could not start Omi: ${e.message}") }
    }
    private fun render() {
        status.text = OmiService.status
        connect.text = if (OmiService.connected) "Disconnect Omi" else "Connect Omi"
        record.text = if (OmiService.recording) "Stop recording" else "Start recording"
        record.isEnabled = OmiService.connected
        evidence.text = if (OmiService.recording) "Audio saved: ${OmiService.bytesRecorded / 1024} KB" else "Connected does not mean recording. You choose when to start."
        val files = File(filesDir, "recordings").listFiles()?.filter { it.extension == "wav" && it.length() > 44 }?.sortedByDescending { it.lastModified() } ?: emptyList()
        val signature = files.joinToString { it.name } + OmiService.recording
        if (signature == librarySignature) return
        librarySignature = signature; library.removeAllViews()
        if (files.isEmpty()) library.addView(text("Your recordings will appear here.", 14f))
        files.forEach { file ->
            library.addView(button("Play ${file.nameWithoutExtension}") {
                if (OmiService.recording) { message("Stop recording before playback."); return@button }
                stopPlayback()
                try {
                    player = MediaPlayer().also { p ->
                        p.setDataSource(file.absolutePath)
                        p.setOnCompletionListener { stopPlayback() }
                        p.setOnErrorListener { _, _, _ -> stopPlayback(); message("This recording could not be played."); true }
                        p.prepare(); p.start()
                    }
                } catch (_: Exception) { stopPlayback(); message("This recording could not be played.") }
            }.apply { isEnabled = !OmiService.recording })
        }
    }
    private fun stopPlayback() { player?.release(); player = null }
    private fun message(value: String) { AlertDialog.Builder(this).setMessage(value).setPositiveButton("OK", null).show() }
}
