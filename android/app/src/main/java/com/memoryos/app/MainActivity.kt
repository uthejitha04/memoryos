package com.memoryos.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.webkit.JavascriptInterface
import org.json.JSONArray
import org.json.JSONObject
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/**
 * MemoryOS mobile app.
 *
 * A full-screen WebView renders the entire MemoryOS UI from the cloud, so the
 * app IS the web app. The page's own Record button calls the injected
 * `MemoryOSNative` bridge, which runs a foreground microphone service — so
 * recording continues with the screen off, the one thing a web page can't do.
 * Transcripts stream back over the events websocket, so the live feed updates
 * exactly as it does in the browser.
 *
 * The pairing token is entered once and stored only in private prefs.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private val prefs by lazy { getSharedPreferences("memoryos", Context.MODE_PRIVATE) }

    /** Exposed to the web page as window.MemoryOSNative */
    inner class Bridge(private val url: String, private val token: String) {
        @JavascriptInterface
        fun startRecording() = start(-1)

        @JavascriptInterface
        fun startRecordingOn(deviceId: Int) = start(deviceId)

        private fun start(deviceId: Int) {
            val i = Intent(this@MainActivity, RecordingService::class.java)
                .putExtra("url", url).putExtra("token", token)
                .putExtra("deviceId", deviceId)
            ContextCompat.startForegroundService(this@MainActivity, i)
        }

        @JavascriptInterface
        fun stopRecording() {
            stopService(Intent(this@MainActivity, RecordingService::class.java))
        }

        @JavascriptInterface
        fun isRecording(): Boolean = RecordingService.running

        /** JSON list of real input devices so the web picker can switch mics. */
        @JavascriptInterface
        fun listInputs(): String {
            val am = getSystemService(AudioManager::class.java)
            val seen = HashSet<String>()
            val arr = JSONArray()
            for (d in am.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
                val label = inputLabel(d)
                if (!seen.add(label)) continue           // collapse duplicates
                arr.put(JSONObject().put("id", d.id).put("label", label))
            }
            return arr.toString()
        }
    }

    private fun inputLabel(d: AudioDeviceInfo): String = when (d.type) {
        AudioDeviceInfo.TYPE_BUILTIN_MIC -> "Phone microphone"
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "Bluetooth: " + (d.productName ?: "headset")
        AudioDeviceInfo.TYPE_WIRED_HEADSET -> "Wired headset"
        AudioDeviceInfo.TYPE_USB_DEVICE, AudioDeviceInfo.TYPE_USB_HEADSET -> "USB mic"
        else -> (d.productName?.toString()?.takeIf { it.isNotBlank() } ?: "Input") + ""
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ensurePermissions()
        val token = prefs.getString("token", "").orEmpty()
        if (token.isEmpty()) promptToken() else buildUi(token)
    }

    private fun buildUi(token: String) {
        val url = prefs.getString("url", DEFAULT_URL)!!.trimEnd('/')
        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            addJavascriptInterface(Bridge(url, token), "MemoryOSNative")
            webViewClient = WebViewClient()
            webChromeClient = object : WebChromeClient() {
                override fun onPermissionRequest(request: PermissionRequest) {
                    request.grant(request.resources)  // allow mic use while screen is on
                }
            }
        }
        setContentView(web)
        web.loadUrl("$url/?token=$token")
    }

    private fun promptToken() {
        val input = EditText(this).apply { hint = "Pairing token" }
        AlertDialog.Builder(this)
            .setTitle("Pair MemoryOS")
            .setMessage("Paste your pairing token — the part after token= in your MemoryOS link.")
            .setView(input)
            .setCancelable(false)
            .setPositiveButton("Pair") { _, _ ->
                val t = input.text.toString().trim()
                if (t.isEmpty()) {
                    promptToken()
                } else {
                    prefs.edit().putString("token", t).putString("url", DEFAULT_URL).apply()
                    buildUi(t)
                }
            }
            .show()
    }

    private fun ensurePermissions() {
        val need = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) need.add(Manifest.permission.POST_NOTIFICATIONS)
        val ask = need.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (ask.isNotEmpty()) ActivityCompat.requestPermissions(this, ask.toTypedArray(), 1)
    }

    override fun onBackPressed() {
        if (::web.isInitialized && web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    companion object {
        const val DEFAULT_URL = "https://memoryos-production-bcd1.up.railway.app"
    }
}
