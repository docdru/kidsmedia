package app.kidsmedia.offline

import android.content.Context
import android.util.Log
import app.kidsmedia.data.ApiJson
import app.kidsmedia.data.BatchRequest
import app.kidsmedia.data.KidsApi
import app.kidsmedia.data.Library
import app.kidsmedia.data.OfflineEvent
import app.kidsmedia.data.Snapshot
import app.kidsmedia.data.SnapshotRule
import app.kidsmedia.data.SnapshotUsage
import app.kidsmedia.rules.Decision
import app.kidsmedia.rules.Rule
import app.kidsmedia.rules.RulesEngine
import app.kidsmedia.rules.Usage
import app.kidsmedia.rules.Window
import java.io.File
import java.io.IOException
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import okhttp3.OkHttpClient
import okhttp3.Request

/** Gems this tablet saw spent offline (SPEC §16.2): [gems] for [ruleId], [mediaId] on [day]. */
@Serializable
data class GemSpend(
    @SerialName("rule_id") val ruleId: Int,
    @SerialName("media_id") val mediaId: Int,
    val day: String,
    val gems: Int
)

/**
 * Offline mode (SPEC §11 M7). Keeps, in app-private storage:
 * - copies of the items the parent marked "available offline",
 * - the last library (so Home works without a connection),
 * - the rules snapshot, evaluated locally with the Kotlin port of the engine,
 * - plays made while offline, sent to `/api/play/batch` when the server is back,
 * - the gems those plays spent (SPEC §16.2), until a fresh snapshot carries the server's balance.
 */
class OfflineStore(
    private val context: Context,
    private val http: OkHttpClient,
    private val api: KidsApi,
    private val clock: TrustedClock = TrustedClock.system(context)
) {
    private val dir = File(context.filesDir, "offline").apply { mkdirs() }
    private val mediaDir = File(dir, "media").apply { mkdirs() }
    private val lock = Mutex()
    private val spendLock = Any()

    // ---- persistence ----------------------------------------------------------------

    private fun <T> read(name: String, serializer: KSerializer<T>): T? =
        runCatching { ApiJson.decodeFromString(serializer, File(dir, name).readText()) }.getOrNull()

    private fun <T> write(name: String, serializer: KSerializer<T>, value: T) {
        val tmp = File(dir, "$name.tmp")
        tmp.writeText(ApiJson.encodeToString(serializer, value))
        tmp.renameTo(File(dir, name))
    }

    fun cachedLibrary(): Library? = read(LIBRARY, Library.serializer())

    fun snapshot(): Snapshot? = read(SNAPSHOT, Snapshot.serializer())

    /** "Now" for offline rules and plays: the server's clock carried forward (TrustedClock). */
    fun now(): OffsetDateTime {
        val ms = clock.nowMs(read(CLOCK, ClockAnchor.serializer()))
        return OffsetDateTime.ofInstant(Instant.ofEpochMilli(ms), ZoneId.systemDefault())
    }

    /**
     * The server's time zone (SPEC §14.4a): opening times, chapter unlocks and days are the
     * server's local times, not the tablet's. See [serverZone].
     */
    fun zone(serverTime: String? = null): ZoneId = serverZone(snapshot()?.timeZone, serverTime, ZoneId.systemDefault())

    fun pendingEvents(): List<OfflineEvent> = read(EVENTS, ListSerializer(OfflineEvent.serializer())) ?: emptyList()

    fun localFile(mediaId: Int): File? = File(mediaDir, "$mediaId").takeIf { it.isFile }

    fun gemSpends(): List<GemSpend> = read(GEM_SPENDS, ListSerializer(GemSpend.serializer())) ?: emptyList()

    /** The gem balance offline: the snapshot's minus the gems spent since (SPEC §16.2). Null without a snapshot. */
    fun gemBalance(): Int? = snapshot()?.let { balance(it.usage.gemBalance, gemSpends()) }

    // ---- while offline ---------------------------------------------------------------

    /**
     * Evaluate locally (same engine as the server). Null when there's no snapshot yet.
     * [sessionStartedAt]: for a file that is already playing, when its session started.
     * The session belongs to that day (§3.3): its usage and limits, also after midnight.
     */
    fun decide(
        groupId: Int,
        mediaId: Int?,
        sessionSeconds: Double = 0.0,
        sessionStartedAt: OffsetDateTime? = null
    ): Decision? {
        val snap = snapshot() ?: return null
        val zone = serverZone(snap.timeZone, null, ZoneId.systemDefault())
        val now = this.now().atZoneSameInstant(zone)
        val today = now.toLocalDate()
        val day = sessionStartedAt?.atZoneSameInstant(zone)?.toLocalDate() ?: today
        // Lengths for the listening budget (§3.3). sync() stores the library before the
        // snapshot, so with a snapshot there is a library; without lengths, no budget.
        val durations = cachedLibrary()?.groups?.flatMap { g -> g.items.map { it.id to it.durationS } }?.toMap()
        val rules = rulesOf(snap.rules)
        val merged = mergeUsage(snap.usage, pendingEvents(), day, zone, mediaId, groupId, sessionSeconds)
        // Gems are spent when a file counts, before the evaluation that sees it (§16.2).
        val spends = noteGemSpends(snap, rules, merged, day)
        val usage =
            merged.copy(
                durations = durations ?: emptyMap(),
                gems = balance(snap.usage.gemBalance, spends),
                gemsPaid = paidOn(snap.usage, spends, day)
            )
        val limitsDay = if (day != today) day.dayOfWeek.value - 1 else null
        return RulesEngine.evaluate(
            rules,
            usage,
            snap.groupIds.toSet(),
            groupId,
            mediaId,
            now,
            limitsDay = limitsDay
        )
    }

    /** Remember the gems spent by plays that count now and didn't before; returns all spends known. */
    private fun noteGemSpends(snap: Snapshot, rules: List<Rule>, usage: Usage, day: LocalDate): List<GemSpend> =
        synchronized(spendLock) {
            val known = gemSpends()
            val fresh = newGemSpends(rules, snap.usage, usage, day, known)
            if (fresh.isEmpty()) {
                known
            } else {
                (known + fresh).also { write(GEM_SPENDS, ListSerializer(GemSpend.serializer()), it) }
            }
        }

    /** Queue one finished offline play for the server. */
    suspend fun record(event: OfflineEvent) {
        if (event.seconds <= 0.0) return
        lock.withLock {
            withContext(Dispatchers.IO) {
                write(EVENTS, ListSerializer(OfflineEvent.serializer()), pendingEvents() + event)
            }
        }
    }

    // ---- when online -------------------------------------------------------------------

    /**
     * After a successful library refresh: send queued plays, refresh the snapshot, fetch
     * missing copies and delete the ones no longer marked offline.
     */
    suspend fun sync(library: Library) = lock.withLock {
        withContext(Dispatchers.IO) {
            runCatching { OffsetDateTime.parse(library.serverTime).toInstant().toEpochMilli() }
                .onSuccess { write(CLOCK, ClockAnchor.serializer(), clock.anchor(it)) }
            write(LIBRARY, Library.serializer(), library)
            val pending = pendingEvents()
            if (pending.isNotEmpty()) {
                api.batch(BatchRequest(pending)) // de-duplicated by event_id on the server
                val sent = pending.map { it.eventId }.toSet()
                write(
                    EVENTS,
                    ListSerializer(OfflineEvent.serializer()),
                    pendingEvents().filter {
                        it.eventId !in
                            sent
                    }
                )
            }
            val fresh = api.snapshot()
            synchronized(spendLock) {
                write(SNAPSHOT, Snapshot.serializer(), fresh)
                // The fresh balance includes what the batch booked (§16.2).
                File(dir, GEM_SPENDS).delete()
            }
            val wanted = library.groups.flatMap { g ->
                g.items.filter { it.offline }.map { it.id to it.size }
            }.toMap()
            mediaDir.listFiles()?.forEach { f -> if (f.name.toIntOrNull() !in wanted.keys) f.delete() }
            for ((id, size) in wanted) {
                val existing = localFile(id)
                if (existing == null || existing.length() != size) download(id)
            }
        }
    }

    private fun download(mediaId: Int) {
        val tmp = File(mediaDir, "$mediaId.part")
        try {
            http.newCall(Request.Builder().url(api.url("/api/media/$mediaId/download")).build()).execute().use { r ->
                if (!r.isSuccessful) return
                tmp.outputStream().use { out -> r.body?.byteStream()?.copyTo(out) }
            }
            tmp.renameTo(File(mediaDir, "$mediaId"))
        } catch (e: IOException) {
            Log.w("kidsmedia", "download of $mediaId failed: $e")
        } finally {
            tmp.delete()
        }
    }

    companion object {
        private const val LIBRARY = "library.json"
        private const val SNAPSHOT = "snapshot.json"
        private const val EVENTS = "events.json"
        private const val CLOCK = "clock.json"
        private const val GEM_SPENDS = "gem_spends.json"

        /**
         * The server's zone: the named [timeZone] of the snapshot (DST-correct), else the
         * offset of a [serverTime] the server sent (e.g. the activities' `server_time`), else
         * [fallback] (the tablet's zone, before the server was ever reached).
         */
        fun serverZone(timeZone: String?, serverTime: String?, fallback: ZoneId): ZoneId =
            timeZone?.takeIf { it.isNotBlank() }?.let { runCatching { ZoneId.of(it) }.getOrNull() }
                ?: serverTime?.let { runCatching { OffsetDateTime.parse(it).offset }.getOrNull() }
                ?: fallback

        fun rulesOf(rules: List<SnapshotRule>): List<Rule> = rules.map { r ->
            Rule(
                id = r.id,
                days = r.days.toSet(),
                allGroups = r.allGroups,
                groupIds = r.groupIds.toSet(),
                block = r.mode == "block",
                maxItems = r.maxItems,
                maxMinutes = r.maxMinutes,
                lockouts = r.lockouts.map { Window.parse(it.start, it.end) },
                graceMinutes = r.graceMinutes,
                countAfterSeconds = r.countAfterSeconds,
                replayPercent = r.replayPercent,
                gemCost = r.gemCost,
                messageEn = r.messageEn,
                messageDe = r.messageDe,
                messageEl = r.messageEl
            )
        }

        /** Snapshot balance minus the gems spent offline since; a spend never goes below 0 (§16.1). */
        fun balance(snapshotBalance: Int, spends: List<GemSpend>): Int =
            (snapshotBalance - spends.sumOf { it.gems }).coerceAtLeast(0)

        /**
         * Rule id -> the files paid on [day]: the server's `gems_paid` (if the snapshot is from
         * that day) plus the spends this tablet saw offline.
         */
        fun paidOn(snap: SnapshotUsage, spends: List<GemSpend>, day: LocalDate): Map<Int, Set<Int>> {
            val key = day.toString()
            val paid = mutableMapOf<Int, MutableSet<Int>>()
            if (snap.day == key) snap.gemsPaid.forEach { (rule, ids) -> paid.getOrPut(rule) { mutableSetOf() } += ids }
            spends.filter { it.day == key }.forEach { paid.getOrPut(it.ruleId) { mutableSetOf() } += it.mediaId }
            return paid
        }

        /**
         * The gem spends (§16.2) due on [day]: files that count in [usage] (snapshot + offline
         * plays) for a gem rule and aren't paid yet, neither on the server nor in [known].
         */
        fun newGemSpends(
            rules: List<Rule>,
            snap: SnapshotUsage,
            usage: Usage,
            day: LocalDate,
            known: List<GemSpend>
        ): List<GemSpend> {
            val paid = usage.copy(gemsPaid = paidOn(snap, known, day))
            return RulesEngine.dueGemSpends(rules, paid, day.dayOfWeek.value - 1)
                .map { (k, gems) -> GemSpend(k.first, k.second, day.toString(), gems) }
        }

        /**
         * Today's usage = the server's snapshot (if it is from today) + queued offline plays
         * of today + the session that is running now (not yet queued).
         */
        fun mergeUsage(
            snap: SnapshotUsage,
            events: List<OfflineEvent>,
            today: LocalDate,
            zone: ZoneId,
            mediaId: Int?,
            groupId: Int,
            sessionSeconds: Double
        ): Usage {
            val fromToday = snap.day == today.toString()
            val started = if (fromToday) snap.started.toMutableMap() else mutableMapOf()
            val seconds = if (fromToday) snap.seconds.toMutableMap() else mutableMapOf()
            val played = if (fromToday) snap.played.toMutableMap() else mutableMapOf()
            for (e in events) {
                val day = runCatching {
                    OffsetDateTime.parse(e.startedAt).atZoneSameInstant(zone).toLocalDate()
                }.getOrNull()
                if (day != today) continue
                started[e.mediaId] = e.groupId
                played[e.mediaId] = (played[e.mediaId] ?: 0.0) + e.seconds
                seconds[e.groupId] = (seconds[e.groupId] ?: 0.0) + e.seconds
            }
            if (sessionSeconds > 0 && mediaId != null) {
                started[mediaId] = groupId
                played[mediaId] = (played[mediaId] ?: 0.0) + sessionSeconds
                seconds[groupId] = (seconds[groupId] ?: 0.0) + sessionSeconds
            }
            return Usage(
                started = started,
                seconds = seconds,
                played = played,
                extraItems = if (fromToday) snap.extraItems else emptyMap(),
                extraMinutes = if (fromToday) snap.extraMinutes else emptyMap()
            )
        }
    }
}
