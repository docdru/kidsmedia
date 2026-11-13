package app.kidsmedia.playback

import android.app.PendingIntent
import android.content.Intent
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy
import androidx.media3.exoplayer.upstream.LoadErrorHandlingPolicy
import androidx.media3.session.DefaultMediaNotificationProvider
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import app.kidsmedia.KidsMediaApp
import app.kidsmedia.MainActivity
import app.kidsmedia.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Background playback (screen off, lock-screen and notification controls) with
 * generous buffering per SPEC §4.3. Audio and video use different buffer sizes, so
 * the service swaps the session's player when the kind changes.
 */
@UnstableApi
class PlaybackService :
    MediaSessionService(),
    PlayerHost {
    private var session: MediaSession? = null
    private var player: ExoPlayer? = null
    private var playerIsVideo = false
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val controller get() = (application as KidsMediaApp).graph.playback

    override fun onCreate() {
        super.onCreate()
        setMediaNotificationProvider(
            DefaultMediaNotificationProvider.Builder(this).build().apply { setSmallIcon(R.drawable.ic_notification) }
        )
        val initial = buildPlayer(video = false)
        player = initial
        val openApp =
            PendingIntent.getActivity(
                this,
                0,
                Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
        session = MediaSession.Builder(this, initial).setSessionActivity(openApp).build()
        controller.attach(this)
        scope.launch {
            while (isActive) {
                controller.tick()
                (application as KidsMediaApp).graph.volume.enforce()
                delay(1_000)
            }
        }
    }

    override fun playerFor(video: Boolean): Player {
        val current = player
        if (current != null && playerIsVideo == video) return current
        val fresh = buildPlayer(video)
        session?.player = fresh
        current?.release()
        player = fresh
        playerIsVideo = video
        return fresh
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    override fun onTaskRemoved(rootIntent: Intent?) {
        val p = player
        if (p == null || !p.playWhenReady || p.mediaItemCount == 0) stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        controller.detach()
        session?.release()
        player?.release()
        session = null
        player = null
        super.onDestroy()
    }

    private fun buildPlayer(video: Boolean): ExoPlayer {
        val http = (application as KidsMediaApp).graph.http
        val loadControl =
            if (video) {
                DefaultLoadControl
                    .Builder()
                    .setBufferDurationsMs(30_000, 180_000, 2_500, 5_000)
                    .setBackBuffer(30_000, true)
                    .build()
            } else {
                DefaultLoadControl
                    .Builder()
                    .setBufferDurationsMs(60_000, 600_000, 2_500, 5_000)
                    .setBackBuffer(60_000, true)
                    .build()
            }
        val sources =
            DefaultMediaSourceFactory(this)
                // DefaultDataSource: http(s) through OkHttp (auth header), file:// for offline copies (M7).
                .setDataSourceFactory(DefaultDataSource.Factory(this, OkHttpDataSource.Factory(http)))
                .setLoadErrorHandlingPolicy(PatientRetryPolicy())
        val attributes =
            AudioAttributes
                .Builder()
                .setUsage(C.USAGE_MEDIA)
                .setContentType(if (video) C.AUDIO_CONTENT_TYPE_MOVIE else C.AUDIO_CONTENT_TYPE_MUSIC)
                .build()
        return ExoPlayer
            .Builder(this)
            .setMediaSourceFactory(sources)
            .setLoadControl(loadControl)
            .setAudioAttributes(attributes, true)
            .setHandleAudioBecomingNoisy(true)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .build()
            .also { playerIsVideo = video }
    }
}

/**
 * Keep retrying network errors for well over a minute (SPEC §4.3) with backoff, but
 * don't retry a 403: that's the rules saying no, and the next heartbeat explains why.
 */
@UnstableApi
class PatientRetryPolicy : DefaultLoadErrorHandlingPolicy(MIN_RETRIES) {
    override fun getRetryDelayMsFor(info: LoadErrorHandlingPolicy.LoadErrorInfo): Long {
        val error = info.exception
        if (error is HttpDataSource.InvalidResponseCodeException && error.responseCode in 400..499) {
            return C.TIME_UNSET
        }
        return minOf(1_000L shl (info.errorCount - 1).coerceIn(0, 4), 10_000L)
    }

    private companion object {
        const val MIN_RETRIES = 12 // ~1 + 2 + 4 + 8 + 10 * 8 s > 60 s
    }
}
