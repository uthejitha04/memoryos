package com.memoryos.app

import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaFormat
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Omi counter is per notification; only a following frame start proves completion. */
internal class OmiFrames {
    private var bytes = ByteArrayOutputStream()
    private var packet = -1
    private var index = -1
    fun feed(data: ByteArray): ByteArray? {
        if (data.size < 4) return null
        val next = (data[0].toInt() and 255) or ((data[1].toInt() and 255) shl 8)
        val part = data[2].toInt() and 255
        if (next == packet) return null
        val continuous = packet < 0 || next == ((packet + 1) and 65535)
        var completed: ByteArray? = null
        if (part == 0) {
            if (bytes.size() > 0 && continuous) completed = bytes.toByteArray()
            bytes.reset()
            bytes.write(data, 3, data.size - 3)
        } else if (bytes.size() > 0 && continuous && part == index + 1 && bytes.size() + data.size - 3 <= 8192) {
            bytes.write(data, 3, data.size - 3)
        } else bytes.reset()
        packet = next
        index = part
        return completed
    }
}

/** Headers are checkpointed every second; a process kill leaves a playable prefix. */
internal class OmiWav(val file: File, private var rate: Int) : AutoCloseable {
    private val out = RandomAccessFile(file, "rw")
    var size = 0L
        private set
    private var checkpoint = 0L
    init { header(); out.fd.sync() }
    fun setRate(value: Int) {
        require(size == 0L || value == rate) { "Decoder changed sample rate during recording" }
        rate = value
        header()
    }
    fun append(data: ByteArray) {
        require(data.size % 2 == 0) { "Invalid PCM16 audio" }
        require(size + data.size < 0xfffffff0L - 36) { "Recording reached WAV size limit" }
        out.seek(44 + size)
        out.write(data)
        size += data.size
        if (size - checkpoint >= rate * 2) {
            header()
            out.fd.sync()
            checkpoint = size
        }
    }
    private fun header() {
        val b = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        b.put("RIFF".toByteArray()).putInt((size + 36).toInt()).put("WAVEfmt ".toByteArray())
        b.putInt(16).putShort(1).putShort(1).putInt(rate).putInt(rate * 2).putShort(2).putShort(16)
        b.put("data".toByteArray()).putInt(size.toInt())
        out.seek(0); out.write(b.array())
    }
    override fun close() { try { header(); out.fd.sync() } finally { out.close() } }
}

internal class OmiOpus(private val sink: OmiWav) : AutoCloseable {
    private val codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS)
    private val info = MediaCodec.BufferInfo()
    private var pts = 0L
    init {
        try {
            val format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, 48000, 1)
            val head = ByteBuffer.allocate(19).order(ByteOrder.LITTLE_ENDIAN)
            head.put("OpusHead".toByteArray()).put(1).put(1).putShort(0).putInt(16000).putShort(0).put(0)
            format.setByteBuffer("csd-0", ByteBuffer.wrap(head.array()))
            format.setByteBuffer("csd-1", ByteBuffer.allocate(8).order(ByteOrder.nativeOrder()).putLong(0).apply { flip() })
            format.setByteBuffer("csd-2", ByteBuffer.allocate(8).order(ByteOrder.nativeOrder()).putLong(80_000_000).apply { flip() })
            format.setInteger(MediaFormat.KEY_PCM_ENCODING, AudioFormat.ENCODING_PCM_16BIT)
            codec.configure(format, null, null, 0)
            codec.start()
        } catch (e: Exception) { codec.release(); throw e }
    }
    fun feed(frame: ByteArray) {
        drain(0)
        val input = codec.dequeueInputBuffer(10_000)
        check(input >= 0) { "Opus decoder could not keep up; recording stopped" }
        val buffer = checkNotNull(codec.getInputBuffer(input))
        buffer.clear(); buffer.put(frame)
        codec.queueInputBuffer(input, 0, frame.size, pts, 0)
        pts += 20_000
        drain(0)
    }
    private fun drain(waitUs: Long): Boolean {
        while (true) {
            val output = codec.dequeueOutputBuffer(info, waitUs)
            when {
                output == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    val format = codec.outputFormat
                    check(format.getInteger(MediaFormat.KEY_CHANNEL_COUNT) == 1) { "Expected mono decoder output" }
                    check(!format.containsKey(MediaFormat.KEY_PCM_ENCODING) || format.getInteger(MediaFormat.KEY_PCM_ENCODING) == AudioFormat.ENCODING_PCM_16BIT) { "Expected PCM16 decoder output" }
                    sink.setRate(format.getInteger(MediaFormat.KEY_SAMPLE_RATE))
                }
                output >= 0 -> {
                    try {
                        if (info.size > 0) {
                            val buffer = checkNotNull(codec.getOutputBuffer(output))
                            buffer.position(info.offset); buffer.limit(info.offset + info.size)
                            val pcm = ByteArray(info.size); buffer.get(pcm); sink.append(pcm)
                        }
                        if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) return true
                    } finally { codec.releaseOutputBuffer(output, false) }
                }
                else -> return false
            }
        }
    }
    override fun close() {
        try {
            drain(0)
            val input = codec.dequeueInputBuffer(10_000)
            check(input >= 0) { "Unable to finish decoder" }
            codec.queueInputBuffer(input, 0, 0, pts, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
            var ended = false
            repeat(20) { if (!ended) ended = drain(10_000) }
            check(ended) { "Decoder final audio timed out" }
        } finally { try { codec.stop() } finally { codec.release() } }
    }
}
