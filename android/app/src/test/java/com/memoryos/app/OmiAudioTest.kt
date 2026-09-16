package com.memoryos.app

import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files

class OmiAudioTest {
    private fun packet(counter: Int, index: Int, vararg payload: Int) =
        byteArrayOf(counter.toByte(), (counter shr 8).toByte(), index.toByte(), *payload.map { it.toByte() }.toByteArray())

    @Test fun assemblesFragmentsAtNextStartAndWrapsCounter() {
        val frames = OmiFrames()
        assertNull(frames.feed(packet(65534, 0, 11, 12)))
        assertNull(frames.feed(packet(65535, 1, 13)))
        assertArrayEquals(byteArrayOf(11, 12, 13), frames.feed(packet(0, 0, 20)))
        assertArrayEquals(byteArrayOf(20), frames.feed(packet(1, 0, 30)))
    }

    @Test fun packetLossDiscardsFrameAndRecoversAtNextBoundary() {
        val frames = OmiFrames()
        frames.feed(packet(1, 0, 11))
        assertNull(frames.feed(packet(3, 1, 99)))
        assertNull(frames.feed(packet(4, 0, 20)))
        assertArrayEquals(byteArrayOf(20), frames.feed(packet(5, 0, 30)))
    }

    @Test fun duplicatesAndOutOfOrderFragmentsNeverEnterAudio() {
        val frames = OmiFrames()
        frames.feed(packet(1, 0, 11))
        assertNull(frames.feed(packet(1, 0, 99)))
        assertArrayEquals(byteArrayOf(11), frames.feed(packet(2, 0, 20)))
        assertNull(frames.feed(packet(3, 2, 88)))
        assertNull(frames.feed(packet(4, 0, 30)))
        assertArrayEquals(byteArrayOf(30), frames.feed(packet(5, 0, 40)))
    }

    @Test fun freshRecordingDoesNotAcceptContinuationOfPriorFrame() {
        val frames = OmiFrames()
        assertNull(frames.feed(packet(30, 1, 99)))
        assertNull(frames.feed(packet(31, 0, 11)))
        assertArrayEquals(byteArrayOf(11), frames.feed(packet(32, 0, 12)))
    }

    @Test fun wavFinalizesHeaderAndPreservesLittleEndianPcm() {
        val file = Files.createTempFile("omi-wav-test", ".wav").toFile()
        try {
            OmiWav(file, 48000).use {
                it.setRate(16000)
                it.append(byteArrayOf(0, 1, 2, 3))
                assertEquals(4L, it.size)
            }
            val bytes = file.readBytes()
            val b = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            assertEquals(48, bytes.size)
            assertEquals("RIFF", String(bytes, 0, 4))
            assertEquals(40, b.getInt(4))
            assertEquals("WAVE", String(bytes, 8, 4))
            assertEquals(16000, b.getInt(24))
            assertEquals(32000, b.getInt(28))
            assertEquals(4, b.getInt(40))
            assertArrayEquals(byteArrayOf(0, 1, 2, 3), bytes.copyOfRange(44, 48))
        } finally { file.delete() }
    }

    @Test fun wavCheckpointMakesAudioReadableBeforeClose() {
        val file = Files.createTempFile("omi-checkpoint-test", ".wav").toFile()
        try {
            OmiWav(file, 8000).use {
                it.append(ByteArray(16000))
                val b = ByteBuffer.wrap(file.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
                assertEquals(16000, b.getInt(40))
                assertEquals(16036, b.getInt(4))
            }
        } finally { file.delete() }
    }
}
