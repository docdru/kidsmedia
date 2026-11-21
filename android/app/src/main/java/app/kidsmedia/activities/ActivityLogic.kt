package app.kidsmedia.activities

import app.kidsmedia.data.Activities
import app.kidsmedia.data.DoneRequest
import app.kidsmedia.data.StoryChapter
import app.kidsmedia.data.StoryWorld
import app.kidsmedia.data.ThumbRef
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

/** The home tile's badge for the current story world (SPEC §13.7). */
sealed interface ActivityBadge {
    /** The chapter to do now, nothing of it done yet. */
    data class Ready(val position: Int) : ActivityBadge

    /** [left] more ideas in chapter [position] open the next chapter, or finish the story when it is the last. */
    data class MoreToGo(val position: Int, val left: Int, val last: Boolean) : ActivityBadge

    /** The next chapter waits for tomorrow (the daily limit, SPEC §13.2). */
    data object Tomorrow : ActivityBadge

    /** Every chapter of the world is done. */
    data object AllDone : ActivityBadge

    /** Nothing to show yet. */
    data object Waiting : ActivityBadge
}

/**
 * Activities as the UI shows them: unlocks and pending taps applied for [today], and whether
 * Ideas are open at the time it was built (SPEC §14.4a).
 */
data class ActivitiesView(
    val data: Activities,
    val today: LocalDate,
    val badge: ActivityBadge,
    val open: OpenState = OpenState.Open
) {
    val world: StoryWorld? get() = data.world
}

object ActivityLogic {
    /** Task kinds in card order (SPEC §13.1, §14.2). */
    val KINDS = listOf("draw", "calc", "write", "read")

    /** Languages a pack can carry, in fallback order (SPEC §13.1). */
    val LANGUAGES = listOf("en", "de", "el")

    /**
     * The text block for [language] (the app's language if a pack can carry it, else English),
     * falling back to English, then German, then Greek, then to any (SPEC §13.1).
     * Returns the language actually used, so the read-aloud voice matches the text.
     */
    fun <T> pick(texts: Map<String, T>, language: String): Pair<String, T>? {
        val wanted = if (language in LANGUAGES) language else "en"
        for (lang in listOf(wanted) + LANGUAGES) {
            texts[lang]?.let { return lang to it }
        }
        return texts.entries.firstOrNull()?.let { it.key to it.value }
    }

    /**
     * The language [world]'s story is told in (SPEC §14.7): the parent's choice from the server,
     * else [appLanguage] (an older server). Texts are picked in it with [pick]'s fallback.
     */
    fun storyLanguage(world: StoryWorld, appLanguage: String): String =
        world.lang?.takeIf { it in LANGUAGES } ?: appLanguage

    /** Ideas to do in [chapter] to open the next one (SPEC §13.2): the server's number, at most all its tasks. */
    fun needed(chapter: StoryChapter): Int = chapter.needed.coerceAtMost(chapter.tasks.size).coerceAtLeast(1)

    /** Enough ideas of [chapter] are done to open the next chapter (or finish the story). */
    fun finished(chapter: StoryChapter): Boolean = chapter.doneKinds.size >= needed(chapter)

    /** Every idea of [chapter] is done, extras included: it earns its stars (SPEC §14.4). */
    fun complete(chapter: StoryChapter): Boolean =
        chapter.tasks.isNotEmpty() && chapter.tasks.all { it.kind in chapter.doneKinds }

    /** Ideas of [chapter] not done yet, extras included (SPEC §14.4). */
    fun notDone(chapter: StoryChapter): Int = chapter.tasks.count { it.kind !in chapter.doneKinds }

    /** Ideas still to do in [chapter] to open the next one. */
    fun left(chapter: StoryChapter): Int = (needed(chapter) - chapter.doneKinds.size).coerceAtLeast(0)

    /** The local date of [serverTime] (`YYYY-MM-DDTHH:MM…`), or null. */
    fun serverDate(serverTime: String): LocalDate? = runCatching { LocalDate.parse(serverTime.take(10)) }.getOrNull()

    /**
     * Whether a queued done tap goes after the server refused it with HTTP [status] (SPEC §13.3,
     * §14.4a): 404 `unknown_task` or 409 (the plan changed, closed, too old) and 422 (it would fail
     * every time). Only an answer of Kids Media itself counts ([fromServer]); an HTML page from
     * something in front of it says nothing about the tap, which is kept and sent again.
     */
    fun dropsTap(status: Int, fromServer: Boolean): Boolean =
        fromServer && (status == 404 || status == 409 || status == 422)

    /**
     * `done_at` of a tap at [now] (the trusted clock in the server's time zone): ISO-8601 with
     * offset, to the second, e.g. `2026-11-03T18:40:00+01:00` (SPEC §14.4a).
     */
    fun doneAt(now: ZonedDateTime): String =
        now.toOffsetDateTime().truncatedTo(ChronoUnit.SECONDS).format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)

    /**
     * Add a "done" tap to the queue of taps not yet confirmed by the server, once per task.
     * A second tap for the same task (a read task read again) keeps the first one's attempts
     * and time.
     */
    fun mergePending(pending: List<DoneRequest>, request: DoneRequest): List<DoneRequest> =
        if (pending.any { it.sameTask(request) }) pending else pending + request

    private fun DoneRequest.sameTask(other: DoneRequest) =
        worldId == other.worldId && position == other.position && kind == other.kind

    /**
     * The pending taps shown as done, and the chapters they open (SPEC §13.2), so the story moves on
     * offline too: a finished chapter opens the next one, unless the daily limit is reached. The day's
     * count starts from the server's [Activities.openedToday] on the server's day, from 0 on a later
     * day. The next world needs the server: it only starts on the next refresh.
     */
    fun applyLocal(data: Activities, pending: List<DoneRequest>, now: LocalDateTime): Activities {
        val world = data.world ?: return data
        val withTaps = world.chapters.map { c ->
            val extra = pending.filter { it.worldId == world.id && it.position == c.position }.map { it.kind }
            c.copy(doneKinds = (c.doneKinds + extra).distinct())
        }
        var openedToday = if (serverDate(data.serverTime) == now.toLocalDate()) data.openedToday else 0
        val chapters = withTaps.toMutableList()
        for (i in 0 until chapters.size - 1) {
            val c = chapters[i]
            val next = chapters[i + 1]
            if (!c.unlocked || !finished(c)) break
            if (next.unlocked) continue
            val limit = data.maxNewPerDay
            if (limit != null && openedToday >= limit) break
            chapters[i + 1] = next.copy(unlocked = true)
            openedToday++
        }
        return data.copy(world = world.copy(chapters = chapters))
    }

    /** The chapter to do now: the first open one that isn't finished, else the last open one. */
    fun current(world: StoryWorld): StoryChapter? =
        world.chapters.firstOrNull { it.unlocked && !finished(it) } ?: world.chapters.lastOrNull { it.unlocked }

    fun badge(world: StoryWorld): ActivityBadge {
        val chapters = world.chapters
        if (chapters.isNotEmpty() && chapters.all { it.unlocked && finished(it) }) return ActivityBadge.AllDone
        val current = current(world) ?: return ActivityBadge.Waiting
        return when {
            finished(current) -> ActivityBadge.Tomorrow // open and finished, the next one locked
            current.doneKinds.isEmpty() -> ActivityBadge.Ready(current.position)
            else -> ActivityBadge.MoreToGo(current.position, left(current), current == chapters.last())
        }
    }

    fun view(data: Activities, pending: List<DoneRequest>, now: LocalDateTime): ActivitiesView {
        val applied = applyLocal(data, pending, now)
        val today = now.toLocalDate()
        val badge = applied.world?.let { badge(it) } ?: ActivityBadge.Waiting
        return ActivitiesView(applied, today, badge, OpenLogic.openState(data.windows, now))
    }

    /** The chapter to show when the story opens: the one to do now (see [current]). */
    fun firstOpen(world: StoryWorld): Int = current(world)?.position ?: world.chapters.firstOrNull()?.position ?: 1

    /**
     * The picture shown for [chapter] (SPEC §13.5): its own, else the world cover.
     * Null means neither: the app draws the world colour with the kind icons.
     */
    fun picture(world: StoryWorld, chapter: StoryChapter): ThumbRef? = chapter.image ?: world.cover

    /** The chapter the story opens on and the one after it: what is kept for offline use. */
    fun ahead(world: StoryWorld): List<StoryChapter> {
        val current = world.chapters.indexOfFirst { it.position == firstOpen(world) }
        return if (current < 0) emptyList() else world.chapters.drop(current).take(2)
    }

    /**
     * Pictures to load ahead so they show offline: the world cover (the Home tile) and the
     * pictures of the chapter the story opens on and the one after it.
     */
    fun prefetchPictures(world: StoryWorld): List<ThumbRef> =
        (listOfNotNull(world.cover) + ahead(world).mapNotNull { picture(world, it) }).distinct()
}
