package app.kidsmedia.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerUrlTest {
    @Test
    fun addsHttpsAndStripsSlash() {
        assertEquals(ServerUrl.Result.Ok("https://kids.example.org"), ServerUrl.normalize(" kids.example.org/ "))
        assertEquals(
            ServerUrl.Result.Ok("https://kids.example.org:8443"),
            ServerUrl.normalize("https://kids.example.org:8443")
        )
    }

    @Test
    fun publicHttpIsRefused() {
        assertEquals(ServerUrl.Result.HttpsRequired, ServerUrl.normalize("http://kids.example.org"))
    }

    @Test
    fun privateHttpIsAllowed() {
        assertEquals(ServerUrl.Result.Ok("http://192.168.1.20:8080"), ServerUrl.normalize("http://192.168.1.20:8080"))
        assertEquals(ServerUrl.Result.Ok("http://nas.local"), ServerUrl.normalize("http://nas.local"))
    }

    @Test
    fun garbageIsInvalid() {
        assertEquals(ServerUrl.Result.Invalid, ServerUrl.normalize(""))
        assertEquals(ServerUrl.Result.Invalid, ServerUrl.normalize("ftp://kids.example.org"))
        assertEquals(ServerUrl.Result.Invalid, ServerUrl.normalize("https://kids example.org"))
    }

    @Test
    fun privateRanges() {
        assertTrue(ServerUrl.isPrivate("10.0.0.1"))
        assertTrue(ServerUrl.isPrivate("172.16.0.1"))
        assertTrue(ServerUrl.isPrivate("172.31.255.255"))
        assertFalse(ServerUrl.isPrivate("172.32.0.1"))
        assertFalse(ServerUrl.isPrivate("8.8.8.8"))
        assertFalse(ServerUrl.isPrivate("192.168.1"))
        assertFalse(ServerUrl.isPrivate("10.example.org"))
    }
}
