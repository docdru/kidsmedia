package app.kidsmedia.playback

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * Keeps the media volume at or below the cap the parent set for this tablet in the
 * admin (SPEC §4.3, `max_volume_percent`). It clamps Android's own music stream rather
 * than scaling the player, so the system slider shows the real level and the cap holds
 * for the speaker and for headphones (Android keeps a level per output).
 *
 * Clamped when the cap changes, when the volume changes (the system's volume broadcast),
 * when the output changes (headphones plugged in) and once a second while the playback
 * service runs, as a backstop. It only works while the app's process is alive; with
 * screen pinning that is all the time the tablet is in use.
 */
class VolumeLimiter(private val context: Context, private val percent: StateFlow<Int?>) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private var started = false

    private val volumeChanged =
        object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) = enforce()
        }

    private val outputChanged =
        object : AudioDeviceCallback() {
            override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>?) = enforce()

            override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>?) = enforce()
        }

    fun start(scope: CoroutineScope) {
        if (started) return
        started = true
        // A system broadcast (sent by system_server), so it reaches a not-exported receiver.
        ContextCompat.registerReceiver(
            context,
            volumeChanged,
            IntentFilter(VOLUME_CHANGED_ACTION),
            ContextCompat.RECEIVER_NOT_EXPORTED
        )
        audio.registerAudioDeviceCallback(outputChanged, Handler(Looper.getMainLooper()))
        scope.launch { percent.collect { enforce() } }
    }

    private fun cap(): Int? =
        if (audio.isVolumeFixed) null else capIndex(audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC), percent.value)

    /** True when "volume up" would go over the cap, so the app can swallow the key. */
    fun atCap(): Boolean = cap()?.let { audio.getStreamVolume(AudioManager.STREAM_MUSIC) >= it } ?: false

    /** Lower the music stream to the cap if it is above it. Cheap; safe to call often. */
    fun enforce() {
        val cap = cap() ?: return
        if (audio.getStreamVolume(AudioManager.STREAM_MUSIC) <= cap) return
        try {
            audio.setStreamVolume(AudioManager.STREAM_MUSIC, cap, 0)
        } catch (e: SecurityException) {
            // E.g. Do Not Disturb policies on some devices; try again on the next change.
            Log.w("kidsmedia", "volume cap: $e")
        }
    }

    companion object {
        /** Not in the public SDK, but sent by every Android version we support. */
        const val VOLUME_CHANGED_ACTION = "android.media.VOLUME_CHANGED_ACTION"

        /**
         * The highest allowed volume step for a stream with [maxIndex] steps, or null for
         * no cap. Rounds down so the cap is never exceeded, but keeps at least one step so
         * a low cap never silences the tablet completely.
         */
        fun capIndex(maxIndex: Int, percent: Int?): Int? {
            if (percent == null || percent >= 100 || maxIndex <= 0) return null
            return (maxIndex * percent.coerceAtLeast(0) / 100).coerceIn(1, maxIndex)
        }
    }
}
