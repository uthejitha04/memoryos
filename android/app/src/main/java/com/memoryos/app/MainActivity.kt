package com.memoryos.app

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/**
 * MemoryOS capture client. Holds the server URL + pairing token, requests mic
 * permission, and starts/stops the foreground RecordingService that streams
 * audio to the cloud brain — continuing with the screen off.
 *
 * No secrets are baked in: the pairing token is entered once on the device and
 * stored only in the app's private prefs.
 */
class MainActivity : Activity() {

    private lateinit var urlField: EditText
    private lateinit var tokenField: EditText
    private lateinit var toggle: Button
    private lateinit var status: TextView

    private val prefs by lazy { getSharedPreferences("memoryos", Context.MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val d = resources.displayMetrics.density
        val pad = (24 * d).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        val title = TextView(this).apply {
            text = "MemoryOS"
            textSize = 30f
        }
        val subtitle = TextView(this).apply {
            text = "Records with the screen off and streams to your cloud brain."
            textSize = 14f
            setPadding(0, (8 * d).toInt(), 0, (24 * d).toInt())
        }

        urlField = EditText(this).apply {
            hint = "Server URL (https://…)"
            setText(prefs.getString("url", DEFAULT_URL))
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
        }
        tokenField = EditText(this).apply {
            hint = "Pairing token (from your MemoryOS link, after token=)"
            setText(prefs.getString("token", ""))
            setSingleLine()
        }
        toggle = Button(this).apply {
            text = "Start recording"
            setOnClickListener { onToggle() }
        }
        status = TextView(this).apply {
            setPadding(0, (24 * d).toInt(), 0, 0)
            gravity = Gravity.CENTER
            textSize = 15f
        }

        root.addView(title)
        root.addView(subtitle)
        root.addView(urlField)
        root.addView(tokenField)
        root.addView(toggle)
        root.addView(status)
        setContentView(root)

        ensurePermissions()
        refresh()
    }

    private fun ensurePermissions() {
        val need = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) need.add(Manifest.permission.POST_NOTIFICATIONS)
        val ask = need.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (ask.isNotEmpty()) ActivityCompat.requestPermissions(this, ask.toTypedArray(), 1)
    }

    private fun onToggle() {
        if (RecordingService.running) {
            stopService(Intent(this, RecordingService::class.java))
        } else {
            val url = urlField.text.toString().trim()
            val token = tokenField.text.toString().trim()
            if (token.isEmpty()) {
                status.text = "Enter your pairing token first."
                return
            }
            prefs.edit().putString("url", url).putString("token", token).apply()
            val i = Intent(this, RecordingService::class.java)
                .putExtra("url", url)
                .putExtra("token", token)
            ContextCompat.startForegroundService(this, i)
        }
        toggle.postDelayed({ refresh() }, 400)
    }

    private fun refresh() {
        val on = RecordingService.running
        toggle.text = if (on) "Stop recording" else "Start recording"
        status.text = if (on) "● Recording — you can lock your screen" else "Idle"
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    companion object {
        // public server URL only; the pairing token is entered on-device
        const val DEFAULT_URL = "https://memoryos-production-bcd1.up.railway.app"
    }
}
