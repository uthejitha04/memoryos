package com.memoryos.app

import android.content.Context
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/** Explicit uploads only; neither recording nor leaving the activity starts a transfer. */
object PhoneTranscription {
    private val worker = Executors.newSingleThreadExecutor()
    @Volatile var busy = false; private set
    @Volatile var status = "Pair your laptop to transcribe saved recordings."; private set
    private fun prefs(context: Context) = context.getSharedPreferences("transcription", Context.MODE_PRIVATE)
    private fun client(config: JSONObject): OkHttpClient {
        val url = config.getString("url").toHttpUrl()
        require(url.isHttps && url.encodedPath == "/" && url.query == null && url.username.isEmpty() && url.password.isEmpty()) { "Pairing needs an HTTPS laptop address" }
        require(config.getString("token").isNotBlank()) { "Pairing token is missing" }
        val certificate = CertificateFactory.getInstance("X.509").generateCertificate(config.getString("certificate").byteInputStream())
        val store = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null); setCertificateEntry("laptop", certificate) }
        val factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(store) }
        val trust = factory.trustManagers.filterIsInstance<X509TrustManager>().single()
        val tls = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), null) }
        return OkHttpClient.Builder().sslSocketFactory(tls.socketFactory, trust)
            .followRedirects(false).followSslRedirects(false)
            .connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(10, TimeUnit.MINUTES).callTimeout(12, TimeUnit.MINUTES).build()
    }
    private fun request(config: JSONObject, path: String) = Request.Builder()
        .url(config.getString("url").trimEnd('/') + path)
        .header("x-memoryos-token", config.getString("token"))

    private fun response(client: OkHttpClient, request: Request): JSONObject = client.newCall(request).execute().use {
        val body = it.body?.string().orEmpty()
        if (!it.isSuccessful) {
            val detail = runCatching { JSONObject(body).getString("detail") }.getOrDefault("Laptop returned ${it.code}")
            error(detail)
        }
        JSONObject(body)
    }

    @Synchronized fun pair(context: Context, json: String) {
        if (busy) return
        busy = true
        val app = context.applicationContext
        status = "Checking laptop connection…"
        worker.execute {
            try {
                val config = JSONObject(json)
                val http = client(config)
                response(http, request(config, "/mobile/connection").build())
                prefs(app).edit().putString("pairing", config.toString()).commit()
                status = "Laptop paired. Choose a saved recording to transcribe."
            } catch (_: Exception) {
                status = "Could not pair. Check the pairing file, laptop HTTPS server and same Wi-Fi connection."
            } finally { busy = false }
        }
    }

    fun transcript(context: Context, file: File): String? =
        File(context.filesDir, "transcripts/${file.name}.txt").takeIf { it.exists() }?.readText()

    @Synchronized fun transcribe(context: Context, file: File) {
        if (busy) return
        val app = context.applicationContext
        val json = prefs(app).getString("pairing", null)
        if (json == null) { status = "Tap Pair laptop first."; return }
        busy = true
        status = "Preparing ${file.name}…"
        worker.execute {
            try {
                val config = JSONObject(json)
                val http = client(config)
                val digest = MessageDigest.getInstance("SHA-256")
                file.inputStream().use { stream ->
                    val buffer = ByteArray(65536)
                    while (true) { val count = stream.read(buffer); if (count < 0) break; digest.update(buffer, 0, count) }
                }
                val id = digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
                val path = "/mobile/recordings/$id"
                status = "Sending a copy to your laptop… Keep this app open."
                var result = response(http, request(config, path).put(file.asRequestBody("audio/wav".toMediaType())).build())
                val deadline = System.nanoTime() + TimeUnit.MINUTES.toNanos(30)
                while (result.getString("status") == "processing" && System.nanoTime() < deadline) {
                    status = "Laptop is transcribing ${file.name}…"
                    Thread.sleep(5000)
                    result = response(http, request(config, path).build())
                }
                if (result.getString("status") == "complete") {
                    val dir = File(app.filesDir, "transcripts").apply { mkdirs() }
                    val target = File(dir, "${file.name}.txt")
                    val temp = File(dir, "${file.name}.tmp")
                    temp.writeText(result.getString("text"))
                    check(temp.renameTo(target)) { "Unable to save transcript" }
                    status = "Transcript ready. Tap Read transcript."
                } else {
                    status = result.optString("detail", "Still processing. Tap Transcribe later to check again.")
                }
            } catch (_: Exception) {
                status = "Transfer or processing failed. Check laptop and Wi-Fi, then tap Transcribe to retry. Your audio stays on this phone."
            } finally { busy = false }
        }
    }
}
