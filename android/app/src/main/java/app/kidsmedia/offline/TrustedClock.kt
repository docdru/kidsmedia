package app.kidsmedia.offline

import android.content.Context
import android.os.SystemClock
import android.provider.Settings
import kotlinx.serialization.Serializable

/** The server's time at the last sync, paired with the tablet's since-boot clock. */
@Serializable
data class ClockAnchor(val serverEpochMs: Long, val elapsedMs: Long, val bootCount: Int? = null)

/**
 * The time used offline (SPEC §4.3, M7).
 *
 * At each sync the server's time is anchored to the monotonic since-boot clock. Offline,
 * "now" is the anchor carried forward with that clock, so changing the tablet's clock
 * in Settings doesn't move lockouts or the day. After a reboot the anchor is gone, and
 * the tablet's own clock is used until the next sync: offline playback keeps working
 * (it fails gracefully for the child) rather than locking up.
 *
 * @param elapsedMs since-boot milliseconds, including deep sleep (SystemClock.elapsedRealtime)
 * @param wallMs the tablet's wall clock (System.currentTimeMillis)
 * @param bootCount Settings.Global.BOOT_COUNT, null if unavailable
 */
class TrustedClock(
    private val elapsedMs: () -> Long,
    private val wallMs: () -> Long,
    private val bootCount: () -> Int?
) {
    fun anchor(serverEpochMs: Long): ClockAnchor = ClockAnchor(serverEpochMs, elapsedMs(), bootCount())

    /** Is [anchor] from this boot? */
    fun valid(anchor: ClockAnchor?): Boolean {
        if (anchor == null) return false
        val boot = bootCount()
        if (boot != null && anchor.bootCount != null && boot != anchor.bootCount) return false
        return elapsedMs() >= anchor.elapsedMs // the since-boot clock restarted: a reboot
    }

    /** Epoch milliseconds now: from the anchor while it's valid, else the tablet's clock. */
    fun nowMs(anchor: ClockAnchor?): Long =
        if (anchor != null && valid(anchor)) anchor.serverEpochMs + (elapsedMs() - anchor.elapsedMs) else wallMs()

    companion object {
        fun system(context: Context) = TrustedClock(
            elapsedMs = SystemClock::elapsedRealtime,
            wallMs = System::currentTimeMillis,
            bootCount = {
                runCatching {
                    Settings.Global.getInt(context.contentResolver, Settings.Global.BOOT_COUNT)
                }.getOrNull()
            }
        )
    }
}
