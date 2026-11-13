package app.kidsmedia.rules

import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** SPEC §3.4 worked examples, mirrored from server/tests/test_rules.py (offline engine, M7). */
class RulesEngineTest {
    private val berlin = ZoneId.of("Europe/Berlin")
    private val all = setOf(1, 2, 3)
    private val everyDay = (0..6).toSet()
    private val weekdays = (0..4).toSet()
    private val weekend = setOf(5, 6)

    // 2026-11-02 is a Monday.
    private fun at(day: Int, hhmm: String): ZonedDateTime {
        val (h, m) = hhmm.split(":").map { it.toInt() }
        return LocalDateTime.of(2026, 11, 2 + day, h, m).atZone(berlin)
    }

    private fun eval(
        rules: List<Rule>,
        now: ZonedDateTime,
        usage: Usage = Usage(),
        group: Int = 1,
        media: Int? = null
    ) = RulesEngine.evaluate(rules, usage, all, group, media, now)

    @Test
    fun oneAudiobookADayFromTwoSeriesOnWeekends() {
        val rules =
            listOf(
                Rule(1, weekend, groupIds = setOf(1, 2), maxItems = 1),
                Rule(2, weekdays, groupIds = setOf(1, 2), block = true)
            )
        val played = Usage(started = mapOf(10 to 1))
        assertTrue(eval(rules, at(5, "10:00")).allowed)
        val second = eval(rules, at(5, "11:00"), played, group = 2)
        assertEquals(Reason.ITEM_LIMIT, second.reason)
        assertEquals(mapOf("limit" to 1), second.params)
        assertTrue(eval(rules, at(5, "11:00"), played, media = 10).allowed) // replays are free
        val monday = eval(rules, at(0, "10:00"))
        assertEquals(Reason.BLOCKED_TODAY, monday.reason)
        assertEquals(5, monday.params["next_day"])
        assertTrue(eval(rules, at(5, "10:00"), played, group = 3).allowed) // other groups untouched
    }

    @Test
    fun musicMinutesCountDown() {
        val rules = listOf(Rule(1, everyDay, groupIds = setOf(3), maxMinutes = 90))
        val d = eval(rules, at(0, "10:00"), Usage(seconds = mapOf(3 to 30 * 60.0)), group = 3)
        assertTrue(d.allowed)
        assertEquals(60L * 60, d.remainingSeconds)
        assertEquals(60, d.minutesLeft)
        val bonus = Usage(seconds = mapOf(3 to 90 * 60.0), extraMinutes = mapOf(1 to 15))
        assertEquals(15L * 60, eval(rules, at(0, "10:00"), bonus, group = 3).remainingSeconds)
        assertEquals(
            Reason.MINUTE_LIMIT,
            eval(rules, at(0, "10:00"), Usage(seconds = mapOf(3 to 5400.0)), group = 3).reason
        )
    }

    @Test
    fun bedtimeLocksUntilMorningAndCountsDown() {
        val rules = listOf(Rule(1, everyDay, allGroups = true, lockouts = listOf(Window.parse("19:30", "07:00"))))
        assertEquals(600L, eval(rules, at(0, "19:20")).remainingSeconds)
        val night = eval(rules, at(0, "21:00"))
        assertEquals(Reason.LOCKOUT, night.reason)
        assertEquals("07:00", night.params["until"])
        assertEquals(at(1, "07:00"), night.unlocksAt)
        assertTrue(eval(rules, at(1, "07:00")).allowed)
    }

    @Test
    fun schoolNightsAreEveningAnchored() {
        val rules =
            listOf(Rule(1, setOf(6, 0, 1, 2, 3), allGroups = true, lockouts = listOf(Window.parse("19:30", "07:00"))))
        for (locked in listOf(at(6, "20:00"), at(0, "06:00"), at(3, "23:00"), at(4, "06:30"))) {
            assertFalse("$locked", eval(rules, locked).allowed)
        }
        for (free in listOf(at(4, "20:00"), at(5, "06:00"), at(5, "20:00"), at(6, "06:00"))) {
            assertTrue("$free", eval(rules, free).allowed)
        }
    }

    @Test
    fun weekendLieIn() {
        val rules =
            listOf(
                Rule(1, setOf(6, 0, 1, 2, 3), allGroups = true, lockouts = listOf(Window.parse("19:30", "07:00"))),
                Rule(2, setOf(4, 5), allGroups = true, lockouts = listOf(Window.parse("20:30", "08:30")))
            )
        assertEquals(Reason.LOCKOUT, eval(rules, at(5, "08:00")).reason)
        assertTrue(eval(rules, at(7, "07:30")).allowed)
    }

    @Test
    fun midnightStartingLockoutGetsACountdown() {
        val rules = listOf(Rule(1, setOf(5), allGroups = true, lockouts = listOf(Window.parse("00:00", "06:00"))))
        assertEquals(600L, eval(rules, at(4, "23:50")).remainingSeconds)
        assertEquals("06:00", eval(rules, at(5, "00:00")).params["until"])
        val block = listOf(Rule(1, setOf(5), allGroups = true, block = true))
        assertEquals(600L, eval(block, at(4, "23:50")).remainingSeconds)
    }

    @Test
    fun graceLetsTheEpisodeFinishOnly() {
        val rules =
            listOf(
                Rule(
                    1,
                    everyDay,
                    allGroups = true,
                    lockouts = listOf(Window.parse("19:30", "07:00")),
                    graceMinutes = 10
                )
            )
        val d = eval(rules, at(0, "19:30"), media = 10)
        assertNotNull(RulesEngine.graceRule(d, 8 * 60.0))
        assertNull(RulesEngine.graceRule(d, 25 * 60.0))
        val items = listOf(Rule(1, everyDay, allGroups = true, maxItems = 1, graceMinutes = 10))
        val next = eval(items, at(0, "10:00"), Usage(started = mapOf(10 to 1)), media = 11)
        assertNull(RulesEngine.graceRule(next, 60.0)) // grace never starts a file (§3.3a)
    }

    @Test
    fun chainedWindowsUnlockAtTheLastEnd() {
        val rules =
            listOf(
                Rule(1, everyDay, allGroups = true, lockouts = listOf(Window.parse("12:00", "13:00"))),
                Rule(2, everyDay, allGroups = true, lockouts = listOf(Window.parse("12:30", "14:00")))
            )
        assertEquals("14:00", eval(rules, at(0, "12:10")).params["until"])
    }

    @Test
    fun remainingIsRealSecondsAcrossDst() {
        // 2027-03-28: clocks jump from 02:00 to 03:00 in Berlin.
        val now = LocalDateTime.of(2027, 3, 28, 1, 30).atZone(berlin)
        val rules = listOf(Rule(1, everyDay, allGroups = true, lockouts = listOf(Window.parse("04:00", "05:00"))))
        assertEquals(90L * 60, RulesEngine.evaluate(rules, Usage(), all, 1, null, now).remainingSeconds)
    }

    @Test
    fun parentMessagesTravelWithTheReason() {
        val rules =
            listOf(
                Rule(
                    1,
                    everyDay,
                    allGroups = true,
                    block = true,
                    messageEn = "Not today!",
                    messageDe = "Heute nicht!",
                    messageEl = "Όχι σήμερα!"
                )
            )
        val d = eval(rules, at(0, "10:00"))
        assertEquals("Not today!", d.params["message_en"])
        assertEquals("Heute nicht!", d.params["message_de"])
        assertEquals("Όχι σήμερα!", d.params["message_el"])
    }

    @Test
    fun aRunningGraceOnlyCoversItsOwnRules() {
        // §3.3a "Multiple rules": a music-limit grace doesn't carry a song through bedtime.
        val bedtime = { grace: Int ->
            listOf(
                Rule(1, everyDay, groupIds = setOf(1), maxMinutes = 5, graceMinutes = 10),
                Rule(
                    2,
                    everyDay,
                    allGroups = true,
                    lockouts = listOf(Window.parse("14:30", "07:00")),
                    graceMinutes = grace
                )
            )
        }
        val used = Usage(started = mapOf(10 to 1), seconds = mapOf(1 to 300.0))
        val limit = eval(bedtime(0), at(5, "14:25"), used, media = 10)
        val granted = RulesEngine.graceRule(limit, 600.0)
        assertEquals(1, granted?.ruleId)
        val covered = setOf(1)
        assertEquals(covered, RulesEngine.extendGrace(limit, 590.0, covered))
        assertNull(RulesEngine.extendGrace(eval(bedtime(0), at(5, "14:30"), used, media = 10), 300.0, covered))
        val bed = eval(bedtime(5), at(5, "14:30"), used, media = 10)
        assertEquals(setOf(1, 2), RulesEngine.extendGrace(bed, 300.0, covered))
        assertNull(RulesEngine.extendGrace(bed, 301.0, covered)) // too much left for bedtime's 5 min
        val items = listOf(Rule(1, everyDay, allGroups = true, maxItems = 1, graceMinutes = 10))
        val other = eval(items, at(5, "10:00"), Usage(started = mapOf(11 to 1)), media = 10)
        assertNull(RulesEngine.extendGrace(other, 60.0, setOf(1))) // never an item limit
    }

    @Test
    fun aPlayingFileKeepsTheLimitsOfItsSessionDay() {
        // §3.3: videos 30 min on weekdays, 60 at weekends; a Friday video keeps Friday's limit.
        val rules =
            listOf(
                Rule(1, weekdays, groupIds = setOf(3), maxMinutes = 30),
                Rule(2, weekend, groupIds = setOf(3), maxMinutes = 60)
            )
        val friday = Usage(started = mapOf(10 to 3), seconds = mapOf(3 to 25 * 60.0))
        val d = RulesEngine.evaluate(rules, friday, all, 3, 10, at(5, "00:05"), limitsDay = 4)
        assertTrue(d.allowed)
        assertEquals(5 * 60L, d.remainingSeconds)
        val usedUp = Usage(started = mapOf(10 to 3), seconds = mapOf(3 to 30 * 60.0))
        val stop = RulesEngine.evaluate(rules, usedUp, all, 3, 10, at(5, "00:10"), limitsDay = 4)
        assertEquals(Reason.MINUTE_LIMIT, stop.reason)
        assertEquals(3600L, eval(rules, at(5, "00:10"), group = 3, media = 11).remainingSeconds)
        val lockout = listOf(Rule(1, setOf(5), allGroups = true, lockouts = listOf(Window.parse("00:00", "06:00"))))
        val locked = RulesEngine.evaluate(lockout, Usage(), all, 1, 10, at(5, "00:01"), limitsDay = 4)
        assertEquals(Reason.LOCKOUT, locked.reason) // lockouts follow the clock
    }

    @Test
    fun aQuickTapDoesNotUseUpTheStory() {
        // §3.4 "Accidental taps", mirrored from test_rules.py.
        val rules = listOf(Rule(1, weekend, groupIds = setOf(1, 2), maxItems = 1, countAfterSeconds = 10))
        val sat = at(5, "14:20")
        val tapped = Usage(started = mapOf(21 to 1, 22 to 2), played = mapOf(21 to 4.0, 22 to 9.9))
        val d = eval(rules, sat, tapped, media = 23)
        assertTrue(d.allowed)
        assertEquals(1, d.itemsLeft)
        val listened =
            Usage(started = mapOf(21 to 1, 22 to 2, 23 to 1), played = mapOf(21 to 4.0, 22 to 9.9, 23 to 10.0))
        assertTrue(eval(rules, sat, listened, media = 23).allowed)
        assertEquals(0, eval(rules, sat, listened, media = 23).itemsLeft)
        for (other in listOf(21, 22, null)) {
            assertEquals(Reason.ITEM_LIMIT, eval(rules, sat, listened, media = other).reason)
        }
    }

    @Test
    fun countAfterSecondsAddsUpOverTheDayAndIsPerRule() {
        val now = at(2, "10:00")
        val once = listOf(Rule(1, everyDay, groupIds = setOf(1), maxItems = 1, countAfterSeconds = 10))
        assertFalse(eval(once, now, Usage(started = mapOf(21 to 1), played = mapOf(21 to 15.0)), media = 22).allowed)
        val rules =
            listOf(
                Rule(1, everyDay, groupIds = setOf(1), maxItems = 1),
                Rule(2, everyDay, groupIds = setOf(1, 2), maxItems = 1, countAfterSeconds = 60)
            )
        val usage = Usage(started = mapOf(21 to 1), played = mapOf(21 to 30.0))
        assertFalse(eval(rules, now, usage, group = 1, media = 22).allowed) // rule 1 counts at start
        assertTrue(eval(rules, now, usage, group = 2, media = 31).allowed) // rule 2 hasn't counted it
    }

    @Test
    fun aCountedStoryHasAListeningBudget() {
        // §3.4 "Replay budget", mirrored from test_rules.py.
        val rules =
            listOf(Rule(1, weekend, groupIds = setOf(1), maxItems = 1, countAfterSeconds = 10, replayPercent = 120))
        val sat = at(5, "14:20")
        val lengths = mapOf(21 to 1800.0)
        val first = Usage(started = mapOf(21 to 1), played = mapOf(21 to 1800.0), durations = lengths)
        val d = eval(rules, sat, first, media = 21)
        assertTrue(d.allowed)
        assertEquals(360L, d.remainingSeconds)
        val done = Usage(started = mapOf(21 to 1), played = mapOf(21 to 2160.0), durations = lengths)
        assertEquals(Reason.ITEM_LIMIT, eval(rules, sat, done, media = 21).reason)
        assertTrue(RulesEngine.evaluate(rules, done, all, 1, 21, sat, ignoreItemLimit = true).allowed)
        val unlimited = listOf(Rule(1, weekend, groupIds = setOf(1), maxItems = 1))
        assertTrue(eval(unlimited, sat, done, media = 21).allowed)
    }

    // ---- gems (SPEC §16, §3.4 "Gems …" rows, mirrored from test_rules.py) ----

    private val cartoons = 3
    private val gemRule =
        Rule(7, everyDay, groupIds = setOf(3), gemCost = 2, countAfterSeconds = 10, replayPercent = 120)

    private fun video(played: Map<Int, Double>, gems: Int, paid: Map<Int, Set<Int>> = emptyMap()) = Usage(
        started = played.mapValues { cartoons },
        played = played,
        seconds = mapOf(cartoons to played.values.sum()),
        durations = mapOf(31 to 600.0, 32 to 600.0, 33 to 600.0),
        gems = gems,
        gemsPaid = paid
    )

    @Test
    fun gemsUnlockAListen() {
        val now = at(2, "15:00")
        val rules = listOf(gemRule)
        // 3 ≥ 2: a new video may start.
        assertTrue(eval(rules, now, video(emptyMap(), 3), group = cartoons, media = 31).allowed)
        // It plays 10 s: it counts, and 2 💎 are due.
        assertEquals(mapOf((7 to 31) to 2), RulesEngine.dueGemSpends(rules, video(mapOf(31 to 10.0), 3), 2))
        // Spent (balance 1): it may go on and be replayed up to 12 min in total.
        val paid = mapOf(7 to setOf(31))
        val counted = video(mapOf(31 to 10.0), 1, paid)
        assertTrue(RulesEngine.dueGemSpends(rules, counted, 2).isEmpty()) // once
        val goOn = eval(rules, now, counted, group = cartoons, media = 31)
        assertTrue(goOn.allowed)
        assertEquals(710L, goOn.remainingSeconds)
        val budgetUsed = eval(rules, now, video(mapOf(31 to 720.0), 1, paid), group = cartoons, media = 31)
        assertEquals(Reason.ITEM_LIMIT, budgetUsed.reason)
        assertNull(budgetUsed.params["limit"]) // a gem rule has no item limit to show
        // Another video can't start: gems {need 2, have 1}.
        for (other in listOf(32, null)) {
            val d = eval(rules, now, counted, group = cartoons, media = other)
            assertEquals(Reason.GEMS, d.reason)
            assertEquals(mapOf("need" to 2, "have" to 1), d.params)
        }
        // A tap of 5 s on a third video spends nothing.
        assertTrue(RulesEngine.dueGemSpends(rules, video(mapOf(31 to 10.0, 33 to 5.0), 1, paid), 2).isEmpty())
        // Tomorrow the first video costs 2 💎 again.
        val tomorrow = eval(rules, at(3, "15:00"), video(emptyMap(), 1), group = cartoons, media = 31)
        assertEquals(Reason.GEMS, tomorrow.reason)
    }

    @Test
    fun gemsDontOpenOtherRules() {
        val night = listOf(Window.parse("19:30", "07:00"))
        val bedtime = Rule(1, everyDay, allGroups = true, lockouts = night, graceMinutes = 10)
        val minutes = Rule(2, everyDay, groupIds = setOf(3), maxMinutes = 30)
        val rules = listOf(gemRule, bedtime, minutes)
        val rich = video(emptyMap(), 10)
        assertEquals(Reason.LOCKOUT, eval(rules, at(2, "20:00"), rich, group = cartoons).reason)
        val minutesUsed = rich.copy(seconds = mapOf(cartoons to 1800.0))
        assertEquals(Reason.MINUTE_LIMIT, eval(rules, at(2, "15:00"), minutesUsed, group = cartoons).reason)
        // A spent listen still stops at 19:30, with grace as usual.
        val spent = video(mapOf(31 to 300.0), 0, mapOf(7 to setOf(31)))
        val atBedtime = eval(rules, at(2, "19:30"), spent, group = cartoons, media = 31)
        assertEquals(Reason.LOCKOUT, atBedtime.reason)
        assertNotNull(RulesEngine.graceRule(atBedtime, 300.0))
        // Gems are last in priority and never graceable.
        val poor = eval(rules, at(2, "19:30"), video(mapOf(31 to 5.0), 0), group = cartoons, media = 31)
        assertEquals(Reason.LOCKOUT, poor.reason)
        assertNull(RulesEngine.graceRule(poor, 60.0))
        assertFalse(Reason.GEMS.graceable)
    }

    @Test
    fun twoGemRulesOnOneGroupAddUp() {
        val rules = listOf(gemRule, Rule(8, everyDay, groupIds = setOf(3), gemCost = 1, countAfterSeconds = 10))
        val now = at(2, "15:00")
        val short = eval(rules, now, video(emptyMap(), 2), group = cartoons)
        assertEquals(Reason.GEMS, short.reason)
        assertEquals(mapOf("need" to 3, "have" to 2), short.params)
        assertEquals(1, short.denials.size)
        assertTrue(eval(rules, now, video(emptyMap(), 3), group = cartoons).allowed)
        // At the count both spend (2 + 1).
        val due = RulesEngine.dueGemSpends(rules, video(mapOf(31 to 10.0), 3), 2)
        assertEquals(mapOf((7 to 31) to 2, (8 to 31) to 1), due)
        // Only one paid: the other still needs its gem.
        val half = video(mapOf(31 to 10.0), 0, mapOf(7 to setOf(31)))
        val d = eval(rules, now, half, group = cartoons, media = 31)
        assertEquals(mapOf("need" to 1, "have" to 0), d.params)
    }

    @Test
    fun gemsTakenAwayWhilePlaying() {
        val rules = listOf(gemRule)
        val now = at(2, "15:00")
        // Balance 2, a video starts, the parent removes 1 💎 after 5 s: denied before it counts.
        val d = eval(rules, now, video(mapOf(31 to 5.0), 1), group = cartoons, media = 31)
        assertEquals(Reason.GEMS, d.reason)
        assertEquals(mapOf("need" to 2, "have" to 1), d.params)
        assertNull(RulesEngine.graceRule(d, 30.0))
        // After it was paid, removing gems doesn't stop it.
        val paid = video(mapOf(31 to 10.0), 0, mapOf(7 to setOf(31)))
        assertTrue(eval(rules, now, paid, group = cartoons, media = 31).allowed)
        // The stream doesn't check gems (ExoPlayer preloads, §16.2).
        val stream = RulesEngine.evaluate(rules, video(emptyMap(), 0), all, cartoons, 32, now, ignoreItemLimit = true)
        assertTrue(stream.allowed)
    }

    @Test
    fun gemRulesFollowTheirDays() {
        val weekendGems = listOf(gemRule.copy(days = weekend))
        assertTrue(eval(weekendGems, at(2, "15:00"), video(emptyMap(), 0), group = cartoons).allowed)
        assertEquals(Reason.GEMS, eval(weekendGems, at(5, "15:00"), video(emptyMap(), 0), group = cartoons).reason)
        assertTrue(RulesEngine.dueGemSpends(weekendGems, video(mapOf(31 to 10.0), 0), 2).isEmpty())
    }
}
