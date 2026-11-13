package app.kidsmedia.activities

import app.kidsmedia.data.Activities
import app.kidsmedia.data.ApiJson
import app.kidsmedia.data.OpenWindow
import java.time.LocalDateTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OpenLogicTest {
    // SPEC §14.4a examples: Mon–Fri 15:00–18:30, Sat–Sun 09:00–18:00.
    private val windows = listOf(
        OpenWindow(days = listOf(0, 1, 2, 3, 4), from = "15:00", to = "18:30"),
        OpenWindow(days = listOf(5, 6), from = "09:00", to = "18:00")
    )

    // Week of Mon 2 Nov 2026.
    private fun tue(hour: Int, minute: Int = 0) = LocalDateTime.of(2026, 11, 3, hour, minute)

    private fun closed(at: LocalDateTime?) = OpenState.Closed(at)

    @Test
    fun `Tue 07_30 is closed and opens Tue 15_00`() {
        assertEquals(closed(tue(15)), OpenLogic.openState(windows, tue(7, 30)))
    }

    @Test
    fun `Tue 16_00 is open`() {
        assertEquals(OpenState.Open, OpenLogic.openState(windows, tue(16)))
    }

    @Test
    fun `Fri 19_00 is closed and opens Sat 09_00`() {
        assertEquals(
            closed(LocalDateTime.of(2026, 11, 7, 9, 0)),
            OpenLogic.openState(windows, LocalDateTime.of(2026, 11, 6, 19, 0))
        )
    }

    @Test
    fun `Sun 18_10 is closed and opens Mon 15_00`() {
        assertEquals(
            closed(LocalDateTime.of(2026, 11, 9, 15, 0)),
            OpenLogic.openState(windows, LocalDateTime.of(2026, 11, 8, 18, 10))
        )
    }

    @Test
    fun `no windows means always open`() {
        assertEquals(OpenState.Open, OpenLogic.openState(emptyList(), tue(3)))
        assertEquals(OpenState.Open, OpenLogic.openState(emptyList(), LocalDateTime.of(2026, 11, 8, 23, 59)))
    }

    @Test
    fun `from is inside the window, to is not`() {
        assertEquals(OpenState.Open, OpenLogic.openState(windows, tue(15)))
        assertEquals(OpenState.Open, OpenLogic.openState(windows, tue(18, 29)))
        assertEquals(closed(LocalDateTime.of(2026, 11, 4, 15, 0)), OpenLogic.openState(windows, tue(18, 30)))
    }

    @Test
    fun `a window only on one weekday opens a week later once it has passed`() {
        val tuesdays = listOf(OpenWindow(days = listOf(1), from = "10:00", to = "11:00"))
        assertEquals(closed(LocalDateTime.of(2026, 11, 10, 10, 0)), OpenLogic.openState(tuesdays, tue(12)))
    }

    @Test
    fun `windows that never open give no opening time`() {
        val never = listOf(OpenWindow(days = emptyList(), from = "10:00", to = "11:00"))
        assertEquals(closed(null), OpenLogic.openState(never, tue(10, 30)))
    }

    @Test
    fun `windows that don't parse are skipped`() {
        val broken = listOf(
            OpenWindow(days = listOf(1), from = "nonsense", to = "11:00"),
            OpenWindow(days = listOf(1), from = "12:00", to = "12:00"),
            OpenWindow(days = listOf(1), from = "14:00", to = "15:00")
        )
        assertEquals(closed(tue(14)), OpenLogic.openState(broken, tue(10, 30)))
    }

    // ---- the grace after closing ----

    @Test
    fun `done at 18_40 for a task opened at 18_20 is accepted, 18_50 is not`() {
        assertTrue(OpenLogic.mayFinish(windows, tue(18, 20), tue(18, 40)))
        assertFalse(OpenLogic.mayFinish(windows, tue(18, 20), tue(18, 50)))
    }

    @Test
    fun `the grace ends 15 minutes after closing`() {
        assertTrue(OpenLogic.mayFinish(windows, tue(18, 20), tue(18, 44)))
        assertFalse(OpenLogic.mayFinish(windows, tue(18, 20), tue(18, 45)))
    }

    @Test
    fun `a task opened while open may be finished while still open`() {
        assertTrue(OpenLogic.mayFinish(windows, tue(15, 5), tue(17)))
    }

    @Test
    fun `a task opened while closed gets no grace`() {
        assertFalse(OpenLogic.mayFinish(windows, tue(18, 35), tue(18, 40)))
    }

    @Test
    fun `the grace reaches past midnight`() {
        val late = listOf(OpenWindow(days = listOf(1), from = "23:00", to = "23:55"))
        assertTrue(OpenLogic.mayFinish(late, tue(23, 50), LocalDateTime.of(2026, 11, 4, 0, 5)))
        assertFalse(OpenLogic.mayFinish(late, tue(23, 50), LocalDateTime.of(2026, 11, 4, 0, 10)))
    }

    @Test
    fun `no windows means a task may always be finished`() {
        assertTrue(OpenLogic.mayFinish(emptyList(), tue(3), tue(23)))
    }

    // ---- wire ----

    @Test
    fun `older servers without opening times mean always open`() {
        val data = ApiJson.decodeFromString(Activities.serializer(), """{"server_time": ""}""")
        assertTrue(data.windows.isEmpty())
        assertTrue(data.openNow)
        assertNull(data.opensAt)
        assertEquals(OpenState.Open, ActivityLogic.view(data, emptyList(), tue(7)).open)
    }

    @Test
    fun `opening times are read from the wire`() {
        val json =
            """{"windows": [{"days": [0, 1, 2, 3, 4], "from": "15:00", "to": "18:30"}],
               "open_now": false, "opens_at": "2026-11-03T15:00:00"}"""
        val data = ApiJson.decodeFromString(Activities.serializer(), json)
        assertEquals(listOf(windows[0]), data.windows)
        assertFalse(data.openNow)
        assertEquals("2026-11-03T15:00:00", data.opensAt)
        assertEquals(closed(tue(15)), ActivityLogic.view(data, emptyList(), tue(7, 30)).open)
    }
}
