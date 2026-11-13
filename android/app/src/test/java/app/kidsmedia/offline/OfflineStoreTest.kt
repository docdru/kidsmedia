package app.kidsmedia.offline

import app.kidsmedia.activities.ActivityLogic
import app.kidsmedia.activities.OpenLogic
import app.kidsmedia.activities.OpenState
import app.kidsmedia.data.OfflineEvent
import app.kidsmedia.data.OpenWindow
import app.kidsmedia.data.SnapshotLockout
import app.kidsmedia.data.SnapshotRule
import app.kidsmedia.data.SnapshotUsage
import app.kidsmedia.data.StoryChapter
import app.kidsmedia.rules.Reason
import app.kidsmedia.rules.RulesEngine
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OfflineStoreTest {
    private val berlin = ZoneId.of("Europe/Berlin")
    private val today = LocalDate.of(2026, 11, 7) // a Saturday

    private fun event(media: Int, group: Int, at: String, seconds: Double) =
        OfflineEvent("e-$media-$at", media, group, at, seconds, 0)

    @Test
    fun usageAddsTodaysQueuedPlaysToTheSnapshot() {
        val snap = SnapshotUsage("2026-11-07", started = mapOf(1 to 10), seconds = mapOf(10 to 600.0))
        val events =
            listOf(
                event(2, 10, "2026-11-07T09:00:00+01:00", 300.0),
                event(3, 10, "2026-11-06T23:59:00+01:00", 999.0) // yesterday: ignored
            )
        val usage = OfflineStore.mergeUsage(snap, events, today, berlin, null, 10, 0.0)
        assertEquals(mapOf(1 to 10, 2 to 10), usage.started)
        assertEquals(900.0, usage.seconds[10]!!, 0.001)
    }

    @Test
    fun aSnapshotFromYesterdayCountsForNothingToday() {
        val snap =
            SnapshotUsage(
                "2026-11-06",
                started = mapOf(1 to 10),
                seconds = mapOf(10 to 600.0),
                extraItems = mapOf(
                    5 to 1
                )
            )
        val usage = OfflineStore.mergeUsage(snap, emptyList(), today, berlin, null, 10, 0.0)
        assertTrue(usage.started.isEmpty() && usage.seconds.isEmpty() && usage.extraItems.isEmpty())
    }

    @Test
    fun theRunningSessionCountsToo() {
        val snap = SnapshotUsage("2026-11-07")
        val usage = OfflineStore.mergeUsage(snap, emptyList(), today, berlin, 7, 10, 120.0)
        assertEquals(mapOf(7 to 10), usage.started)
        assertEquals(120.0, usage.seconds[10]!!, 0.001)
    }

    @Test
    fun playedSecondsPerFileAddUp() {
        // count_after_seconds (§3.3) needs each file's seconds: snapshot + queued + running.
        val snap = SnapshotUsage("2026-11-07", started = mapOf(7 to 10), played = mapOf(7 to 4.0))
        val events = listOf(event(7, 10, "2026-11-07T09:00:00+01:00", 3.0))
        val usage = OfflineStore.mergeUsage(snap, events, today, berlin, 7, 10, 5.0)
        assertEquals(12.0, usage.played[7]!!, 0.001)
        val rule = SnapshotRule(1, days = listOf(5), allGroups = true, countAfterSeconds = 10)
        assertEquals(10, OfflineStore.rulesOf(listOf(rule)).single().countAfterSeconds)
    }

    @Test
    fun snapshotRulesKeepTheParentMessages() {
        // Decision 15: offline reasons carry the parent's text in all three languages.
        val rule = SnapshotRule(1, days = listOf(5), messageEn = "Bye", messageDe = "Tschüss", messageEl = "Γεια")
        val mapped = OfflineStore.rulesOf(listOf(rule)).single()
        assertEquals(listOf("Bye", "Tschüss", "Γεια"), listOf(mapped.messageEn, mapped.messageDe, mapped.messageEl))
    }

    @Test
    fun oneStoryADayHoldsOffline() {
        val rules = OfflineStore.rulesOf(
            listOf(SnapshotRule(1, days = listOf(5, 6), groupIds = listOf(10), maxItems = 1))
        )
        val now = LocalDateTime.of(2026, 11, 7, 10, 0).atZone(berlin)
        val played = OfflineStore.mergeUsage(
            SnapshotUsage("2026-11-07"),
            listOf(event(2, 10, "2026-11-07T09:00:00+01:00", 300.0)),
            today,
            berlin,
            null,
            10,
            0.0
        )
        val next = RulesEngine.evaluate(rules, played, setOf(10), 10, 3, now)
        assertFalse(next.allowed)
        assertEquals(Reason.ITEM_LIMIT, next.reason)
        assertTrue(RulesEngine.evaluate(rules, played, setOf(10), 10, 2, now).allowed) // replay is free
    }

    @Test
    fun snapshotRulesBecomeEngineRules() {
        val r =
            OfflineStore.rulesOf(
                listOf(
                    SnapshotRule(
                        4,
                        days = listOf(0),
                        allGroups = true,
                        mode = "block",
                        lockouts = listOf(SnapshotLockout("19:30", "07:00"))
                    )
                )
            ).single()
        assertTrue(r.block && r.allGroups && r.lockouts.single().crossesMidnight)
    }

    @Test
    fun theServerZoneIsTheSnapshotsElseTheServerTimesOffsetElseTheTablets() {
        val tablet = ZoneId.of("America/New_York")
        assertEquals(berlin, OfflineStore.serverZone("Europe/Berlin", "2026-11-03T16:00:00+05:00", tablet))
        assertEquals(ZoneOffset.ofHours(1), OfflineStore.serverZone("", "2026-11-03T16:00:00+01:00", tablet))
        assertEquals(ZoneOffset.ofHours(1), OfflineStore.serverZone("Not/AZone", "2026-11-03T16:00:00+01:00", tablet))
        assertEquals(tablet, OfflineStore.serverZone(null, "", tablet))
    }

    @Test
    fun openingTimesAndUnlocksAreTheServersLocalTimes() {
        // Tue 3 Nov 2026, 16:00 in Berlin = 10:00 on a tablet set to New York (SPEC §14.4a).
        val tablet = ZoneId.of("America/New_York")
        val trusted = OffsetDateTime.of(2026, 11, 3, 10, 0, 0, 0, ZoneOffset.ofHours(-5))
        val zone = OfflineStore.serverZone("Europe/Berlin", null, tablet)
        val now = trusted.atZoneSameInstant(zone).toLocalDateTime()
        val windows = listOf(OpenWindow(listOf(0, 1, 2, 3, 4), "15:00", "18:30"))
        assertEquals(OpenState.Open, OpenLogic.openState(windows, now))
        // In the tablet's zone it would wrongly be closed until 15:00.
        assertTrue(
            OpenLogic.openState(windows, trusted.atZoneSameInstant(tablet).toLocalDateTime()) is OpenState.Closed
        )
        // Wed 4 Nov 00:30 in Berlin is still Tue 18:30 in New York: Wednesday's chapter is open.
        val night = OffsetDateTime.of(2026, 11, 3, 18, 30, 0, 0, ZoneOffset.ofHours(-5)).atZoneSameInstant(zone)
        val chapter = StoryChapter(position = 3, date = "2026-11-04")
        assertTrue(ActivityLogic.unlocked(chapter, night.toLocalDateTime(), "00:00"))
        assertFalse(ActivityLogic.unlocked(chapter, night.withZoneSameInstant(tablet).toLocalDateTime(), "00:00"))
        // The tap's time is sent in the server's zone.
        assertEquals("2026-11-04T00:30:00+01:00", ActivityLogic.doneAt(night))
    }

    // ---- gems offline (SPEC §16.2) ----

    private val gemRules =
        OfflineStore.rulesOf(
            listOf(SnapshotRule(7, days = listOf(5, 6), groupIds = listOf(10), gemCost = 2, countAfterSeconds = 10))
        )

    @Test
    fun snapshotRulesCarryTheGemCost() {
        assertEquals(2, gemRules.single().gemCost)
        assertNull(OfflineStore.rulesOf(listOf(SnapshotRule(1, days = listOf(5)))).single().gemCost)
    }

    @Test
    fun anOfflinePlaySpendsItsGemsOnceWhenItCounts() {
        val snap = SnapshotUsage("2026-11-07")
        val tap = OfflineStore.mergeUsage(snap, emptyList(), today, berlin, 2, 10, 5.0)
        assertTrue(OfflineStore.newGemSpends(gemRules, snap, tap, today, emptyList()).isEmpty())
        val counts = OfflineStore.mergeUsage(snap, emptyList(), today, berlin, 2, 10, 10.0)
        val spends = OfflineStore.newGemSpends(gemRules, snap, counts, today, emptyList())
        assertEquals(listOf(GemSpend(7, 2, "2026-11-07", 2)), spends)
        // Seen once, never again that day.
        assertTrue(OfflineStore.newGemSpends(gemRules, snap, counts, today, spends).isEmpty())
        assertEquals(3, OfflineStore.balance(5, spends))
        assertEquals(0, OfflineStore.balance(1, spends)) // never below 0
    }

    @Test
    fun aFileTheServerAlreadyPaidSpendsNothingOffline() {
        // gems_paid: the snapshot's balance already has that spend.
        val snap =
            SnapshotUsage(
                "2026-11-07",
                started = mapOf(2 to 10),
                played = mapOf(2 to 30.0),
                gemsPaid = mapOf(7 to listOf(2))
            )
        val usage = OfflineStore.mergeUsage(snap, emptyList(), today, berlin, 2, 10, 60.0)
        assertTrue(OfflineStore.newGemSpends(gemRules, snap, usage, today, emptyList()).isEmpty())
        // Paid means a spend exists, not just played past count_after_seconds: like the
        // server's next heartbeat, an unpaid counted file spends now.
        val unpaid = snap.copy(gemsPaid = emptyMap())
        assertEquals(1, OfflineStore.newGemSpends(gemRules, unpaid, usage, today, emptyList()).size)
        // From yesterday's snapshot it's a new listen today.
        val old = snap.copy(day = "2026-11-06")
        val fresh = OfflineStore.mergeUsage(old, emptyList(), today, berlin, 2, 10, 60.0)
        assertEquals(1, OfflineStore.newGemSpends(gemRules, old, fresh, today, emptyList()).size)
        assertEquals(mapOf(7 to setOf(2)), OfflineStore.paidOn(snap, emptyList(), today))
        assertTrue(OfflineStore.paidOn(old, emptyList(), today).isEmpty())
    }

    @Test
    fun theOfflineBalanceLocksTheNextListen() {
        val now = LocalDateTime.of(2026, 11, 7, 10, 0).atZone(berlin)
        val snap = SnapshotUsage("2026-11-07", gemBalance = 3)
        val played = listOf(event(2, 10, "2026-11-07T09:00:00+01:00", 60.0))
        val usage = OfflineStore.mergeUsage(snap, played, today, berlin, null, 10, 0.0)
        val spends = OfflineStore.newGemSpends(gemRules, snap, usage, today, emptyList())
        val local =
            usage.copy(
                gems = OfflineStore.balance(snap.gemBalance, spends), // 3 - 2 = 1
                gemsPaid = OfflineStore.paidOn(snap, spends, today)
            )
        val next = RulesEngine.evaluate(gemRules, local, setOf(10), 10, 3, now)
        assertEquals(Reason.GEMS, next.reason)
        assertEquals(mapOf("need" to 2, "have" to 1), next.params)
        assertTrue(RulesEngine.evaluate(gemRules, local, setOf(10), 10, 2, now).allowed) // paid today
    }
}
