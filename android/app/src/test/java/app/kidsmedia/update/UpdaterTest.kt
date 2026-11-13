package app.kidsmedia.update

import app.kidsmedia.data.AppRelease
import java.security.MessageDigest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdaterTest {
    private fun release(code: Int) = AppRelease("1.0.$code", code, 10, "00")

    @Test
    fun onlyNewerVersionsAreOffered() {
        assertTrue(Updater.isNewer(release(10101), 10100))
        assertFalse(Updater.isNewer(release(10100), 10100))
        assertFalse(Updater.isNewer(release(9), 10100))
    }

    @Test
    fun sha256IsLowercaseHex() {
        val digest = MessageDigest.getInstance("SHA-256").digest("abc".toByteArray())
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", Updater.sha256Hex(digest))
    }

    @Test
    fun automaticCheckOnlyOnAndroid12PlusWhenIdleAndAtMostEverySixHours() {
        val s = 31 // Android 12
        val sixHours = Updater.AUTO_CHECK_INTERVAL_MS
        assertTrue(Updater.shouldAutoCheck(nowMs = 1_000, lastCheckMs = null, sdkInt = s, playing = false))
        assertFalse(Updater.shouldAutoCheck(nowMs = 1_000, lastCheckMs = null, sdkInt = 30, playing = false))
        assertFalse(Updater.shouldAutoCheck(nowMs = 1_000, lastCheckMs = null, sdkInt = s, playing = true))
        assertFalse(Updater.shouldAutoCheck(nowMs = sixHours - 1, lastCheckMs = 0, sdkInt = s, playing = false))
        assertTrue(Updater.shouldAutoCheck(nowMs = sixHours, lastCheckMs = 0, sdkInt = s, playing = false))
        // The tablet's clock was set back: due now instead of blocked until it catches up.
        assertTrue(Updater.shouldAutoCheck(nowMs = 1_000, lastCheckMs = 5_000, sdkInt = s, playing = false))
    }

    @Test
    fun aVersionAndroidDidntInstallIsNotDownloadedAgainAutomatically() {
        assertTrue(Updater.shouldAutoInstall(release(102), installedCode = 101, attemptedCode = null))
        // Attempted, but the app is still 101: Android dropped it (e.g. wanted a tap).
        assertFalse(Updater.shouldAutoInstall(release(102), installedCode = 101, attemptedCode = 102))
        // A newer release is tried again.
        assertTrue(Updater.shouldAutoInstall(release(103), installedCode = 101, attemptedCode = 102))
        assertFalse(Updater.shouldAutoInstall(release(101), installedCode = 101, attemptedCode = null))
    }
}
