package app.kidsmedia.activities

import android.content.Context
import app.kidsmedia.data.Activities
import app.kidsmedia.data.ApiJson
import app.kidsmedia.data.DoneRequest
import app.kidsmedia.data.PendingPhoto
import app.kidsmedia.data.Rewards
import java.io.File
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer

/**
 * Activities offline (SPEC §13.3): the last answer from the server, "I'm done!" taps the
 * server hasn't confirmed yet, and the last rewards answer (§14.4, shown offline, actions need
 * the server). All are small JSON files in app-private storage. Photos of drawings and stories
 * (§15.3) live next to them: `photos/shots/` while the child looks at a picture, `photos/queue/`
 * (listed in `photos.json`) until the server has stored it.
 */
class ActivityStore(context: Context) {
    private val dir = File(context.filesDir, "activities").apply { mkdirs() }
    private val lock = Mutex()
    private val pendingSerializer = ListSerializer(DoneRequest.serializer())
    private val photoSerializer = ListSerializer(PendingPhoto.serializer())
    private val shots = File(dir, "photos/shots").apply { mkdirs() }
    private val queued = File(dir, "photos/queue").apply { mkdirs() }

    private fun <T> read(name: String, serializer: KSerializer<T>): T? =
        runCatching { ApiJson.decodeFromString(serializer, File(dir, name).readText()) }.getOrNull()

    private fun <T> write(name: String, serializer: KSerializer<T>, value: T) {
        val tmp = File(dir, "$name.tmp")
        tmp.writeText(ApiJson.encodeToString(serializer, value))
        tmp.renameTo(File(dir, name))
    }

    suspend fun cached(): Activities? = withContext(Dispatchers.IO) { read(CACHE, Activities.serializer()) }

    suspend fun save(activities: Activities) = lock.withLock {
        withContext(Dispatchers.IO) { write(CACHE, Activities.serializer(), activities) }
    }

    suspend fun cachedRewards(): Rewards? = withContext(Dispatchers.IO) { read(REWARDS, Rewards.serializer()) }

    suspend fun saveRewards(rewards: Rewards) = lock.withLock {
        withContext(Dispatchers.IO) { write(REWARDS, Rewards.serializer(), rewards) }
    }

    suspend fun pending(): List<DoneRequest> = withContext(Dispatchers.IO) {
        read(PENDING, pendingSerializer) ?: emptyList()
    }

    suspend fun addPending(request: DoneRequest) = lock.withLock {
        withContext(Dispatchers.IO) {
            val now = read(PENDING, pendingSerializer) ?: emptyList()
            write(PENDING, pendingSerializer, ActivityLogic.mergePending(now, request))
        }
    }

    /** The server answered for [request] (accepted, or refused for good): forget it. */
    suspend fun removePending(request: DoneRequest) = lock.withLock {
        withContext(Dispatchers.IO) {
            val now = read(PENDING, pendingSerializer) ?: emptyList()
            write(PENDING, pendingSerializer, now - request)
        }
    }

    // ---- photos (SPEC §15.3) ----

    /**
     * A new file for the camera screen to write a picture to. Pictures not sent are left there
     * and deleted the next time the camera opens ([clearShots]).
     */
    fun newShot(): File = File(shots, "${UUID.randomUUID()}.jpg")

    /** The camera opens: pictures of an earlier visit that weren't sent go. */
    suspend fun clearShots() = withContext(Dispatchers.IO) {
        shots.listFiles()?.forEach { it.delete() }
        Unit
    }

    /** The queued photo's JPEG; null if it is gone. */
    fun photoFile(photo: PendingPhoto): File? = File(queued, photo.file).takeIf { it.isFile }

    suspend fun pendingPhotos(): List<PendingPhoto> = withContext(Dispatchers.IO) {
        read(PHOTOS, photoSerializer) ?: emptyList()
    }

    /**
     * "Send": [shot] moves into the queue with its task and time, before any upload is tried, so
     * the task shows as done at once and the photo survives being offline. Null if the file is gone.
     */
    suspend fun queuePhoto(shot: File, worldId: Int, position: Int, kind: String, doneAt: String): PendingPhoto? =
        lock.withLock {
            withContext(Dispatchers.IO) {
                val target = File(queued, shot.name)
                if (!shot.renameTo(target)) return@withContext null
                val photo = PendingPhoto(worldId, position, kind, doneAt, target.name)
                val now = read(PHOTOS, photoSerializer) ?: emptyList()
                write(PHOTOS, photoSerializer, PhotoLogic.enqueue(now, photo))
                photo
            }
        }

    /** The server stored [photo] or refused it for good: forget it and delete its file. */
    suspend fun removePhoto(photo: PendingPhoto) = lock.withLock {
        withContext(Dispatchers.IO) {
            val now = read(PHOTOS, photoSerializer) ?: emptyList()
            write(PHOTOS, photoSerializer, now - photo)
            File(queued, photo.file).delete()
        }
    }

    /** Queue files no queued photo names (after a crash between the two writes): deleted. */
    suspend fun pruneQueuedFiles() = lock.withLock {
        withContext(Dispatchers.IO) {
            val queue = read(PHOTOS, photoSerializer) ?: emptyList()
            val names = queued.listFiles()?.map { it.name }.orEmpty()
            PhotoLogic.orphans(names, queue).forEach { File(queued, it).delete() }
        }
    }

    companion object {
        private const val CACHE = "activities.json"
        private const val PENDING = "pending.json"
        private const val PHOTOS = "photos.json"
        private const val REWARDS = "rewards.json"
    }
}
