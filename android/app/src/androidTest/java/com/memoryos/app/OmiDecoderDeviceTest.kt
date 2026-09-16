package com.memoryos.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

@RunWith(AndroidJUnit4::class)
class OmiDecoderDeviceTest {
    @Test fun syntheticBleOpusProducesPlayableWav() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val data = instrumentation.context.assets.open("omi-tone.bin").use { it.readBytes() }
        val file = File.createTempFile("synthetic-omi-", ".wav", instrumentation.targetContext.cacheDir)
        try {
            val wav = OmiWav(file, 48000)
            try {
                val decoder = OmiOpus(wav)
                try {
                    val frames = OmiFrames()
                    var offset = 0
                    while (offset < data.size) {
                        val length = (data[offset].toInt() and 255) or ((data[offset + 1].toInt() and 255) shl 8)
                        offset += 2
                        frames.feed(data.copyOfRange(offset, offset + length))?.let { decoder.feed(it) }
                        offset += length
                    }
                } finally { decoder.close() }
            } finally { wav.close() }
            val bytes = file.readBytes()
            val buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            assertEquals("RIFF", String(bytes.copyOfRange(0, 4)))
            assertEquals(bytes.size - 44, buffer.getInt(40))
            val rate = buffer.getInt(24)
            val seconds = (bytes.size - 44).toDouble() / (rate * 2)
            assertTrue("Expected roughly two seconds, got $seconds", seconds in 1.7..2.2)
            var energy = 0.0
            for (i in 44 until bytes.size step 2) {
                val sample = buffer.getShort(i).toDouble() / 32768.0
                energy += sample * sample
            }
            assertTrue("Decoded output must contain the synthetic tone", sqrt(energy / ((bytes.size - 44) / 2)) > 0.1)
        } finally { file.delete() }
    }
}
