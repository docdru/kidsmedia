package app.kidsmedia

import android.app.Application
import android.os.SystemClock
import app.kidsmedia.activities.ActivityStore
import app.kidsmedia.activities.SpeechCache
import app.kidsmedia.data.KidsApi
import app.kidsmedia.data.Prefs
import app.kidsmedia.data.buildHttpClient
import app.kidsmedia.offline.OfflineStore
import app.kidsmedia.playback.PlaybackController
import app.kidsmedia.playback.VolumeLimiter
import app.kidsmedia.update.Updater
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import coil3.disk.DiskCache
import coil3.disk.directory
import coil3.network.okhttp.OkHttpNetworkFetcherFactory
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.OkHttpClient

/** Process-wide objects shared by the UI and the playback service. */
class Graph(val app: Application) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    val prefs = Prefs(app, scope)
    val http: OkHttpClient = buildHttpClient(prefs)
    val api = KidsApi(http, prefs)
    val offline = OfflineStore(app, http, api)
    val playback = PlaybackController(api, scope, { SystemClock.elapsedRealtime() }, offline)
    val updater = Updater(app, http, api)
    val volume = VolumeLimiter(app, prefs.maxVolumePercent)
    val activities = ActivityStore(app)
    val speech = SpeechCache(app, http, api)
}

class KidsMediaApp :
    Application(),
    SingletonImageLoader.Factory {
    lateinit var graph: Graph
        private set

    override fun onCreate() {
        super.onCreate()
        graph = Graph(this).also { it.volume.start(it.scope) }
    }

    /** Thumbnails need the device token too; their URLs are immutable (SPEC §6.4). */
    override fun newImageLoader(context: PlatformContext): ImageLoader = ImageLoader
        .Builder(context)
        .components { add(OkHttpNetworkFetcherFactory(callFactory = { graph.http })) }
        .diskCache {
            DiskCache
                .Builder()
                .directory(cacheDir.resolve("thumbs"))
                .maxSizeBytes(100L * 1024 * 1024)
                .build()
        }.build()
}
