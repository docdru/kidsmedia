package app.kidsmedia.activities

import app.kidsmedia.data.Activities
import app.kidsmedia.data.DoneRequest
import app.kidsmedia.data.StoryChapter
import app.kidsmedia.data.StoryWorld
import app.kidsmedia.data.ThumbRef
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.time.temporal.TemporalAdjusters

/** The home tile's badge for the current story world (SPEC §13.7). */
sealed interface ActivityBadge {
    /** Today's chapter is ready, and it's the only one open. */
    data class Ready(val position: Int) : ActivityBadge

    data class SeveralReady(val count: Int) : ActivityBadge

    /** One chapter open, from an earlier day. */
    data object CatchUp : ActivityBadge

    data object DoneToday : ActivityBadge

    /** Nothing open; the next chapter unlocks on [date] (today: later, at the unlock time). */
    data class NextOn(val date: LocalDate) : ActivityBadge

    /** Every chapter of the world is done. */
    data object AllDone : ActivityBadge

    /** Nothing open and no date known for the next chapter. */
    data object Waiting : ActivityBadge
}

/** What a chapter button shows above its number: its weekday, or "next week". */
sealed interface ChapterDay {
    data class Weekday(val day: DayOfWeek) : ChapterDay

    data object NextWeek : ChapterDay

    data object Unknown : ChapterDay
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

    fun parseTime(value: String): LocalTime = runCatching { LocalTime.parse(value) }.getOrDefault(LocalTime.of(7, 0))

    fun dateOf(chapter: StoryChapter): LocalDate? =
        chapter.date?.let { runCatching { LocalDate.parse(it) }.getOrNull() }

    /**
     * Is [chapter] unlocked at [now] (SPEC §13.2/4)? The server's answer counts; offline,
     * the date and unlock time decide, so a chapter still opens on its morning.
     */
    fun unlocked(chapter: StoryChapter, now: LocalDateTime, unlockTime: String): Boolean {
        if (chapter.unlocked) return true
        val date = dateOf(chapter) ?: return false
        val today = now.toLocalDate()
        return date.isBefore(today) || (date == today && !now.toLocalTime().isBefore(parseTime(unlockTime)))
    }

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

    /** Unlocks recomputed for [now], and the pending taps shown as done. */
    fun applyLocal(data: Activities, pending: List<DoneRequest>, now: LocalDateTime): Activities {
        val world = data.world ?: return data
        val chapters = world.chapters.map { c ->
            val extra = pending.filter { it.worldId == world.id && it.position == c.position }.map { it.kind }
            c.copy(
                unlocked = unlocked(c, now, data.unlockTime),
                doneKinds = (c.doneKinds + extra).distinct()
            )
        }
        return data.copy(world = world.copy(chapters = chapters))
    }

    fun badge(world: StoryWorld, today: LocalDate): ActivityBadge {
        val chapters = world.chapters
        if (chapters.isNotEmpty() && chapters.all { it.doneKinds.isNotEmpty() }) return ActivityBadge.AllDone
        val open = chapters.filter { it.unlocked && it.doneKinds.isEmpty() }
        val todays = chapters.firstOrNull { dateOf(it) == today }
        return when {
            open.size > 1 -> ActivityBadge.SeveralReady(open.size)
            open.size == 1 && open[0] == todays -> ActivityBadge.Ready(open[0].position)
            open.size == 1 -> ActivityBadge.CatchUp
            todays != null && todays.unlocked -> ActivityBadge.DoneToday
            else -> chapters.firstOrNull { !it.unlocked }?.let(::dateOf)?.let { ActivityBadge.NextOn(it) }
                ?: ActivityBadge.Waiting
        }
    }

    fun view(data: Activities, pending: List<DoneRequest>, now: LocalDateTime): ActivitiesView {
        val applied = applyLocal(data, pending, now)
        val today = now.toLocalDate()
        val badge = applied.world?.let { badge(it, today) } ?: ActivityBadge.Waiting
        return ActivitiesView(applied, today, badge, OpenLogic.openState(data.windows, now))
    }

    /** The chapter to show when the story opens: the first open one, else the last unlocked. */
    fun firstOpen(world: StoryWorld): Int {
        world.chapters.firstOrNull { it.unlocked && it.doneKinds.isEmpty() }?.let { return it.position }
        return world.chapters.lastOrNull { it.unlocked }?.position ?: world.chapters.firstOrNull()?.position ?: 1
    }

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

    /** Label above a chapter button: its weekday this week, else "next week" (prototype). */
    fun chapterDay(chapter: StoryChapter, today: LocalDate): ChapterDay {
        val date = dateOf(chapter) ?: return ChapterDay.Unknown
        val endOfWeek = today.with(TemporalAdjusters.nextOrSame(DayOfWeek.SUNDAY))
        return if (date.isAfter(endOfWeek)) ChapterDay.NextWeek else ChapterDay.Weekday(date.dayOfWeek)
    }
}
