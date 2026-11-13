package app.kidsmedia.playback

/**
 * Local enforcement and counting for one play session (SPEC §3.3, §4.3).
 *
 * - [playedSecondsTotal] is the cumulative playing time of the session, measured with a
 *   monotonic clock, sent with every heartbeat (it never goes backwards).
 * - The server's `remaining_seconds` becomes a wall-clock deadline: lockouts approach
 *   while paused too, and every resume triggers a fresh heartbeat anyway. When the
 *   deadline passes, playback must stop even if the server is unreachable.
 *
 * @param clock monotonic milliseconds (SystemClock.elapsedRealtime in the app)
 */
class PlayGuard(private val clock: () -> Long) {
    private var playedMs = 0L
    private var playingSince: Long? = null
    private var deadline: Long? = null

    var graceActive: Boolean = false
        private set

    /** A new play session started (new file or new session id). */
    fun reset(remainingSeconds: Int?, grace: Boolean = false) {
        playedMs = 0L
        playingSince = playingSince?.let { clock() }
        update(remainingSeconds, grace)
    }

    fun setPlaying(playing: Boolean) {
        val now = clock()
        val since = playingSince
        if (playing && since == null) {
            playingSince = now
        } else if (!playing && since != null) {
            playedMs += now - since
            playingSince = null
        }
    }

    fun playedSecondsTotal(): Double {
        val running = playingSince?.let { clock() - it } ?: 0L
        return (playedMs + running) / 1000.0
    }

    /** Apply an answer from the server. null = no limit in sight. */
    fun update(remainingSeconds: Int?, grace: Boolean) {
        deadline = remainingSeconds?.let { clock() + it * 1000L }
        graceActive = grace
    }

    /** Seconds until playback must stop, rounded up; null = no limit. */
    fun remainingSeconds(): Long? = deadline?.let { maxOf(0L, (it - clock() + 999) / 1000) }

    fun expired(): Boolean = deadline?.let { clock() >= it } ?: false
}
