package app.kidsmedia.activities

import app.kidsmedia.data.ApiJson
import app.kidsmedia.data.GemLog
import app.kidsmedia.data.GemLogEntry
import app.kidsmedia.data.Gems
import app.kidsmedia.data.StarLog
import java.time.LocalDate
import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LogLogicTest {
    private fun entry(id: Int, delta: Int, kind: String = "admin", note: String? = null) =
        GemLogEntry(id = id, delta = delta, kind = kind, note = note, createdAt = "2026-11-07T13:20:00Z")

    // ---- the popup ----

    @Test
    fun aNewTabletStartsFromTheNewestChangeSoOldOnesNeverPopUp() {
        assertEquals(7, LogLogic.firstSeen(7))
        assertEquals(0, LogLogic.firstSeen(null)) // none yet: the first gems ever do pop up
        assertFalse(LogLogic.hasNews(7, LogLogic.firstSeen(7)))
    }

    @Test
    fun onlyANewerChangeIsNews() {
        assertTrue(LogLogic.hasNews(8, 7))
        assertFalse(LogLogic.hasNews(7, 7))
        assertFalse(LogLogic.hasNews(null, 0))
    }

    @Test
    fun theNewsAreTheGrownUpsChangesSinceTheSeenOneOldestFirst() {
        val log = listOf(
            entry(9, -1, note = "Shouted"),
            entry(8, -2, "spend"),
            entry(6, 3, note = "Tidied"),
            entry(4, 1)
        )
        val news = LogLogic.news(log, seen = 4)!!
        assertEquals(listOf(6, 9), news.entries.map { it.id }) // the spend is no news
        assertEquals(2, news.total)
        assertEquals(9, news.lastId)
        assertNull(LogLogic.news(log, seen = 9))
        assertNull(LogLogic.news(listOf(entry(10, -2, "spend")), seen = 9))
    }

    // ---- the date line ----

    @Test
    fun rowsSayTodayYesterdayOrTheDateInTheTabletsZone() {
        val berlin = ZoneId.of("Europe/Berlin")
        val today = LocalDate.of(2026, 11, 7)
        val now = LogLogic.time("2026-11-07T13:20:00Z", today, berlin)!!
        assertEquals(LogDay.TODAY, now.day)
        assertEquals(14, now.at.hour) // UTC+1
        assertEquals(LogDay.TODAY, LogLogic.time("2026-11-06T23:30:00Z", today, berlin)!!.day) // 00:30 local
        assertEquals(LogDay.YESTERDAY, LogLogic.time("2026-11-06T10:00:00+01:00", today, berlin)!!.day)
        assertEquals(LogDay.EARLIER, LogLogic.time("2026-11-01T10:00:00Z", today, berlin)!!.day)
        assertNull(LogLogic.time("yesterday", today, berlin))
    }

    // ---- the wire ----

    @Test
    fun theLogsAndTheNewsIdDecode() {
        val gems = ApiJson.decodeFromString(Gems.serializer(), """{"balance":3,"used":true,"news_id":12}""")
        assertEquals(12, gems.newsId)
        assertNull(ApiJson.decodeFromString(Gems.serializer(), """{"balance":3,"used":true}""").newsId)
        val gemLog = ApiJson.decodeFromString(
            GemLog.serializer(),
            """{"balance":1,"entries":[{"id":3,"delta":-2,"kind":"spend","note":null,"title":"Episode 1",
              "thumb":{"url":"/api/thumbs/m1-abc.webp","aspect":1.0},"created_at":"2026-11-07T13:20:00Z"}]}"""
        )
        assertEquals("Episode 1", gemLog.entries.single().title)
        val starLog = ApiJson.decodeFromString(
            StarLog.serializer(),
            """{"stars":4,"entries":[
              {"id":5,"delta":3,"kind":"chapter","task":{"world_id":1,"world_name":"Spurenjäger","position":2,
               "kind":"read","title":"R","picture":null},"swap":null,"wish":null,"created_at":"2026-11-07T13:20:00Z"},
              {"id":6,"delta":-2,"kind":"wish","task":null,"swap":null,"wish":{"en":{"name":"Dessert"}},
               "created_at":"2026-11-07T13:21:00Z"}]}"""
        )
        assertEquals(2, starLog.entries[0].task?.position)
        assertEquals("Dessert", starLog.entries[1].wish?.get("en")?.name)
    }
}
