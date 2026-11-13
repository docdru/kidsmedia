package app.kidsmedia.activities

import app.kidsmedia.data.ApiJson
import app.kidsmedia.data.DoneRequest
import app.kidsmedia.data.PendingPhoto
import kotlinx.serialization.builtins.ListSerializer
import org.junit.Assert.assertEquals
import org.junit.Test

class PhotoLogicTest {
    private fun photo(file: String, doneAt: String, kind: String = "draw") = PendingPhoto(1, 2, kind, doneAt, file)

    @Test
    fun theLongEdgeIsAtMost2048AndNothingIsScaledUp() {
        assertEquals(2048 to 1536, PhotoLogic.scaledSize(4000, 3000))
        assertEquals(1536 to 2048, PhotoLogic.scaledSize(3000, 4000))
        assertEquals(2048 to 1152, PhotoLogic.scaledSize(4096, 2304))
        assertEquals(1600 to 1200, PhotoLogic.scaledSize(1600, 1200))
        assertEquals(2048 to 2048, PhotoLogic.scaledSize(2048, 2048))
        // A very thin picture keeps at least one pixel.
        assertEquals(2048 to 1, PhotoLogic.scaledSize(100_000, 10))
    }

    @Test
    fun everyPhotoIsQueuedAlsoASecondOneOfTheSameTask() {
        val a = photo("a.jpg", "2026-11-03T18:40:00+01:00")
        val b = photo("b.jpg", "2026-11-03T18:45:00+01:00")
        val queue = PhotoLogic.enqueue(PhotoLogic.enqueue(emptyList(), a), b)
        assertEquals(listOf(a, b), queue)
        // The same file twice is one photo.
        assertEquals(queue, PhotoLogic.enqueue(queue, a))
    }

    @Test
    fun theOldestIsSentFirst() {
        val late = photo("late.jpg", "2026-11-03T18:00:00+01:00")
        // Earlier, though written in another offset: 16:30Z is 17:30+01:00.
        val early = photo("early.jpg", "2026-11-03T16:30:00Z")
        val broken = photo("broken.jpg", "not a time")
        assertEquals(listOf(broken, early, late), PhotoLogic.uploadOrder(listOf(late, early, broken)))
    }

    @Test
    fun aPhotoIsDeletedOnlyWhenStoredOrRefusedForGood() {
        assertEquals(PhotoOutcome.SENT, PhotoLogic.outcome(200))
        // Every 4xx drops it, 409 activities_closed too: done_at is fixed, it would only repeat (SPEC §15.3).
        assertEquals(PhotoOutcome.DROP, PhotoLogic.outcome(409))
        assertEquals(PhotoOutcome.DROP, PhotoLogic.outcome(404))
        assertEquals(PhotoOutcome.DROP, PhotoLogic.outcome(413))
        assertEquals(PhotoOutcome.DROP, PhotoLogic.outcome(422))
        // Not about the photo: kept for the next refresh.
        assertEquals(PhotoOutcome.KEEP, PhotoLogic.outcome(408))
        assertEquals(PhotoOutcome.KEEP, PhotoLogic.outcome(429))
        assertEquals(PhotoOutcome.KEEP, PhotoLogic.outcome(500))
        assertEquals(PhotoOutcome.KEEP, PhotoLogic.outcome(502))
    }

    @Test
    fun filesNoQueuedPhotoNamesAreOrphans() {
        val queue = listOf(photo("a.jpg", "2026-11-03T18:40:00+01:00"))
        assertEquals(listOf("b.jpg"), PhotoLogic.orphans(listOf("a.jpg", "b.jpg"), queue))
        assertEquals(listOf("a.jpg"), PhotoLogic.orphans(listOf("a.jpg"), emptyList()))
    }

    @Test
    fun aQueuedPhotoShowsAsDoneAndSurvivesTheStore() {
        val p = photo("a.jpg", "2026-11-03T18:40:00+01:00", kind = "write")
        assertEquals(DoneRequest(1, 2, "write", doneAt = "2026-11-03T18:40:00+01:00"), p.asDone())
        val serializer = ListSerializer(PendingPhoto.serializer())
        val json = ApiJson.encodeToString(serializer, listOf(p))
        assertEquals(
            """[{"world_id":1,"position":2,"kind":"write","done_at":"2026-11-03T18:40:00+01:00","file":"a.jpg"}]""",
            json
        )
        assertEquals(listOf(p), ApiJson.decodeFromString(serializer, json))
    }
}
