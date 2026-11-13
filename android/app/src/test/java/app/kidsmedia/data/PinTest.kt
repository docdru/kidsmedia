package app.kidsmedia.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PinTest {
    @Test
    fun hashVerifiesOnlyTheSamePin() {
        val stored = Pin.hash("4711", iterations = 1_000)
        assertTrue(Pin.verify("4711", stored))
        assertFalse(Pin.verify("4712", stored))
        assertFalse(stored.contains("4711"))
    }

    @Test
    fun saltMakesHashesDiffer() {
        assertNotEquals(Pin.hash("1234", iterations = 1_000), Pin.hash("1234", iterations = 1_000))
    }

    @Test
    fun rejectsMalformedInput() {
        assertFalse(Pin.verify("1234", null))
        assertFalse(Pin.verify("1234", "garbage"))
        assertFalse(Pin.verify("12a4", Pin.hash("1234", iterations = 1_000)))
        assertFalse(Pin.isValid("123"))
        assertFalse(Pin.isValid("12345"))
    }

    @Test
    fun attemptsLockAfterFiveFailures() {
        var now = 0L
        val attempts = PinAttempts({ now })
        repeat(4) { attempts.record(false) }
        assertTrue(attempts.allowed())
        attempts.record(false)
        assertFalse(attempts.allowed())
        assertEquals(60_000L, attempts.lockedForMs())
        now += 60_000
        assertTrue(attempts.allowed())
    }

    @Test
    fun successResetsTheCount() {
        val attempts = PinAttempts({ 0L })
        repeat(4) { attempts.record(false) }
        attempts.record(true)
        repeat(4) { attempts.record(false) }
        assertTrue(attempts.allowed())
    }
}
