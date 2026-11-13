package app.kidsmedia.playback

import android.net.Uri
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import app.kidsmedia.data.ApiException
import app.kidsmedia.data.HeartbeatRequest
import app.kidsmedia.data.KidsApi
import app.kidsmedia.data.LibraryGroup
import app.kidsmedia.data.LibraryItem
import app.kidsmedia.data.OfflineEvent
import app.kidsmedia.data.Params
import app.kidsmedia.offline.OfflineStore
import app.kidsmedia.rules.Decision
import app.kidsmedia.rules.RulesEngine
import java.io.IOException
import java.time.OffsetDateTime
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonPrimitive

/** Why playback can't start or had to stop: a server reason code plus params (SPEC §3.2). */
data class Block(val reason: String, val params: Params = emptyMap())

data class NowPlaying(val group: LibraryGroup, val item: LibraryItem, val remainingSeconds: Long?, val grace: Boolean)

/** Provided by [PlaybackService]: a player configured for audio or video buffering. */
interface PlayerHost {
    fun playerFor(video: Boolean): Player
}

/**
 * Coordinates the server rules with the player (SPEC §5.3):
 * every new file goes through `/play/start` (auto-advance included), heartbeats every
 * 15 s while playing, a local countdown that stops playback even offline, and the
 * reason screen when the server says no.
 *
 * All methods run on the main thread.
 */
class PlaybackController(
    private val api: KidsApi,
    private val scope: CoroutineScope,
    private val clock: () -> Long,
    private val store: OfflineStore? = null
) {
    private val _nowPlaying = MutableStateFlow<NowPlaying?>(null)
    val nowPlaying: StateFlow<NowPlaying?> = _nowPlaying.asStateFlow()

    private val _block = MutableStateFlow<Block?>(null)
    val block: StateFlow<Block?> = _block.asStateFlow()

    private val _offline = MutableStateFlow(false)
    val offline: StateFlow<Boolean> = _offline.asStateFlow()

    private val _player = MutableStateFlow<Player?>(null)
    val player: StateFlow<Player?> = _player.asStateFlow()

    private var host: PlayerHost? = null
    private var group: LibraryGroup? = null
    private var item: LibraryItem? = null
    private var sessionId: String? = null
    private val guard = PlayGuard(clock)
    private var lastBeatAt = 0L
    private var beating = false
    private var forceQueued = false
    private var lastPositionMs = 0L

    // Offline session (M7): rules are evaluated locally and the play is queued for the server.
    private var offlineSession = false
    private var sessionStartedAt: OffsetDateTime? = null
    private var graceUsed = false
    private var graceRuleIds: Set<Int> = emptySet()

    private val listener =
        object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                guard.setPlaying(isPlaying)
                if (isPlaying) scope.launch { beat() } // refresh the deadline on every resume
                publish()
            }

            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                val id = mediaItem?.mediaId?.toIntOrNull() ?: return
                if (id != item?.id &&
                    (
                        reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO ||
                            reason == Player.MEDIA_ITEM_TRANSITION_REASON_SEEK
                        )
                ) {
                    val ended = reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO
                    scope.launch { advanceTo(id, ended) }
                }
            }
        }

    fun attach(host: PlayerHost) {
        this.host = host
    }

    fun detach() {
        _player.value?.removeListener(listener)
        _player.value = null
        host = null
    }

    fun dismissBlock() {
        _block.value = null
    }

    fun showBlock(block: Block) {
        _block.value = block
    }

    /** Start [index] of [group] and queue the following items. */
    suspend fun play(group: LibraryGroup, index: Int): Boolean {
        val target = group.items.getOrNull(index) ?: return false
        flushAndWait(_player.value?.currentPosition ?: lastPositionMs)
        val answer =
            try {
                api.start(target.id)
            } catch (e: ApiException) {
                _offline.value = false
                _block.value = Block(e.reason, e.params)
                return false
            } catch (e: IOException) {
                _offline.value = true
                return playOffline(group, index, target)
            }
        _offline.value = false
        if (!answer.allowed) {
            _block.value = Block(answer.reason ?: "not_allowed", answer.params)
            return false
        }
        val p = preparePlayer(group) ?: return false
        endSession()
        this.group = group
        this.item = target
        sessionId = answer.playSessionId
        sessionStartedAt = store?.now() ?: OffsetDateTime.now()
        guard.reset(answer.remainingSeconds, answer.grace.active)
        lastBeatAt = clock()
        p.setMediaItems(group.items.drop(index).map { mediaItem(group, it) }, 0, answer.positionMs)
        p.prepare()
        p.play()
        publish()
        return true
    }

    fun togglePlay() {
        val p = _player.value ?: return
        if (p.isPlaying) {
            p.pause()
        } else {
            if (guard.expired()) {
                scope.launch { beat(force = true) }
                return
            }
            p.play()
        }
    }

    /** Pause for Listen (SPEC §14.3): the voice and media never play at once. */
    fun pause() {
        _player.value?.takeIf { it.isPlaying }?.pause()
    }

    fun seekBy(deltaMs: Long) {
        val p = _player.value ?: return
        seekTo(p.currentPosition + deltaMs)
    }

    /** Jump to an absolute position, e.g. from the seek bar. Grace bookkeeping follows via heartbeats (§3.3a). */
    fun seekTo(positionMs: Long) {
        val p = _player.value ?: return
        val max = p.duration.takeIf { it > 0 } ?: Long.MAX_VALUE
        p.seekTo(positionMs.coerceIn(0L, max))
    }

    fun stop() {
        flushSession(_player.value?.currentPosition ?: lastPositionMs)
        endSession()
        _player.value?.stop()
        sessionId = null
        _nowPlaying.value = null
    }

    /** Called every second by the service while it runs. */
    fun tick() {
        val p = _player.value ?: return
        if (p.isPlaying) {
            lastPositionMs = p.currentPosition
            if (guard.expired()) {
                p.pause() // local enforcement, also when offline (SPEC §4.3)
                scope.launch { beat(force = true) }
            } else if (clock() - lastBeatAt >= HEARTBEAT_MS) {
                scope.launch { beat() }
            }
        }
        publish()
    }

    /** [ended]: the previous item played to its end (an automatic transition). */
    private suspend fun advanceTo(mediaId: Int, ended: Boolean) {
        val g = group ?: return
        val next = g.items.firstOrNull { it.id == mediaId } ?: return
        val p = _player.value ?: return
        p.pause()
        val previous = item
        flushAndWait(if (ended && previous != null) (previous.durationS * 1000).toLong() else lastPositionMs)
        val answer =
            try {
                api.start(next.id)
            } catch (e: ApiException) {
                _block.value = Block(e.reason, e.params)
                return
            } catch (e: IOException) {
                _offline.value = true
                advanceOffline(g, next)
                return
            }
        if (!answer.allowed) {
            _block.value = Block(answer.reason ?: "not_allowed", answer.params)
            return
        }
        endSession()
        item = next
        sessionId = answer.playSessionId
        sessionStartedAt = store?.now() ?: OffsetDateTime.now()
        guard.reset(answer.remainingSeconds, answer.grace.active)
        lastBeatAt = clock()
        if (answer.positionMs > 0) p.seekTo(answer.positionMs)
        p.play()
        publish()
    }

    private suspend fun beat(force: Boolean = false) {
        if (beating) {
            // A forced beat (the countdown hit zero) must not get lost behind one in flight,
            // or the player stays paused without a reason screen (review finding #7).
            if (force) forceQueued = true
            return
        }
        val p = _player.value ?: return
        val current = item ?: return
        val session = sessionId ?: return
        if (!force && !p.isPlaying) return
        if (offlineSession) {
            localBeat(p, current, force)
            return
        }
        beating = true
        lastBeatAt = clock()
        try {
            val answer =
                api.heartbeat(
                    HeartbeatRequest(
                        current.id,
                        session,
                        guard.playedSecondsTotal(),
                        p.currentPosition.coerceAtLeast(0)
                    )
                )
            _offline.value = false
            if (!answer.allowed) {
                p.pause()
                guard.update(0, false)
                _block.value = Block(answer.reason ?: "not_allowed", answer.params)
            } else {
                guard.update(answer.remainingSeconds, answer.grace.active)
                if (force && !p.isPlaying && _block.value == null) p.play()
            }
        } catch (e: ApiException) {
            if (e.status == 404 && e.reason == "unknown_session") {
                restartSession(current)
            } else if (guard.expired()) {
                _block.value = Block(e.reason, e.params)
            }
        } catch (e: IOException) {
            _offline.value = true
            if (guard.expired()) _block.value = offlineStop(current) // offline and out of time
        } finally {
            beating = false
            publish()
            if (forceQueued) {
                forceQueued = false
                scope.launch { beat(force = true) }
            }
        }
    }

    /**
     * SPEC §3.3: the last heartbeat of an online session before it's replaced or stopped,
     * so the playing time since the previous heartbeat (up to 15 s per file) is counted.
     * The answer is ignored: it's about the file that is ending, and the next file gets
     * its own `/play/start`. Values are captured now, before the session is replaced.
     */
    private fun flushSession(positionMs: Long): Job? {
        if (offlineSession) return null
        val current = item ?: return null
        val session = sessionId ?: return null
        val request = HeartbeatRequest(current.id, session, guard.playedSecondsTotal(), positionMs.coerceAtLeast(0))
        return scope.launch {
            try {
                api.heartbeat(request)
            } catch (ignored: IOException) {
                // Best effort: if it doesn't arrive, these few seconds stay uncounted.
            }
        }
    }

    /** Flush before the next `/play/start`, so its minute check sees the time just played. */
    private suspend fun flushAndWait(positionMs: Long) {
        val job = flushSession(positionMs) ?: return
        withTimeoutOrNull(FLUSH_TIMEOUT_MS) { job.join() }
    }

    /** SPEC §3.3: an unknown or pruned session -> start again (re-evaluates the rules). */
    private suspend fun restartSession(current: LibraryItem) {
        val p = _player.value ?: return
        val answer =
            try {
                api.start(current.id)
            } catch (e: IOException) {
                return
            }
        if (!answer.allowed) {
            p.pause()
            _block.value = Block(answer.reason ?: "not_allowed", answer.params)
            return
        }
        sessionId = answer.playSessionId
        guard.reset(answer.remainingSeconds, answer.grace.active)
    }

    private fun preparePlayer(group: LibraryGroup): Player? {
        val p = host?.playerFor(group.isVideo) ?: return null
        if (_player.value !== p) {
            _player.value?.removeListener(listener)
            p.addListener(listener)
            _player.value = p
        }
        return p
    }

    /** No server: play the downloaded copy if the local rules engine allows it (M7). */
    private fun playOffline(group: LibraryGroup, index: Int, target: LibraryItem): Boolean {
        val store = this.store
        if (store == null || store.localFile(target.id) == null) {
            _block.value = Block("offline")
            return false
        }
        val decision = store.decide(group.id, target.id)
        if (decision == null) {
            _block.value = Block("offline")
            return false
        }
        if (!decision.allowed) {
            _block.value = Block(decision.reason?.wire ?: "not_allowed", paramsOf(decision.params))
            return false
        }
        val p = preparePlayer(group) ?: return false
        endSession()
        this.group = group
        this.item = target
        startOfflineSession(decision)
        // Only downloaded items are queued: the rest can't play without a connection.
        val queue = group.items.drop(index).filter { store.localFile(it.id) != null }
        p.setMediaItems(queue.map { mediaItem(group, it) }, 0, target.positionMs)
        p.prepare()
        p.play()
        publish()
        return true
    }

    private fun advanceOffline(group: LibraryGroup, next: LibraryItem) {
        val p = _player.value ?: return
        val decision = store?.takeIf { it.localFile(next.id) != null }?.decide(group.id, next.id)
        if (decision == null || !decision.allowed) {
            _block.value =
                Block(decision?.reason?.wire ?: "offline", decision?.let { paramsOf(it.params) } ?: emptyMap())
            return
        }
        endSession()
        item = next
        startOfflineSession(decision)
        p.play()
        publish()
    }

    private fun startOfflineSession(decision: Decision) {
        offlineSession = true
        graceUsed = false
        graceRuleIds = emptySet()
        sessionId = "offline-${UUID.randomUUID()}"
        sessionStartedAt = store?.now() ?: OffsetDateTime.now()
        guard.reset(decision.remainingSeconds?.toInt(), false)
        lastBeatAt = clock()
    }

    /**
     * The heartbeat's job offline: re-evaluate locally, with grace (§3.3a) once per session.
     * A running grace covers only the rules that granted it; a rule that expires later
     * must grant its own grace (RulesEngine.extendGrace, like the server).
     */
    private fun localBeat(p: Player, current: LibraryItem, force: Boolean) {
        lastBeatAt = clock()
        val g = group ?: return
        val decision = store?.decide(g.id, current.id, guard.playedSecondsTotal(), sessionStartedAt) ?: return
        val itemLeft = (current.durationS - p.currentPosition / 1000.0).coerceAtLeast(0.0)
        when {
            decision.allowed -> {
                if (!guard.graceActive) guard.update(decision.remainingSeconds?.toInt(), false)
                if (force && !p.isPlaying && _block.value == null) p.play()
            }
            guard.graceActive && !guard.expired() -> {
                val extended = RulesEngine.extendGrace(decision, itemLeft, graceRuleIds)
                val added = decision.denials.filter { it.ruleId !in graceRuleIds }
                if (extended == null) {
                    stopLocally(p, decision)
                } else if (added.isNotEmpty()) {
                    graceRuleIds = extended
                    val cap = added.minOf { it.graceMinutes } * 120L // 2 x grace_minutes
                    guard.update(minOf(guard.remainingSeconds() ?: cap, cap).toInt(), true)
                }
            }
            else -> {
                val grace = if (graceUsed) null else RulesEngine.graceRule(decision, itemLeft)
                if (grace != null) {
                    graceUsed = true
                    graceRuleIds = decision.denials.map { it.ruleId }.toSet()
                    guard.update(minOf(itemLeft, grace.graceMinutes * 120.0).toInt() + 1, true)
                } else {
                    stopLocally(p, decision)
                }
            }
        }
        publish()
    }

    /**
     * The countdown ran out while the server can't be reached. The server didn't say why,
     * so ask the local rules (the offline snapshot) for the real reason; without one, show
     * a neutral "break" rather than guessing "time's up" (review finding #8).
     */
    private fun offlineStop(current: LibraryItem): Block {
        val g = group
        val decision = g?.let { store?.decide(it.id, current.id, 0.0, sessionStartedAt) }
        val reason = decision?.takeIf { !it.allowed }?.reason
        return if (decision != null && reason != null) {
            Block(reason.wire, paramsOf(decision.params))
        } else {
            Block(PAUSE_REASON)
        }
    }

    private fun stopLocally(p: Player, decision: Decision) {
        p.pause()
        guard.update(0, false)
        graceRuleIds = emptySet()
        _block.value = Block(decision.reason?.wire ?: "not_allowed", paramsOf(decision.params))
    }

    /** Queue the finished offline play for /api/play/batch (online plays count via heartbeats). */
    private fun endSession() {
        val store = this.store
        val current = item
        val g = group
        val started = sessionStartedAt
        if (offlineSession && store != null && current != null && g != null && started != null) {
            val event =
                OfflineEvent(
                    eventId = UUID.randomUUID().toString(),
                    mediaId = current.id,
                    groupId = g.id,
                    startedAt = started.toString(),
                    seconds = guard.playedSecondsTotal(),
                    positionMs = _player.value?.currentPosition?.coerceAtLeast(0) ?: 0
                )
            scope.launch { store.record(event) }
        }
        offlineSession = false
        sessionStartedAt = null
    }

    private fun paramsOf(params: Map<String, Any?>): Params = params.mapValues { (_, v) ->
        when (v) {
            null -> null
            is Number -> JsonPrimitive(v)
            else -> JsonPrimitive(v.toString())
        }
    }

    private fun publish() {
        val g = group
        val i = item
        _nowPlaying.value =
            if (g == null || i == null || sessionId == null) {
                null
            } else {
                NowPlaying(g, i, guard.remainingSeconds(), guard.graceActive)
            }
    }

    private fun mediaItem(group: LibraryGroup, item: LibraryItem): MediaItem {
        val metadata =
            MediaMetadata
                .Builder()
                .setTitle(item.title)
                .setArtist(group.name)
                .setArtworkUri((item.thumb ?: group.cover)?.let { Uri.parse(api.url(it.url) + "?size=lg") })
                .build()
        return MediaItem
            .Builder()
            .setMediaId(item.id.toString())
            // A downloaded copy is used online too: it saves data, and heartbeats still count.
            .setUri(
                store?.localFile(item.id)?.let { Uri.fromFile(it) }
                    ?: Uri.parse(api.url("/api/media/${item.id}/stream"))
            )
            .setMediaMetadata(metadata)
            .build()
    }

    companion object {
        const val HEARTBEAT_MS = 15_000L
        const val FLUSH_TIMEOUT_MS = 3_000L

        /** App-side reason (never from the server): stopped offline for an unknown reason. */
        const val PAUSE_REASON = "pause"
    }
}
