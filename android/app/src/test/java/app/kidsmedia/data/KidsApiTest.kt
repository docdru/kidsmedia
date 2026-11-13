package app.kidsmedia.data

import java.io.File
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class KidsApiTest {
    private val server = MockWebServer()
    private lateinit var credentials: FakeCredentials
    private lateinit var api: KidsApi

    private class FakeCredentials(override var baseUrl: String?, override var token: String?) : Credentials

    @Before
    fun setUp() {
        server.start()
        credentials = FakeCredentials(server.url("/").toString().trimEnd('/'), "secret-token")
        api = KidsApi(buildHttpClient(credentials), credentials)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun sendsBearerTokenAndParsesLibrary() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """
                    {"server_time":"2026-11-07T14:20:00+01:00","groups":[
                      {"id":1,"name":"Die Drei ???","kind":"audiobook","color":"#4552C7",
                       "cover":{"url":"/api/thumbs/g1-abc123","aspect":1.0},
                       "status":{"allowed":true,"items_left":1,"params":{}},
                       "items":[{"id":7,"title":"Folge 1","duration_s":2712.5,"position_ms":1000,
                                 "played_today":false,"allowed":true,"future_field":42}]},
                      {"id":2,"name":"Videos","kind":"video","color":"#C74545",
                       "status":{"allowed":false,"reason":"lockout","params":{"until":"07:00"}}}
                    ]}
                """.trimIndent()
            )
        )
        val library = api.library()
        val request = server.takeRequest()
        assertEquals("/api/library", request.path)
        assertEquals("Bearer secret-token", request.getHeader("Authorization"))
        assertEquals(2, library.groups.size)
        assertEquals(1, library.groups[0].status.itemsLeft)
        assertEquals(2712.5, library.groups[0].items[0].durationS, 0.0)
        assertTrue(library.groups[1].isVideo)
        assertEquals("lockout", library.groups[1].status.reason)
        assertEquals("07:00", library.groups[1].status.params.string("until"))
    }

    @Test
    fun errorBodyBecomesReasonCode() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"reason":"unknown_session","params":{}}"""))
        try {
            api.heartbeat(HeartbeatRequest(7, "abc", 12.0, 3000))
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(404, e.status)
            assertEquals("unknown_session", e.reason)
        }
        val body = server.takeRequest().body.readUtf8()
        assertTrue(body.contains("\"played_seconds_total\":12.0"))
    }

    @Test
    fun nonJsonErrorStillHasACode() = runTest {
        server.enqueue(MockResponse().setResponseCode(502).setBody("<html>bad gateway</html>"))
        try {
            api.library()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals("http_502", e.reason)
        }
    }

    @Test
    fun pairDoesNotLeakAnOldToken() = runTest {
        credentials.token = null
        server.enqueue(MockResponse().setBody("""{"token":"t","device_id":3,"profile_id":1}"""))
        val answer = api.pair(credentials.baseUrl!!, PairRequest("123456", "Tab", "1.0"))
        assertEquals(3, answer.deviceId)
        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun parsesTheLatestAppRelease() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """{"version_name":"1.2.3","version_code":10203,"size":5,"sha256":"ab",""" +
                    """"uploaded_at":"2026-11-03T00:00:00Z","url":"/api/app/apk"}"""
            )
        )
        val release = api.latestApp()
        assertEquals(10203, release.versionCode)
        assertEquals("/api/app/latest", server.takeRequest().path)
    }

    @Test
    fun activityDonePostsTheTaskAndLockedIsAReason() = runTest {
        server.enqueue(MockResponse().setBody("""{"unlock_time":"07:00","world":null}"""))
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"reason":"activity_locked"}"""))
        assertNull(api.activityDone(DoneRequest(1, 3, "draw")).world)
        val request = server.takeRequest()
        assertEquals("/api/activities/done", request.path)
        assertEquals("""{"world_id":1,"position":3,"kind":"draw"}""", request.body.readUtf8())
        try {
            api.activityDone(DoneRequest(1, 4, "calc"))
            fail("expected 409")
        } catch (e: ApiException) {
            assertEquals(409, e.status)
            assertEquals("activity_locked", e.reason)
        }
    }

    @Test
    fun readDoneSendsTheAttempts() = runTest {
        server.enqueue(MockResponse().setBody("""{"level":"harder","world":null}"""))
        assertEquals("harder", api.activityDone(DoneRequest(1, 2, "read", attempts = 3)).level)
        val body = server.takeRequest().body.readUtf8()
        assertEquals("""{"world_id":1,"position":2,"kind":"read","attempts":3}""", body)
    }

    @Test
    fun doneSendsTheTimeOfTheTap() = runTest {
        server.enqueue(MockResponse().setBody("""{"world":null}"""))
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"reason":"activity_too_old"}"""))
        api.activityDone(DoneRequest(1, 2, "draw", doneAt = "2026-11-03T18:40:00+01:00"))
        val body = server.takeRequest().body.readUtf8()
        assertEquals("""{"world_id":1,"position":2,"kind":"draw","done_at":"2026-11-03T18:40:00+01:00"}""", body)
        try {
            api.activityDone(DoneRequest(1, 2, "calc", doneAt = "2026-10-01T10:00:00+02:00"))
            fail("expected 409")
        } catch (e: ApiException) {
            assertEquals(409, e.status)
            assertEquals("activity_too_old", e.reason)
        }
    }

    @Test
    fun aPhotoIsUploadedAsRawJpegWithTheTaskInTheQuery() = runTest {
        val file = File.createTempFile("shot", ".jpg").apply { deleteOnExit() }
        val jpeg = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 1, 2, 3, 0xFF.toByte(), 0xD9.toByte())
        file.writeBytes(jpeg)
        server.enqueue(MockResponse().setBody("""{"level":"normal","world":null}"""))
        server.enqueue(MockResponse().setResponseCode(422).setBody("""{"reason":"bad_image"}"""))
        val photo = PendingPhoto(1, 3, "draw", "2026-11-03T18:40:00+01:00", file.name)
        assertNull(api.activityPhoto(photo, file).world)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        val url = request.requestUrl!!
        assertEquals("/api/activities/photo", url.encodedPath)
        assertEquals("1", url.queryParameter("world_id"))
        assertEquals("3", url.queryParameter("position"))
        assertEquals("draw", url.queryParameter("kind"))
        // The "+" of the offset is encoded, so the server doesn't read it as a space.
        assertEquals("2026-11-03T18:40:00+01:00", url.queryParameter("done_at"))
        assertTrue(request.path!!.contains("%2B01"))
        assertEquals("image/jpeg", request.getHeader("Content-Type"))
        assertEquals("Bearer secret-token", request.getHeader("Authorization"))
        assertTrue(jpeg.contentEquals(request.body.readByteArray()))
        try {
            api.activityPhoto(photo, file)
            fail("expected 422")
        } catch (e: ApiException) {
            assertEquals(422, e.status)
            assertEquals("bad_image", e.reason)
        }
        file.delete()
    }

    @Test
    fun rewardsSwapAndWish() = runTest {
        server.enqueue(MockResponse().setBody("""{"stars":12,"album":[],"swaps":[],"wishes":[]}"""))
        server.enqueue(MockResponse().setBody("""{"stars":7}"""))
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"reason":"request_pending"}"""))
        assertEquals(12, api.rewards().stars)
        assertEquals("/api/rewards", server.takeRequest().path)
        assertEquals(7, api.redeemSwap(4).stars)
        val swap = server.takeRequest()
        assertEquals("POST", swap.method)
        assertEquals("/api/rewards/swaps/4", swap.path)
        try {
            api.requestWish(9)
            fail("expected 409")
        } catch (e: ApiException) {
            assertEquals(409, e.status)
            assertEquals("request_pending", e.reason)
        }
        assertEquals("/api/rewards/wishes/9/request", server.takeRequest().path)
    }

    @Test
    fun tokenOnlyGoesToOurHost() {
        assertTrue(AuthInterceptor.isOurs("https://kids.example/api/library", "https://kids.example"))
        assertTrue(AuthInterceptor.isOurs("https://kids.example/api/thumbs/m1?size=md", "https://kids.example"))
        assertFalse(AuthInterceptor.isOurs("https://kids.example.evil.net/x", "https://kids.example"))
        assertFalse(AuthInterceptor.isOurs("https://other.example/api", "https://kids.example"))
    }
}
