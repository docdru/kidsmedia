package app.kidsmedia.data

import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.serializer
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody

/** An error answer from the server: HTTP status plus reason code and params (SPEC §3.2). */
class ApiException(val status: Int, val reason: String, val params: Params = emptyMap()) :
    IOException("HTTP $status: $reason")

/** Where the server is and how to authenticate. Read on every request. */
interface Credentials {
    val baseUrl: String?
    val token: String?
}

/** Adds `Authorization: Bearer …` to every request for our server (API, streams, thumbnails). */
class AuthInterceptor(private val credentials: Credentials) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): okhttp3.Response {
        val request = chain.request()
        val base = credentials.baseUrl
        val token = credentials.token
        if (base == null || token == null || !isOurs(request.url.toString(), base)) {
            return chain.proceed(request)
        }
        return chain.proceed(request.newBuilder().header("Authorization", "Bearer $token").build())
    }

    companion object {
        /** `https://kids.example` must not match `https://kids.example.evil.net/…`. */
        fun isOurs(url: String, base: String): Boolean =
            url == base || url.startsWith("$base/") || url.startsWith("$base?")
    }
}

fun buildHttpClient(credentials: Credentials): OkHttpClient = OkHttpClient
    .Builder()
    .addInterceptor(AuthInterceptor(credentials))
    .connectTimeout(15, TimeUnit.SECONDS)
    .readTimeout(30, TimeUnit.SECONDS)
    .retryOnConnectionFailure(true)
    .build()

class KidsApi(private val client: OkHttpClient, private val credentials: Credentials) {
    private val jsonType = "application/json".toMediaType()
    private val jpegType = "image/jpeg".toMediaType()

    fun url(path: String): String = (credentials.baseUrl ?: error("no server configured")) + path

    suspend fun pair(baseUrl: String, request: PairRequest): PairResponse =
        call(post(baseUrl + "/api/pair", request), PairResponse.serializer())

    suspend fun snapshot(): Snapshot =
        call(Request.Builder().url(url("/api/offline/snapshot")).get().build(), Snapshot.serializer())

    suspend fun batch(request: BatchRequest): BatchResponse =
        call(post(url("/api/play/batch"), request), BatchResponse.serializer())

    suspend fun latestApp(): AppRelease =
        call(Request.Builder().url(url("/api/app/latest")).get().build(), AppRelease.serializer())

    suspend fun me(): DeviceMe = call(Request.Builder().url(url("/api/me")).get().build(), DeviceMe.serializer())

    suspend fun library(): Library =
        call(Request.Builder().url(url("/api/library")).get().build(), Library.serializer())

    suspend fun activities(): Activities =
        call(Request.Builder().url(url("/api/activities")).get().build(), Activities.serializer())

    suspend fun activityDone(request: DoneRequest): Activities =
        call(post(url("/api/activities/done"), request), Activities.serializer())

    /**
     * A photo of a drawn or written task (SPEC §15.3): the raw JPEG [file] as the body, the task and
     * the time it was taken in the query. Judged like [activityDone]; answers the same body as GET.
     */
    suspend fun activityPhoto(photo: PendingPhoto, file: File): Activities {
        val url = url("/api/activities/photo")
            .toHttpUrl()
            .newBuilder()
            .addQueryParameter("world_id", photo.worldId.toString())
            .addQueryParameter("position", photo.position.toString())
            .addQueryParameter("kind", photo.kind)
            .addQueryParameter("done_at", photo.doneAt)
            .build()
        val request = Request
            .Builder()
            .url(url)
            .post(file.asRequestBody(jpegType))
            .build()
        return call(request, Activities.serializer())
    }

    suspend fun rewards(): Rewards =
        call(Request.Builder().url(url("/api/rewards")).get().build(), Rewards.serializer())

    suspend fun redeemSwap(swapId: Int): Rewards =
        call(postEmpty(url("/api/rewards/swaps/$swapId")), Rewards.serializer())

    suspend fun requestWish(wishId: Int): Rewards =
        call(postEmpty(url("/api/rewards/wishes/$wishId/request")), Rewards.serializer())

    suspend fun start(mediaId: Int): StartResponse =
        call(post(url("/api/play/start"), StartRequest(mediaId)), StartResponse.serializer())

    suspend fun heartbeat(request: HeartbeatRequest): HeartbeatResponse =
        call(post(url("/api/play/heartbeat"), request), HeartbeatResponse.serializer())

    private inline fun <reified T> post(url: String, body: T): Request = Request
        .Builder()
        .url(url)
        .post(ApiJson.encodeToString(serializer<T>(), body).toRequestBody(jsonType))
        .build()

    private fun postEmpty(url: String): Request = Request
        .Builder()
        .url(url)
        .post("{}".toRequestBody(jsonType))
        .build()

    private suspend fun <T> call(request: Request, deserializer: KSerializer<T>): T = withContext(Dispatchers.IO) {
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val error = runCatching { ApiJson.decodeFromString(ApiErrorBody.serializer(), text) }.getOrNull()
                throw ApiException(
                    response.code,
                    error?.reason ?: "http_${response.code}",
                    error?.params ?: emptyMap()
                )
            }
            ApiJson.decodeFromString(deserializer, text)
        }
    }
}
