package app.kidsmedia.activities

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy
import androidx.media3.exoplayer.upstream.LoadErrorHandlingPolicy
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.OkHttpClient

/**
 * "Listen" (SPEC §13.7, §14.3): plays a sequence of [Clip]s, each from the server's rendered
 * speech (the offline copy first) or, without one or when it fails to load, with the tablet's own
 * voice in the text's language. [speaking] is the key of what is being read, so a screen can show
 * its button as playing. [resolve] picks each clip's source; [beforeSpeaking] pauses media
 * playback, so the two never play at once.
 */
@OptIn(UnstableApi::class)
class Speaker(
    context: Context,
    http: OkHttpClient,
    private val resolve: (Clip) -> ClipSource,
    private val beforeSpeaking: () -> Unit
) {
    private val _speaking = MutableStateFlow<String?>(null)
    val speaking: StateFlow<String?> = _speaking.asStateFlow()

    private val main = Handler(Looper.getMainLooper())

    /** Null while the engine starts. */
    private var ready: Boolean? = null
    private var clips: List<Clip> = emptyList()
    private var index = 0
    private var language = "en"

    /** Id of the utterance being spoken; callbacks for any other are stale. */
    private var utterance: String? = null
    private var serial = 0
    private var waitingForTts = false
    private var playingAudio = false

    private val tts: TextToSpeech =
        TextToSpeech(context.applicationContext) { status ->
            main.post {
                ready = status == TextToSpeech.SUCCESS
                if (ready != true) Log.w("kidsmedia", "text to speech unavailable: $status")
                if (waitingForTts) {
                    waitingForTts = false
                    playCurrent()
                }
            }
        }

    private val player: ExoPlayer = buildPlayer(context.applicationContext, http)

    init {
        tts.setOnUtteranceProgressListener(
            object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = Unit

                override fun onDone(utteranceId: String?) = finished(utteranceId)

                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) = finished(utteranceId)

                override fun onError(utteranceId: String?, errorCode: Int) = finished(utteranceId)
            }
        )
        player.addListener(
            object : Player.Listener {
                override fun onPlaybackStateChanged(playbackState: Int) {
                    if (playbackState == Player.STATE_ENDED && playingAudio) {
                        playingAudio = false
                        next()
                    }
                }

                override fun onPlayerError(error: PlaybackException) {
                    if (!playingAudio) return
                    playingAudio = false
                    Log.w("kidsmedia", "speech audio failed, using text to speech: $error")
                    clips.getOrNull(index)?.let { say(it.text) } ?: finish()
                }
            }
        )
    }

    /** TextToSpeech calls back on its own thread. */
    private fun finished(utteranceId: String?) {
        main.post {
            if (utteranceId != null && utteranceId == utterance) {
                utterance = null
                next()
            }
        }
    }

    /** Start reading [clips], or stop if [key] is already being read. */
    fun toggle(key: String, clips: List<Clip>, language: String) {
        if (_speaking.value == key) stop() else speak(key, clips, language)
    }

    fun speak(key: String, clips: List<Clip>, language: String) {
        stop()
        val shown = clips.filter { it.text.isNotBlank() }
        if (shown.isEmpty()) return
        beforeSpeaking()
        _speaking.value = key
        this.clips = shown
        this.language = language
        index = 0
        playCurrent()
    }

    private fun playCurrent() {
        val clip = clips.getOrNull(index) ?: return finish()
        when (val source = resolve(clip)) {
            is ClipSource.Cached -> playAudio(Uri.fromFile(source.file).toString())
            is ClipSource.Remote -> playAudio(source.url)
            is ClipSource.Speech -> say(source.text)
        }
    }

    private fun playAudio(uri: String) {
        playingAudio = true
        player.setMediaItem(MediaItem.fromUri(uri))
        player.prepare()
        player.play()
    }

    private fun say(text: String) {
        when (ready) {
            null -> waitingForTts = true
            false -> next()
            true -> {
                val id = "speech-${++serial}"
                utterance = id
                tts.language = Locale.forLanguageTag(language)
                tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, id)
            }
        }
    }

    private fun next() {
        if (_speaking.value == null) return
        index++
        playCurrent()
    }

    private fun finish() {
        clips = emptyList()
        _speaking.value = null
    }

    fun stop() {
        waitingForTts = false
        utterance = null
        playingAudio = false
        finish()
        player.stop()
        player.clearMediaItems()
        if (ready == true) tts.stop()
    }

    fun shutdown() {
        stop()
        tts.shutdown()
        player.release()
    }

    private companion object {
        /** Short clips: give up quickly and let TextToSpeech read the clip instead. */
        fun buildPlayer(context: Context, http: OkHttpClient): ExoPlayer {
            val client = http.newBuilder().connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build()
            val sources =
                DefaultMediaSourceFactory(context)
                    // DefaultDataSource: http(s) through OkHttp (auth header), file:// for the offline copies.
                    .setDataSourceFactory(DefaultDataSource.Factory(context, OkHttpDataSource.Factory(client)))
                    .setLoadErrorHandlingPolicy(NoRetryPolicy())
            val attributes =
                AudioAttributes
                    .Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_SPEECH)
                    .build()
            // No audio focus, like the TextToSpeech voice: media playback is paused before speaking instead.
            return ExoPlayer
                .Builder(context)
                .setMediaSourceFactory(sources)
                .setAudioAttributes(attributes, false)
                .build()
        }
    }
}

/** A failed clip falls back to TextToSpeech at once instead of retrying. */
@OptIn(UnstableApi::class)
private class NoRetryPolicy : DefaultLoadErrorHandlingPolicy() {
    override fun getRetryDelayMsFor(info: LoadErrorHandlingPolicy.LoadErrorInfo): Long = C.TIME_UNSET
}
