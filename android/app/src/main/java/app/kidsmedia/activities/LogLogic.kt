package app.kidsmedia.activities

import app.kidsmedia.data.GemLogEntry
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeParseException

/** Gem changes by a grown-up the child hasn't seen yet: the popup on Home (SPEC §16.6). */
data class GemNews(val entries: List<GemLogEntry>) {
    /** Added minus taken away, over every new change. */
    val total: Int get() = entries.sumOf { it.delta }

    /** The newest change shown; stored as seen when the popup is closed. */
    val lastId: Int get() = entries.maxOf { it.id }
}

/** Which day a log row is from, for its date line ("Today 14:20", "Yesterday 08:10", a date). */
enum class LogDay { TODAY, YESTERDAY, EARLIER }

data class LogTime(val day: LogDay, val at: LocalDateTime)

object LogLogic {
    /**
     * The seen marker for a tablet that has none yet (first start, a new pairing): the newest
     * change there is, so old history never pops up; 0 when there is none yet, so the first
     * gems ever given do.
     */
    fun firstSeen(newsId: Int?): Int = newsId ?: 0

    /** A newer change by a grown-up than the one seen: fetch the log for the popup. */
    fun hasNews(newsId: Int?, seen: Int): Boolean = newsId != null && newsId > seen

    /**
     * The popup's content: every change by a grown-up newer than [seen], oldest first. Spends
     * aren't news: the child just listened, and the balance showed "💎 −2" then (§16.4).
     */
    fun news(entries: List<GemLogEntry>, seen: Int): GemNews? {
        val fresh = entries.filter { it.kind == "admin" && it.id > seen }.sortedBy { it.id }
        return if (fresh.isEmpty()) null else GemNews(fresh)
    }

    /** When [createdAt] (ISO with offset) happened, in [zone], relative to [today]. */
    fun time(createdAt: String, today: LocalDate, zone: ZoneId): LogTime? {
        val at = try {
            OffsetDateTime.parse(createdAt).atZoneSameInstant(zone).toLocalDateTime()
        } catch (_: DateTimeParseException) {
            return null
        }
        val day = when (at.toLocalDate()) {
            today -> LogDay.TODAY
            today.minusDays(1) -> LogDay.YESTERDAY
            else -> LogDay.EARLIER
        }
        return LogTime(day, at)
    }
}
