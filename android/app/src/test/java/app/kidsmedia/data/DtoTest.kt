package app.kidsmedia.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Wire types parse new fields and still parse an older server without them (SPEC §16.3). */
class DtoTest {
    private val oldLibrary =
        """
        {"server_time":"2026-11-07T14:20:00+01:00","groups":[
          {"id":3,"name":"Cartoons","kind":"video","color":"#C74545",
           "status":{"allowed":true,"params":{}},
           "items":[{"id":31,"title":"Ep 1","duration_s":600}]}
        ]}
        """.trimIndent()

    @Test
    fun anOlderLibraryHasNoGems() {
        val library = ApiJson.decodeFromString(Library.serializer(), oldLibrary)
        assertEquals(Gems(balance = 0, used = false), library.gems)
        assertNull(library.groups[0].status.gemCost)
        assertFalse(library.groups[0].items[0].paidToday)
    }

    @Test
    fun theLibraryCarriesBalanceCostAndPaidToday() {
        val library =
            ApiJson.decodeFromString(
                Library.serializer(),
                """
                {"server_time":"2026-11-07T14:20:00+01:00","gems":{"balance":3,"used":true},"groups":[
                  {"id":3,"name":"Cartoons","kind":"video","color":"#C74545",
                   "status":{"allowed":false,"reason":"gems","params":{"need":2,"have":1},"gem_cost":2},
                   "items":[{"id":31,"title":"Ep 1","duration_s":600,"paid_today":true},
                            {"id":32,"title":"Ep 2","duration_s":600}]}
                ]}
                """.trimIndent()
            )
        assertEquals(Gems(3, true), library.gems)
        val status = library.groups[0].status
        assertEquals(2, status.gemCost)
        assertEquals("gems", status.reason)
        assertEquals(2, status.params.int("need"))
        assertEquals(1, status.params.int("have"))
        assertTrue(library.groups[0].items[0].paidToday)
        assertFalse(library.groups[0].items[1].paidToday)
    }

    @Test
    fun theSnapshotCarriesTheBalanceAndGemRules() {
        val snap =
            ApiJson.decodeFromString(
                Snapshot.serializer(),
                """
                {"server_time":"2026-11-07T14:20:00+01:00","time_zone":"Europe/Berlin","group_ids":[3],
                 "rules":[{"id":7,"days":[5,6],"group_ids":[3],"gem_cost":2,"count_after_seconds":10}],
                 "usage":{"day":"2026-11-07","gem_balance":4,"gems_paid":{"7":[31]}}}
                """.trimIndent()
            )
        assertEquals(4, snap.usage.gemBalance)
        assertEquals(mapOf(7 to listOf(31)), snap.usage.gemsPaid)
        assertEquals(2, snap.rules.single().gemCost)
    }

    @Test
    fun anOlderSnapshotHasNoGems() {
        val snap =
            ApiJson.decodeFromString(
                Snapshot.serializer(),
                """
                {"server_time":"2026-11-07T14:20:00+01:00","time_zone":"Europe/Berlin","group_ids":[3],
                 "rules":[{"id":1,"days":[5]}],"usage":{"day":"2026-11-07"}}
                """.trimIndent()
            )
        assertEquals(0, snap.usage.gemBalance)
        assertTrue(snap.usage.gemsPaid.isEmpty())
        assertNull(snap.rules.single().gemCost)
    }

    @Test
    fun theGemsReasonComesFromTheHeartbeat() {
        val answer =
            ApiJson.decodeFromString(
                HeartbeatResponse.serializer(),
                """{"allowed":false,"reason":"gems","params":{"need":2,"have":1}}"""
            )
        assertEquals("gems", answer.reason)
        assertEquals(2, answer.params.int("need"))
    }
}
