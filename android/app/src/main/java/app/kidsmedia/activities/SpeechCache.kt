package app.kidsmedia.activities

import android.content.Context
import android.util.Log
import app.kidsmedia.data.Activities
import app.kidsmedia.data.KidsApi
import java.io.File
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * Speech files kept for offline use (SPEC §14.3): `filesDir/speech/<key>.m4a`. Keys are content
 * hashes, so a file never changes; a changed text has a new key, and old files are pruned once
 * the activities no longer mention them.
 */
class SpeechCache(context: Context, private val http: OkHttpClient, private val api: KidsApi) {
    private val dir = File(context.filesDir, "speech").apply { mkdirs() }
    private val lock = Mutex()

    fun file(key: String): File? = File(dir, SpeechLogic.fileName(key)).takeIf { it.isFile && it.length() > 0 }

    /** After fresh activities: drop what they no longer mention, fetch what Listen plays next. */
    suspend fun sync(data: Activities, language: String) = lock.withLock {
        withContext(Dispatchers.IO) {
            val referenced = SpeechLogic.referencedKeys(data)
            dir.listFiles()?.forEach { f -> if (SpeechLogic.stale(f.name, referenced)) f.delete() }
            val world = data.world ?: return@withContext
            for (path in SpeechLogic.prefetch(world, language)) {
                val key = SpeechLogic.keyOf(path) ?: continue
                if (file(key) == null) download(key, path)
            }
        }
    }

    private fun download(key: String, path: String) {
        val tmp = File(dir, "$key.part")
        try {
            http.newCall(Request.Builder().url(api.url(path)).build()).execute().use { r ->
                if (!r.isSuccessful) return
                tmp.outputStream().use { out -> r.body?.byteStream()?.copyTo(out) }
            }
            tmp.renameTo(File(dir, SpeechLogic.fileName(key)))
        } catch (e: IOException) {
            Log.w("kidsmedia", "speech download $key failed: $e")
        } finally {
            tmp.delete()
        }
    }
}
