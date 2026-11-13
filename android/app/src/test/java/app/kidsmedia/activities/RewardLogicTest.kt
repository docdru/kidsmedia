package app.kidsmedia.activities

import app.kidsmedia.data.AlbumPage
import app.kidsmedia.data.ApiJson
import app.kidsmedia.data.Rewards
import app.kidsmedia.data.Sticker
import app.kidsmedia.data.Swap
import app.kidsmedia.data.ThumbRef
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RewardLogicTest {
    private val fiveForAStory =
        Swap(id = 1, stars = 5, ruleId = 3, extraItems = 1, maxPerDay = 1, groups = listOf("Pippi"))

    // ---- swap availability ----

    @Test
    fun aSwapNeedsEnoughStars() {
        assertEquals(SwapState.NOT_ENOUGH_STARS, RewardLogic.swapState(fiveForAStory, 4))
        assertEquals(SwapState.AVAILABLE, RewardLogic.swapState(fiveForAStory, 5))
        assertEquals(SwapState.AVAILABLE, RewardLogic.swapState(fiveForAStory, 12))
    }

    @Test
    fun theDailyLimitWinsOverStars() {
        val used = fiveForAStory.copy(usedToday = 1)
        assertEquals(SwapState.LIMIT_REACHED, RewardLogic.swapState(used, 20))
        assertEquals(SwapState.LIMIT_REACHED, RewardLogic.swapState(used, 0))
        val twice = fiveForAStory.copy(maxPerDay = 2, usedToday = 1)
        assertEquals(SwapState.AVAILABLE, RewardLogic.swapState(twice, 5))
    }

    @Test
    fun noLimitMeansAnyNumberPerDay() {
        val unlimited = fiveForAStory.copy(maxPerDay = null, usedToday = 9)
        assertEquals(SwapState.AVAILABLE, RewardLogic.swapState(unlimited, 5))
    }

    // ---- labels ----

    private fun label(swap: Swap) = RewardLogic.swapLabel(
        swap,
        items = { if (it == 1) "1 more story" else "$it more stories" },
        minutes = { "$it more minutes" },
        join = { amount, groups -> "$amount · $groups" }
    )

    @Test
    fun theLabelIsBuiltFromTheAmountAndTheGroups() {
        assertEquals("1 more story · Pippi", label(fiveForAStory))
        assertEquals("2 more stories · Pippi", label(fiveForAStory.copy(extraItems = 2)))
        val music = Swap(id = 2, stars = 5, extraMinutes = 15, groups = listOf("Music", " ", "Kids songs"))
        assertEquals("15 more minutes · Music, Kids songs", label(music))
    }

    @Test
    fun withoutGroupsTheLabelIsTheAmountAndWithoutAmountThereIsNone() {
        assertEquals("15 more minutes", label(Swap(id = 2, stars = 5, extraMinutes = 15)))
        assertNull(label(Swap(id = 3, stars = 5, extraItems = 0, extraMinutes = null, groups = listOf("Music"))))
    }

    // ---- album ----

    @Test
    fun albumSlotsAddSilhouettesForTheCurrentWorld() {
        val pic = ThumbRef("/api/thumbs/w1c2-ab.webp")
        val stickers = listOf(Sticker(2, pic, "2026-11-03"), Sticker(1, null, "2026-11-02"))
        val page = AlbumPage(worldId = 1, stickers = stickers)
        val slots = RewardLogic.slots(page, chapterCount = 4)
        assertEquals(listOf(1, 2, 3, 4), slots.map { it.position })
        assertEquals(listOf(true, true, false, false), slots.map { it.earned })
        assertEquals(pic, slots[1].sticker?.image)
        // A past world: only what the server sent.
        assertEquals(listOf(1, 2), RewardLogic.slots(page, chapterCount = null).map { it.position })
    }

    @Test
    fun aStickerWithoutDateIsStillToCome() {
        val page = AlbumPage(worldId = 1, stickers = listOf(Sticker(1, null, null)))
        assertFalse(RewardLogic.slots(page, null)[0].earned)
    }

    // ---- wire format ----

    @Test
    fun parsesTheRewardsAnswer() {
        val json =
            """
            {"stars":7,"album":[{"world_id":1,"texts":{"en":{"name":"Lighthouse Island"}},"cover":null,
              "stickers":[{"position":1,"image":{"url":"/api/thumbs/w1c1-9e.webp","aspect":1.0},
                "earned_on":"2026-11-02"}]}],
             "swaps":[{"id":1,"stars":5,"rule_id":3,"extra_items":1,"extra_minutes":null,"used_today":0,
               "max_per_day":1,"groups":["Pippi"],"new_field":true}],
             "wishes":[{"id":4,"texts":{"de":{"name":"Nachtisch aussuchen"}},"stars":10,
               "request":{"id":9,"status":"pending","created_at":"2026-11-05T15:00:00+01:00"}},
               {"id":5,"texts":{"en":{"name":"Park"}},"stars":3,"request":null}]}
            """.trimIndent()
        val r = ApiJson.decodeFromString(Rewards.serializer(), json)
        assertEquals(7, r.stars)
        assertEquals("Lighthouse Island", r.album[0].texts["en"]!!.name)
        assertEquals("2026-11-02", r.album[0].stickers[0].earnedOn)
        assertEquals(fiveForAStory, r.swaps[0])
        assertEquals("pending", r.wishes[0].request!!.status)
        assertNull(r.wishes[1].request)
        // The offline cache writes and reads the same types.
        val cached = ApiJson.encodeToString(Rewards.serializer(), r)
        assertEquals(r, ApiJson.decodeFromString(Rewards.serializer(), cached))
        assertTrue(ApiJson.decodeFromString(Rewards.serializer(), "{}").album.isEmpty())
    }
}
