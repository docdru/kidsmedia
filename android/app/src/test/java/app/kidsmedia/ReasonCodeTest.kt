package app.kidsmedia

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ReasonCodeTest {
    @Test
    fun `every reason code from SPEC 3_2 is known`() {
        val spec = listOf("blocked_today", "lockout", "item_limit", "minute_limit", "gems")
        assertEquals(spec, ReasonCode.entries.map { it.wire })
        spec.forEach { assertEquals(it, ReasonCode.fromWire(it)?.wire) }
    }

    @Test
    fun `unknown codes are tolerated`() {
        assertNull(ReasonCode.fromWire("something_new"))
    }

    @Test
    fun `wire names are unique`() {
        assertEquals(ReasonCode.entries.size, ReasonCode.entries.map { it.wire }.toSet().size)
    }
}
