package app.kidsmedia.ui

import android.content.Context
import app.kidsmedia.R
import app.kidsmedia.ReasonCode
import app.kidsmedia.data.Params
import app.kidsmedia.data.Status
import app.kidsmedia.data.int
import app.kidsmedia.data.string
import java.time.DayOfWeek
import java.time.format.TextStyle
import java.util.Locale

/** Child-facing text for a server reason code (SPEC §3.2: the server never sends sentences). */
fun reasonText(context: Context, reason: String, params: Params): String {
    if (reason == "offline") return context.getString(R.string.reason_offline)
    // App-side: stopped while offline without a known reason (PlaybackController.PAUSE_REASON).
    if (reason == "pause") return context.getString(R.string.reason_pause)
    // App-side: Ideas outside their opening times (SPEC §14.4a), params from AppViewModel.closeIdeas.
    if (reason == AppViewModel.IDEAS_CLOSED) return ideasRestingText(context, params)
    // The parent's own words for this rule, in the tablet's language (SPEC §3.2, M6).
    customMessage(context, params)?.let { return it }
    return when (ReasonCode.fromWire(reason)) {
        ReasonCode.LOCKOUT -> context.getString(R.string.reason_lockout, params.string("until") ?: "")
        ReasonCode.BLOCKED_TODAY ->
            params.int("next_day")?.let { context.getString(R.string.reason_blocked_today_next, dayName(context, it)) }
                ?: context.getString(R.string.reason_blocked_today)
        ReasonCode.ITEM_LIMIT -> context.getString(R.string.reason_item_limit)
        ReasonCode.MINUTE_LIMIT -> context.getString(R.string.reason_minute_limit)
        ReasonCode.GEMS -> {
            val (need, have) = gemsNeedHave(params)
            context.getString(R.string.reason_gems, need, have)
        }
        null -> context.getString(R.string.reason_generic)
    }
}

/** The `gems` reason's params (SPEC §16.2): gems needed and owned; missing values read as 0. */
fun gemsNeedHave(params: Params): Pair<Int, Int> = (params.int("need") ?: 0) to (params.int("have") ?: 0)

/**
 * The gem pictures of the `gems` reason screen (SPEC §16.4): one per gem needed, true = owned
 * (bright), false = missing (faded). Owned gems beyond the need aren't shown.
 */
fun gemPictures(need: Int, have: Int): List<Boolean> = List(need.coerceAtLeast(0)) { it < have }

/** "Ideas are resting until 15:00" (today) or "… until Monday at 15:00"; `day` is 0 = Monday. */
private fun ideasRestingText(context: Context, params: Params): String {
    val time = params.string("time") ?: return context.getString(R.string.reason_ideas_resting_now)
    return params.int("day")?.let { context.getString(R.string.reason_ideas_resting_on, dayName(context, it), time) }
        ?: context.getString(R.string.reason_ideas_resting, time)
}

fun customMessage(context: Context, params: Params): String? =
    customMessage(params, context.resources.configuration.locales[0].language)

/**
 * The parent's text for [language]: `message_de`, `message_el`, or `message_en` for any other
 * language. No fallback to another language: without it the app's own text for the code is shown.
 */
fun customMessage(params: Params, language: String): String? {
    val key = when (language) {
        "de" -> "message_de"
        "el" -> "message_el"
        else -> "message_en"
    }
    return params.string(key)?.trim()?.takeIf { it.isNotEmpty() }
}

/** Short badge for a home tile (SPEC §8.1). */
fun badgeText(context: Context, status: Status): String {
    if (!status.allowed) {
        return when (ReasonCode.fromWire(status.reason ?: "")) {
            ReasonCode.LOCKOUT -> context.getString(R.string.badge_lockout, status.params.string("until") ?: "")
            ReasonCode.BLOCKED_TODAY -> context.getString(R.string.badge_blocked)
            ReasonCode.ITEM_LIMIT -> context.getString(R.string.badge_item_limit)
            ReasonCode.MINUTE_LIMIT -> context.getString(R.string.badge_minute_limit)
            ReasonCode.GEMS ->
                context.getString(R.string.badge_gems, status.gemCost ?: status.params.int("need") ?: 0)
            null -> context.getString(R.string.reason_generic)
        }
    }
    // New files need gems (SPEC §16.4): "💎 2".
    status.gemCost?.let { return context.getString(R.string.badge_gems, it) }
    status.itemsLeft?.let { return context.resources.getQuantityString(R.plurals.badge_items_left, it, it) }
    status.minutesLeft?.let { return context.getString(R.string.badge_minutes_left, it) }
    return context.getString(R.string.badge_ready)
}

/** Pill on the player: time left today or "finishing this episode" during grace (SPEC §8.3). */
fun remainingText(context: Context, remainingSeconds: Long?, grace: Boolean): String? = when {
    grace -> context.getString(R.string.player_finishing)
    remainingSeconds == null -> null
    remainingSeconds < 60 -> context.getString(R.string.player_less_than_minute)
    else -> context.getString(R.string.player_minutes_left, ((remainingSeconds + 59) / 60).toInt())
}

/** SPEC weekday numbers: 0 = Monday ... 6 = Sunday. */
fun dayName(context: Context, specDay: Int): String {
    val locale: Locale = context.resources.configuration.locales[0]
    return DayOfWeek.of(specDay.coerceIn(0, 6) + 1).getDisplayName(TextStyle.FULL, locale)
}

fun formatDuration(seconds: Double): String {
    val total = seconds.toLong()
    val h = total / 3600
    val m = (total % 3600) / 60
    val s = total % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}
