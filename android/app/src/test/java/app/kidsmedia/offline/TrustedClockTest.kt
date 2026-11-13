package app.kidsmedia.offline

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TrustedClockTest {
    private var elapsed = 5_000_000L
    private var wall = 1_000L
    private var boot: Int? = 7
    private val clock = TrustedClock({ elapsed }, { wall }, { boot })

    private val server = 1_793_000_000_000L // the server's time at the sync

    @Test
    fun changingTheTabletClockDoesNotMoveTime() {
        val anchor = clock.anchor(server)
        elapsed += 60_000
        wall += 6 * 3_600_000L // the child sets the clock six hours ahead
        assertTrue(clock.valid(anchor))
        assertEquals(server + 60_000, clock.nowMs(anchor))
    }

    @Test
    fun afterARebootTheTabletClockIsUsed() {
        val anchor = clock.anchor(server)
        boot = 8
        elapsed = 10_000_000L // up longer than before, but a new boot
        assertFalse(clock.valid(anchor))
        assertEquals(wall, clock.nowMs(anchor))
    }

    @Test
    fun withoutABootCountARestartedSinceBootClockMeansAReboot() {
        boot = null
        val anchor = clock.anchor(server)
        elapsed = 1_000L
        assertFalse(clock.valid(anchor))
        assertEquals(wall, clock.nowMs(anchor))
    }

    @Test
    fun noAnchorYetUsesTheTabletClock() {
        assertEquals(wall, clock.nowMs(null))
    }
}
