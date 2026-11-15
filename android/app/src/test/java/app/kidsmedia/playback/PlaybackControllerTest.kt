package app.kidsmedia.playback

import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import app.kidsmedia.data.Credentials
import app.kidsmedia.data.KidsApi
import app.kidsmedia.data.LibraryGroup
import app.kidsmedia.data.LibraryItem
import app.kidsmedia.data.buildHttpClient
import java.lang.reflect.Proxy
import java.util.Collections
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Auto-advance with the screen off (SPEC §5.3): the player must keep running while
 * `/play/start` is asked for the next file, or the tablet falls asleep in between.
 */
class PlaybackControllerTest {
    private val server = MockWebServer()
    private lateinit var controller: PlaybackController
    private val player = FakePlayer()

    private class FakeCredentials(override val baseUrl: String?, override val token: String?) : Credentials

    /** Records what the controller does to the player; everything else answers with a default. */
    private class FakePlayer {
        val calls: MutableList<String> = Collections.synchronizedList(mutableListOf())
        var listener: Player.Listener? = null

        @Volatile var volume = 1f

        @Volatile var playWhenReady = false

        val proxy: Player =
            Proxy.newProxyInstance(Player::class.java.classLoader, arrayOf(Player::class.java)) { self, method, raw ->
                val args = raw.orEmpty()
                when (method.name) {
                    "addListener" -> listener = args[0] as Player.Listener
                    "setVolume" -> {
                        volume = args[0] as Float
                        calls += "volume=$volume"
                    }
                    "getVolume" -> return@newProxyInstance volume
                    "play" -> {
                        playWhenReady = true
                        calls += "play"
                    }
                    "pause" -> {
                        playWhenReady = false
                        calls += "pause"
                    }
                    "seekTo" -> calls += "seekTo=${args.last()}"
                    "isPlaying", "getPlayWhenReady" -> return@newProxyInstance playWhenReady
                    "equals" -> return@newProxyInstance (self === args[0])
                    "hashCode" -> return@newProxyInstance 1
                    "toString" -> return@newProxyInstance "FakePlayer"
                }
                when (method.returnType) {
                    Boolean::class.javaPrimitiveType -> false
                    Int::class.javaPrimitiveType -> 0
                    Long::class.javaPrimitiveType -> 0L
                    Float::class.javaPrimitiveType -> 0f
                    else -> null
                }
            } as Player
    }

    private val group =
        LibraryGroup(
            id = 1,
            name = "Music",
            kind = "music",
            color = "#4552C7",
            items = listOf(LibraryItem(1, "One", 180.0), LibraryItem(2, "Two", 200.0))
        )

    @Before
    fun setUp() {
        server.start()
        val credentials = FakeCredentials(server.url("/").toString().trimEnd('/'), "secret-token")
        val api = KidsApi(buildHttpClient(credentials), credentials)
        controller = PlaybackController(api, CoroutineScope(Dispatchers.Unconfined), { System.nanoTime() / 1_000_000 })
        controller.attach(
            object : PlayerHost {
                override fun playerFor(video: Boolean): Player = player.proxy
            }
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun allowed(session: String) =
        MockResponse().setBody("""{"allowed":true,"play_session_id":"$session","position_ms":0}""")

    /** Song one plays; then the player moves on to song two by itself. */
    private fun playThenAdvance() {
        server.enqueue(allowed("s1"))
        runBlocking { assertTrue(controller.play(group, 0)) }
        assertEquals("/api/play/start", server.takeRequest().path)
        player.calls.clear()
        val listener = checkNotNull(player.listener)
        listener.onMediaItemTransition(
            MediaItem.Builder().setMediaId("2").build(),
            Player.MEDIA_ITEM_TRANSITION_REASON_AUTO
        )
    }

    private fun waitFor(what: String, condition: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
        while (!condition()) {
            assertTrue("timed out waiting for $what, calls: ${player.calls}", System.nanoTime() < deadline)
            Thread.sleep(10)
        }
    }

    @Test
    fun autoAdvanceKeepsThePlayerRunningWhileTheServerIsAsked() {
        // The final heartbeat of song one and the start of song two both take a moment.
        server.enqueue(MockResponse().setBody("""{"allowed":true}""").setBodyDelay(500, TimeUnit.MILLISECONDS))
        server.enqueue(allowed("s2").setBodyDelay(500, TimeUnit.MILLISECONDS))
        playThenAdvance()

        // While the requests are in flight: muted, but never paused.
        assertEquals("/api/play/heartbeat", server.takeRequest().path)
        assertEquals(listOf("volume=0.0"), player.calls.toList())
        assertTrue(player.playWhenReady)
        assertEquals("/api/play/start", server.takeRequest().path)

        waitFor("song two to start") { controller.nowPlaying.value?.item?.id == 2 && player.volume == 1f }
        assertEquals(listOf("volume=0.0", "seekTo=0", "play", "volume=1.0"), player.calls.toList())
        assertTrue(player.playWhenReady)
        assertNull(controller.block.value)
    }

    @Test
    fun deniedAutoAdvancePausesAndShowsTheReason() {
        server.enqueue(MockResponse().setBody("""{"allowed":true}"""))
        server.enqueue(MockResponse().setBody("""{"allowed":false,"reason":"item_limit","params":{"limit":1}}"""))
        playThenAdvance()

        waitFor("the reason screen") { controller.block.value != null && player.volume == 1f }
        assertEquals("item_limit", controller.block.value?.reason)
        assertEquals(listOf("volume=0.0", "pause", "seekTo=0", "volume=1.0"), player.calls.toList())
        assertFalse(player.playWhenReady)
        // Song one stays the session: nothing started for song two.
        assertNotNull(controller.nowPlaying.value)
        assertEquals(1, controller.nowPlaying.value?.item?.id)
    }
}
