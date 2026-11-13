package app.kidsmedia.activities

import app.kidsmedia.data.PendingPhoto
import java.time.Instant
import java.time.OffsetDateTime
import kotlin.math.roundToInt

/** What to do with a queued photo after an upload attempt (SPEC §15.3 Offline). */
enum class PhotoOutcome {
    /** The server stored it: forget it and delete the file. */
    SENT,

    /** The server refused it for good: forget it and delete the file. */
    DROP,

    /** Try again on the next successful refresh. */
    KEEP
}

/**
 * Photos of drawings and stories (SPEC §15.3): the size the app sends, and the offline queue's
 * order and rules. Pure, so it is tested on the JVM.
 */
object PhotoLogic {
    /** The app sends the long edge at most this long. */
    const val MAX_EDGE = 2048

    const val JPEG_QUALITY = 85

    /** [width] × [height] scaled down so the long edge is at most [maxEdge]; never scaled up. */
    fun scaledSize(width: Int, height: Int, maxEdge: Int = MAX_EDGE): Pair<Int, Int> {
        val long = maxOf(width, height)
        if (long <= maxEdge || long <= 0) return width to height
        val k = maxEdge.toDouble() / long
        return (width * k).roundToInt().coerceAtLeast(1) to (height * k).roundToInt().coerceAtLeast(1)
    }

    /** Add [photo] to the queue. Every photo is kept, also a second one of the same task. */
    fun enqueue(queue: List<PendingPhoto>, photo: PendingPhoto): List<PendingPhoto> =
        if (queue.any { it.file == photo.file }) queue else queue + photo

    /** Oldest first, by the time each was taken; ones without a readable time keep their place first. */
    fun uploadOrder(queue: List<PendingPhoto>): List<PendingPhoto> = queue.sortedBy { takenAt(it) ?: Instant.MIN }

    private fun takenAt(photo: PendingPhoto): Instant? =
        runCatching { OffsetDateTime.parse(photo.doneAt).toInstant() }.getOrNull()

    /**
     * An answer of the server, HTTP [status]: a photo is deleted after the server stored it or refused
     * it with a 4xx (SPEC §15.3), like a queued done tap. Its `done_at` is fixed, so a refusal
     * (e.g. 409 `activities_closed`) would only repeat. A timeout (408) or too many requests (429)
     * aren't about the photo, so it is kept for those.
     */
    fun outcome(status: Int): PhotoOutcome = when {
        status in 200..299 -> PhotoOutcome.SENT
        status == 408 || status == 429 -> PhotoOutcome.KEEP
        status in 400..499 -> PhotoOutcome.DROP
        else -> PhotoOutcome.KEEP
    }

    /** Queue files that no queued photo names any more (left over after a crash): safe to delete. */
    fun orphans(files: List<String>, queue: List<PendingPhoto>): List<String> {
        val named = queue.map { it.file }.toSet()
        return files.filter { it !in named }
    }
}
