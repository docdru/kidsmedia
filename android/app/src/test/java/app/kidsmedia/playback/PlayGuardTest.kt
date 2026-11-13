package app.kidsmedia.playback

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlayGuardTest {
    private var now = 1_000_000L
    private val guard = PlayGuard { now }

    @Test
    fun countsOnlyWhilePlaying() {
        guard.reset(null)
        guard.setPlaying(true)
        now += 10_000
        guard.setPlaying(false)
        now += 60_000 // paused: not counted
        guard.setPlaying(true)
        now += 5_000
        assertEquals(15.0, guard.playedSecondsTotal(), 0.001)
    }

    @Test
    fun totalNeverGoesBackwardsWithinASession() {
        guard.reset(null)
        guard.setPlaying(true)
        var last = 0.0
        repeat(20) {
            now += 700
            val total = guard.playedSecondsTotal()
            assertTrue(total >= last)
            last = total
        }
    }

    @Test
    fun resetStartsANewSessionButKeepsPlaying() {
        guard.reset(null)
        guard.setPlaying(true)
        now += 30_000
        guard.reset(600)
        assertEquals(0.0, guard.playedSecondsTotal(), 0.001)
        now += 2_000
        assertEquals(2.0, guard.playedSecondsTotal(), 0.001)
    }

    @Test
    fun deadlineRunsOnWallClockEvenWhilePaused() {
        guard.reset(90)
        assertEquals(90L, guard.remainingSeconds())
        now += 89_500
        assertEquals(1L, guard.remainingSeconds())
        assertFalse(guard.expired())
        now += 500
        assertTrue(guard.expired())
        assertEquals(0L, guard.remainingSeconds())
    }

    @Test
    fun noLimitMeansNoDeadline() {
        guard.reset(null)
        now += 10_000_000
        assertNull(guard.remainingSeconds())
        assertFalse(guard.expired())
    }

    @Test
    fun graceFlagFollowsServer() {
        guard.reset(120, grace = true)
        assertTrue(guard.graceActive)
        guard.update(60, grace = false)
        assertFalse(guard.graceActive)
    }
}
