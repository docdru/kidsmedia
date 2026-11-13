package app.kidsmedia.rules

import java.time.Duration
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit

/**
 * The rules engine (SPEC §3), a line-by-line port of server/src/kidsmedia/rules.py for
 * offline mode (M7). Pure: everything comes in as arguments. Keep both in step; the
 * §3.4 examples are tested on both sides.
 *
 * Weekdays: 0 = Monday ... 6 = Sunday. Times are zoned in the server's time zone.
 */
enum class Reason(val wire: String, val priority: Int) {
    BLOCKED_TODAY("blocked_today", 0),
    LOCKOUT("lockout", 1),
    ITEM_LIMIT("item_limit", 2),
    MINUTE_LIMIT("minute_limit", 3),
    GEMS("gems", 4);

    val graceable: Boolean get() = this == LOCKOUT || this == MINUTE_LIMIT
}

const val DAY_SECONDS = 24 * 3600

/** Lockout window in seconds since midnight. start > end crosses midnight. */
data class Window(val start: Int, val end: Int) {
    init {
        require(start != end) { "a lockout window can't start and end at the same time" }
        require(start in 0 until DAY_SECONDS && end in 0 until DAY_SECONDS) { "window times must be within one day" }
    }

    val crossesMidnight: Boolean get() = start > end

    companion object {
        fun parse(start: String, end: String): Window = Window(hhmm(start), hhmm(end))

        private fun hhmm(value: String): Int {
            val (h, m) = value.split(":").map { it.toInt() }
            require(h in 0..23 && m in 0..59) { "bad time $value" }
            return h * 3600 + m * 60
        }
    }
}

data class Rule(
    val id: Int,
    val days: Set<Int>,
    val enabled: Boolean = true,
    val allGroups: Boolean = false,
    val groupIds: Set<Int> = emptySet(),
    val block: Boolean = false,
    val maxItems: Int? = null,
    val maxMinutes: Int? = null,
    val lockouts: List<Window> = emptyList(),
    val graceMinutes: Int = 0,
    // A started file uses up an item only after this much playing today (§3.3). 0 = at start.
    val countAfterSeconds: Int = 0,
    // Listening budget of a counted file per day, in % of its length (§3.3). null = unlimited.
    val replayPercent: Int? = null,
    // Gems a listen of a file in the scope costs (§16.2). null = no gems needed.
    val gemCost: Int? = null,
    val messageEn: String? = null,
    val messageDe: String? = null,
    val messageEl: String? = null
) {
    fun covers(groupId: Int): Boolean = enabled && (allGroups || groupId in groupIds)

    fun scope(allGroupIds: Set<Int>): Set<Int> = if (allGroups) allGroupIds else groupIds
}

/**
 * Today's usage. [started] maps each distinct file started today to its group, [played]
 * the same keys to that file's playing seconds today.
 */
data class Usage(
    val started: Map<Int, Int> = emptyMap(),
    val seconds: Map<Int, Double> = emptyMap(),
    val played: Map<Int, Double> = emptyMap(),
    val durations: Map<Int, Double> = emptyMap(), // file key -> length in s
    val extraItems: Map<Int, Int> = emptyMap(),
    val extraMinutes: Map<Int, Int> = emptyMap(),
    // The profile's gem balance (§16.1).
    val gems: Int = 0,
    // Rule id -> the files whose listen that gem rule has been paid for today (§16.2).
    val gemsPaid: Map<Int, Set<Int>> = emptyMap()
)

data class Denial(val ruleId: Int, val reason: Reason, val graceMinutes: Int)

data class Decision(
    val allowed: Boolean,
    val reason: Reason? = null,
    val params: Map<String, Any?> = emptyMap(),
    val denials: List<Denial> = emptyList(),
    val remainingSeconds: Long? = null,
    val itemsLeft: Int? = null,
    val minutesLeft: Int? = null,
    val unlocksAt: ZonedDateTime? = null
)

object RulesEngine {
    private fun weekday(t: ZonedDateTime): Int = t.dayOfWeek.value - 1

    private fun midnight(t: ZonedDateTime): ZonedDateTime = t.truncatedTo(ChronoUnit.DAYS)

    private fun secondsOfDay(t: ZonedDateTime): Long = t.toLocalTime().toSecondOfDay().toLong()

    /** Wall-clock offset from midnight, like Python's `midnight + timedelta(seconds=…)`. */
    private fun atOffset(midnight: ZonedDateTime, days: Long, seconds: Int): ZonedDateTime =
        midnight.toLocalDateTime().plusDays(days).plusSeconds(seconds.toLong()).atZone(midnight.zone)

    /** Real seconds between two times (correct across DST changes). */
    private fun until(later: ZonedDateTime, now: ZonedDateTime): Double =
        Duration.between(now, later).toMillis() / 1000.0

    /** The files that use up an item of [rule] today (§3.3). File key -> group id. */
    fun countedItems(rule: Rule, usage: Usage): Map<Int, Int> =
        usage.started.filterKeys { (usage.played[it] ?: 0.0) >= rule.countAfterSeconds }

    /** The listen of [mediaId] is paid today for the gem rule [rule] (§16.2). */
    fun isPaid(rule: Rule, usage: Usage, mediaId: Int?): Boolean =
        mediaId != null && mediaId in (usage.gemsPaid[rule.id] ?: emptySet())

    /**
     * §16.2 "Spending" (the server's `due_gem_spends`, for every file of [usage]): the gems due
     * now, (rule id, file key) -> gem_cost, for each gem rule active on [weekday] (the usage's
     * day) that covers the file, counts it (`count_after_seconds`) and isn't paid yet.
     */
    fun dueGemSpends(rules: List<Rule>, usage: Usage, weekday: Int): Map<Pair<Int, Int>, Int> {
        val due = linkedMapOf<Pair<Int, Int>, Int>()
        for (r in rules) {
            val cost = r.gemCost ?: continue
            if (!r.enabled || r.block || weekday !in r.days) continue
            for ((mediaId, groupId) in countedItems(r, usage)) {
                if (r.covers(groupId) && !isPaid(r, usage, mediaId)) due[r.id to mediaId] = cost
            }
        }
        return due
    }

    /** Seconds of [mediaId]'s listening budget left today under [rule], or null (§3.3). */
    fun replayLeft(rule: Rule, usage: Usage, mediaId: Int): Double? {
        val pct = rule.replayPercent ?: return null
        val length = usage.durations[mediaId]?.takeIf { it > 0 } ?: return null
        return length * pct / 100 - (usage.played[mediaId] ?: 0.0)
    }

    fun windowEndIfActive(rule: Rule, window: Window, now: ZonedDateTime): ZonedDateTime? {
        val day = weekday(now)
        val t = secondsOfDay(now)
        val today = midnight(now)
        if (!window.crossesMidnight) {
            return if (day in rule.days && t >= window.start && t < window.end) atOffset(today, 0, window.end) else null
        }
        // Evening part, ends tomorrow:
        if (day in rule.days && t >= window.start) return atOffset(today, 1, window.end)
        // Morning part of yesterday's window:
        if (Math.floorMod(day - 1, 7) in rule.days && t < window.end) return atOffset(today, 0, window.end)
        return null
    }

    fun nextWindowStartToday(rule: Rule, now: ZonedDateTime): ZonedDateTime? {
        if (weekday(now) !in rule.days) return null
        val t = secondsOfDay(now)
        val start = rule.lockouts.map { it.start }.filter { it > t }.minOrNull() ?: return null
        return atOffset(midnight(now), 0, start)
    }

    fun lockoutFreeAt(rules: List<Rule>, now: ZonedDateTime): ZonedDateTime {
        var t = now
        repeat(16) {
            val ends = rules.flatMap { r -> r.lockouts.mapNotNull { w -> windowEndIfActive(r, w, t) } }
            if (ends.isEmpty()) return t
            t = ends.max()
        }
        return t
    }

    private fun nextUnblockedDay(rules: List<Rule>, day: Int): Int? {
        for (k in 1..7) {
            val candidate = (day + k) % 7
            if (rules.none { it.block && candidate in it.days }) return candidate
        }
        return null
    }

    private fun deniesAtMidnight(rules: List<Rule>, now: ZonedDateTime): Boolean {
        val tomorrow = (weekday(now) + 1) % 7
        return rules.any { r -> tomorrow in r.days && (r.block || r.lockouts.any { it.start == 0 }) }
    }

    /** May [mediaId] of [groupId] play at [now]? mediaId = null asks about a NEW file. */
    fun evaluate(
        rules: List<Rule>,
        usage: Usage,
        allGroupIds: Set<Int>,
        groupId: Int,
        mediaId: Int?,
        now: ZonedDateTime,
        ignoreItemLimit: Boolean = false,
        // The weekday whose item and minute limits apply: a file that is already playing
        // keeps the limits of the day its session started (§3.3). Lockouts and block
        // always follow the clock. null = today.
        limitsDay: Int? = null
    ): Decision {
        val day = weekday(now)
        val limitDay = limitsDay ?: day
        val covering = rules.filter { it.covers(groupId) }
        val denials = mutableListOf<Denial>()
        val remaining = mutableListOf<Double>()
        var itemsLeft: Int? = null
        var minutesLeft: Int? = null
        // Condition 5 (§16.2): the costs of the gem rules not paid for this file today, and
        // the first of them, which gives the `gems` reason.
        var gemNeed = 0
        var gemDenier: Int? = null

        for (r in covering) {
            if (r.lockouts.any { windowEndIfActive(r, it, now) != null }) {
                denials += Denial(r.id, Reason.LOCKOUT, r.graceMinutes)
            }
            nextWindowStartToday(r, now)?.let { remaining += until(it, now) }
            if (r.block) {
                if (day in r.days) denials += Denial(r.id, Reason.BLOCKED_TODAY, 0) // current weekday (§3.3b)
                continue
            }
            if (limitDay !in r.days) continue // limits use the weekday of the session's day (§3.3)
            val scope = r.scope(allGroupIds)
            val counted = countedItems(r, usage)
            val alreadyCounted = mediaId != null && mediaId in counted
            r.maxItems?.let { max ->
                val limit = max + (usage.extraItems[r.id] ?: 0)
                val count = counted.values.count { it in scope }
                val left = maxOf(0, limit - count)
                itemsLeft = itemsLeft?.let { minOf(it, left) } ?: left
                // Only a file that already counts may go on once the limit is used up.
                if (left == 0 && !alreadyCounted && !ignoreItemLimit) {
                    denials += Denial(r.id, Reason.ITEM_LIMIT, r.graceMinutes)
                }
            }
            // A paid listen goes on; the other gem rules need gems together.
            val paid = r.gemCost != null && isPaid(r, usage, mediaId)
            if (r.gemCost != null && !paid) {
                gemNeed += r.gemCost
                if (gemDenier == null) gemDenier = r.id
            }
            val listening = (r.maxItems != null && alreadyCounted) || paid
            if (listening && mediaId != null && !ignoreItemLimit) {
                // A counted (or paid) file's listening budget (replayPercent): used up = done for today.
                val budget = replayLeft(r, usage, mediaId)
                if (budget != null && budget <= 0) {
                    denials += Denial(r.id, Reason.ITEM_LIMIT, r.graceMinutes)
                } else if (budget != null) {
                    remaining += budget
                }
            }
            r.maxMinutes?.let { max ->
                val limitS = (max + (usage.extraMinutes[r.id] ?: 0)) * 60.0
                val leftS = limitS - scope.sumOf { usage.seconds[it] ?: 0.0 }
                val whole = maxOf(0, Math.floor(leftS / 60).toInt())
                minutesLeft = minutesLeft?.let { minOf(it, whole) } ?: whole
                if (leftS <= 0) denials += Denial(r.id, Reason.MINUTE_LIMIT, r.graceMinutes) else remaining += leftS
            }
        }
        // The stream skips gems like the item limit (ExoPlayer preloads). Not graceable:
        // grace never spends or skips gems (§16.2).
        val denier = gemDenier
        if (denier != null && gemNeed > usage.gems && !ignoreItemLimit) denials += Denial(denier, Reason.GEMS, 0)

        if (denials.isNotEmpty()) {
            val order = covering.withIndex().associate { (i, r) -> r.id to i }
            val first = denials.minWith(compareBy({ it.reason.priority }, { order.getValue(it.ruleId) }))
            val params = linkedMapOf<String, Any?>()
            var unlocksAt: ZonedDateTime? = null
            val rule = covering.first { it.id == first.ruleId }
            when (first.reason) {
                Reason.LOCKOUT -> {
                    unlocksAt = lockoutFreeAt(covering, now)
                    params["until"] = "%02d:%02d".format(unlocksAt.hour, unlocksAt.minute)
                }
                Reason.BLOCKED_TODAY -> params["next_day"] = nextUnblockedDay(covering, day)
                // A used-up listening budget of a gem rule without an item limit has no limit (§16.2).
                Reason.ITEM_LIMIT -> rule.maxItems?.let { params["limit"] = it + (usage.extraItems[rule.id] ?: 0) }
                Reason.MINUTE_LIMIT -> params["limit"] = (rule.maxMinutes ?: 0) + (usage.extraMinutes[rule.id] ?: 0)
                Reason.GEMS -> {
                    params["need"] = gemNeed
                    params["have"] = usage.gems
                }
            }
            rule.messageEn?.takeIf { it.isNotEmpty() }?.let { params["message_en"] = it }
            rule.messageDe?.takeIf { it.isNotEmpty() }?.let { params["message_de"] = it }
            rule.messageEl?.takeIf { it.isNotEmpty() }?.let { params["message_el"] = it }
            return Decision(
                allowed = false,
                reason = first.reason,
                params = params,
                denials = denials,
                itemsLeft = itemsLeft,
                minutesLeft = minutesLeft,
                unlocksAt = unlocksAt
            )
        }
        if (deniesAtMidnight(covering, now)) remaining += until(atOffset(midnight(now), 1, 0), now)
        return Decision(
            allowed = true,
            remainingSeconds = remaining.minOrNull()?.toLong(),
            itemsLeft = itemsLeft,
            minutesLeft = minutesLeft
        )
    }

    /** §3.3a: may the item that is already playing finish despite [decision]? */
    fun graceRule(decision: Decision, itemRemainingSeconds: Double): Denial? {
        if (decision.allowed || decision.denials.isEmpty()) return null
        for (d in decision.denials) {
            if (!d.reason.graceable || d.graceMinutes <= 0) return null
            if (itemRemainingSeconds > d.graceMinutes * 60) return null
        }
        return decision.denials.minBy { it.graceMinutes }
    }

    /**
     * §3.3a "Multiple rules", for a grace that is already running (same as the server's
     * `extend_grace`). [covered] are the rules that granted it: their lockouts and minute
     * limits stay covered, and a rule that expires later must grant grace for the rest of
     * the item on its own. Returns the rules covered from now on, or null = stop playback.
     */
    fun extendGrace(decision: Decision, itemRemainingSeconds: Double, covered: Set<Int>): Set<Int>? {
        if (decision.allowed) return covered
        for (d in decision.denials) {
            if (!d.reason.graceable) return null
            if (d.ruleId !in covered && (d.graceMinutes <= 0 || itemRemainingSeconds > d.graceMinutes * 60)) {
                return null
            }
        }
        return covered + decision.denials.map { it.ruleId }
    }
}
