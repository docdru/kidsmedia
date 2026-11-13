package app.kidsmedia.activities

import app.kidsmedia.data.OpenWindow
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

/** Whether Ideas are open (SPEC §14.4a). */
sealed interface OpenState {
    data object Open : OpenState

    /** Closed until [opensAt]; null when no window ever opens. */
    data class Closed(val opensAt: LocalDateTime?) : OpenState
}

/**
 * Opening times for Ideas (SPEC §14.4a), evaluated on the tablet with its trusted clock, also
 * offline. A window is open from `from` (inclusive) to `to` (exclusive) on the weekdays in
 * `days` (0 = Monday ... 6 = Sunday). No windows = always open.
 */
object OpenLogic {
    /** "I'm done!" is still accepted this long after a window closed (§14.4a). */
    const val GRACE_MINUTES = 15L

    /** A window with parsed times; null if it doesn't parse or is empty (from ≥ to). */
    private data class Span(val days: Set<Int>, val from: LocalTime, val to: LocalTime)

    private fun span(window: OpenWindow): Span? {
        val from = runCatching { LocalTime.parse(window.from) }.getOrNull() ?: return null
        val to = runCatching { LocalTime.parse(window.to) }.getOrNull() ?: return null
        return if (from.isBefore(to)) Span(window.days.filter { it in 0..6 }.toSet(), from, to) else null
    }

    /** SPEC weekday of [date]: 0 = Monday ... 6 = Sunday. */
    private fun specDay(date: LocalDate): Int = date.dayOfWeek.value - 1

    private fun onDay(span: Span, date: LocalDate): Boolean = specDay(date) in span.days

    fun openState(windows: List<OpenWindow>, now: LocalDateTime): OpenState {
        if (windows.isEmpty()) return OpenState.Open
        val spans = windows.mapNotNull(::span)
        val today = now.toLocalDate()
        val time = now.toLocalTime()
        if (spans.any { onDay(it, today) && !time.isBefore(it.from) && time.isBefore(it.to) }) return OpenState.Open
        // The next start after now: today later, or on one of the next seven days.
        val opensAt = (0L..7L).asSequence()
            .map { today.plusDays(it) }
            .mapNotNull { date ->
                spans.filter { onDay(it, date) }.map { date.atTime(it.from) }.filter { it.isAfter(now) }.minOrNull()
            }
            .firstOrNull()
        return OpenState.Closed(opensAt)
    }

    /**
     * May a task screen opened at [openedAt] still send "I'm done!" at [now]? Yes while open, and
     * for [GRACE_MINUTES] after a window closed, if the task was opened while open (§14.4a).
     */
    fun mayFinish(windows: List<OpenWindow>, openedAt: LocalDateTime, now: LocalDateTime): Boolean {
        if (openState(windows, openedAt) != OpenState.Open) return false
        if (openState(windows, now) == OpenState.Open) return true
        // Closed less than GRACE_MINUTES ago: a window ended in (now - grace, now]. Windows end
        // before midnight, so the grace may reach into the next day.
        val spans = windows.mapNotNull(::span)
        return listOf(now.toLocalDate(), now.toLocalDate().minusDays(1)).any { date ->
            spans.any { span ->
                val end = date.atTime(span.to)
                onDay(span, date) && !now.isBefore(end) && now.isBefore(end.plusMinutes(GRACE_MINUTES))
            }
        }
    }
}
