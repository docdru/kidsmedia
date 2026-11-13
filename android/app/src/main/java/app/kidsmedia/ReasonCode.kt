package app.kidsmedia

import androidx.annotation.StringRes

/**
 * Reason codes sent by the server when something may not be played (SPEC §3.2).
 * The server never sends finished sentences; the app maps each code to a
 * localized string (English, German and Greek, SPEC §4.3).
 */
enum class ReasonCode(val wire: String, @StringRes val message: Int) {
    BLOCKED_TODAY("blocked_today", R.string.reason_blocked_today),
    LOCKOUT("lockout", R.string.reason_lockout),
    ITEM_LIMIT("item_limit", R.string.reason_item_limit),
    MINUTE_LIMIT("minute_limit", R.string.reason_minute_limit),
    GEMS("gems", R.string.reason_gems);

    companion object {
        private val byWire = entries.associateBy { it.wire }

        /** Returns null for codes this app version doesn't know yet. */
        fun fromWire(code: String): ReasonCode? = byWire[code]
    }
}
