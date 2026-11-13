package app.kidsmedia.playback

import app.kidsmedia.playback.VolumeLimiter.Companion.capIndex
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VolumeLimiterTest {
    @Test
    fun noCapWithoutPercentOrAtFull() {
        assertNull(capIndex(15, null))
        assertNull(capIndex(15, 100))
        assertNull(capIndex(0, 50))
    }

    @Test
    fun roundsDownSoTheCapIsNeverExceeded() {
        assertEquals(9, capIndex(15, 60)) // 9.0
        assertEquals(7, capIndex(15, 50)) // 7.5
        assertEquals(10, capIndex(25, 40))
    }

    @Test
    fun keepsAtLeastOneStep() {
        assertEquals(1, capIndex(15, 5))
        assertEquals(1, capIndex(15, 1))
    }
}
