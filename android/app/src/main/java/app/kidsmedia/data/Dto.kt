package app.kidsmedia.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull

/** Wire types of the server API (openapi.json, SPEC §5). */

val ApiJson =
    Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        encodeDefaults = true
    }

typealias Params = Map<String, JsonElement?>

fun Params.string(key: String): String? = (this[key] as? JsonPrimitive)?.takeUnless { it.content == "null" }?.content

fun Params.int(key: String): Int? = (this[key] as? JsonPrimitive)?.intOrNull

@Serializable
data class PairRequest(
    val code: String,
    @SerialName("device_name") val deviceName: String? = null,
    @SerialName("app_version") val appVersion: String? = null
)

@Serializable
data class PairResponse(
    val token: String,
    @SerialName("device_id") val deviceId: Int,
    @SerialName("profile_id") val profileId: Int
)

@Serializable
data class ThumbRef(val url: String, val aspect: Double = 1.0, val source: String? = null)

@Serializable
data class Status(
    val allowed: Boolean = true,
    val reason: String? = null,
    val params: Params = emptyMap(),
    @SerialName("items_left") val itemsLeft: Int? = null,
    @SerialName("minutes_left") val minutesLeft: Int? = null,
    @SerialName("unlocks_at") val unlocksAt: String? = null,
    @SerialName("remaining_seconds") val remainingSeconds: Int? = null,
    /** Gems 💎 a new file of the group needs today, null = none (SPEC §16.3). */
    @SerialName("gem_cost") val gemCost: Int? = null
)

@Serializable
data class LibraryItem(
    val id: Int,
    val title: String,
    @SerialName("duration_s") val durationS: Double,
    val thumb: ThumbRef? = null,
    @SerialName("position_ms") val positionMs: Long = 0,
    @SerialName("played_today") val playedToday: Boolean = false,
    val allowed: Boolean = true,
    val offline: Boolean = false,
    val size: Long = 0,
    /** It counts today for every gem rule that covers it: no more gems needed today (SPEC §16.3). */
    @SerialName("paid_today") val paidToday: Boolean = false
)

@Serializable
data class LibraryGroup(
    val id: Int,
    val name: String,
    val kind: String,
    val color: String,
    val cover: ThumbRef? = null,
    val status: Status = Status(),
    val items: List<LibraryItem> = emptyList(),
    val offline: Boolean = false
) {
    val isVideo: Boolean get() = kind == "video"
}

@Serializable
data class Library(
    @SerialName("server_time") val serverTime: String,
    val groups: List<LibraryGroup>,
    /** This tablet's volume cap in % of the maximum, null = none (set in the admin). */
    @SerialName("max_volume_percent") val maxVolumePercent: Int? = null,
    /** The gem balance (SPEC §16.3). Older servers send none: no gems, nothing shown. */
    val gems: Gems = Gems()
)

/** [used]: an enabled gem rule exists, so the tablet shows the balance (SPEC §16.3). */
@Serializable
data class Gems(val balance: Int = 0, val used: Boolean = false)

@Serializable
data class Grace(
    val active: Boolean = false,
    @SerialName("until_position_ms") val untilPositionMs: Long? = null,
    @SerialName("rule_id") val ruleId: Int? = null,
    @SerialName("expires_at") val expiresAt: String? = null
)

@Serializable
data class StartRequest(@SerialName("media_id") val mediaId: Int)

@Serializable
data class StartResponse(
    val allowed: Boolean,
    val reason: String? = null,
    val params: Params = emptyMap(),
    @SerialName("remaining_seconds") val remainingSeconds: Int? = null,
    val grace: Grace = Grace(),
    @SerialName("play_session_id") val playSessionId: String? = null,
    @SerialName("position_ms") val positionMs: Long = 0,
    @SerialName("stream_url") val streamUrl: String? = null
)

@Serializable
data class HeartbeatRequest(
    @SerialName("media_id") val mediaId: Int,
    @SerialName("play_session_id") val playSessionId: String,
    @SerialName("played_seconds_total") val playedSecondsTotal: Double,
    @SerialName("position_ms") val positionMs: Long
)

@Serializable
data class HeartbeatResponse(
    val allowed: Boolean,
    val reason: String? = null,
    val params: Params = emptyMap(),
    @SerialName("remaining_seconds") val remainingSeconds: Int? = null,
    val grace: Grace = Grace()
)

@Serializable
data class ApiErrorBody(val reason: String, val params: Params = emptyMap())

@Serializable
data class DeviceMe(
    @SerialName("device_id") val deviceId: Int,
    val name: String,
    @SerialName("profile_id") val profileId: Int,
    @SerialName("time_zone") val timeZone: String = "",
    @SerialName("max_volume_percent") val maxVolumePercent: Int? = null
)

/** Latest app on the server (SPEC §4.3 self-update). */
@Serializable
data class AppRelease(
    @SerialName("version_name") val versionName: String,
    @SerialName("version_code") val versionCode: Int,
    val size: Long,
    val sha256: String,
    val url: String = "/api/app/apk"
)

// ---- offline mode (SPEC §5.3a, M7) ----

@Serializable
data class SnapshotLockout(val start: String, val end: String)

@Serializable
data class SnapshotRule(
    val id: Int,
    val days: List<Int>,
    @SerialName("all_groups") val allGroups: Boolean = false,
    @SerialName("group_ids") val groupIds: List<Int> = emptyList(),
    val mode: String = "limit",
    @SerialName("max_items") val maxItems: Int? = null,
    @SerialName("max_minutes") val maxMinutes: Int? = null,
    val lockouts: List<SnapshotLockout> = emptyList(),
    @SerialName("grace_minutes") val graceMinutes: Int = 0,
    @SerialName("count_after_seconds") val countAfterSeconds: Int = 0,
    @SerialName("replay_percent") val replayPercent: Int? = null,
    @SerialName("gem_cost") val gemCost: Int? = null,
    @SerialName("message_en") val messageEn: String? = null,
    @SerialName("message_de") val messageDe: String? = null,
    @SerialName("message_el") val messageEl: String? = null
)

@Serializable
data class SnapshotUsage(
    val day: String,
    val started: Map<Int, Int> = emptyMap(),
    val seconds: Map<Int, Double> = emptyMap(),
    val played: Map<Int, Double> = emptyMap(),
    @SerialName("extra_items") val extraItems: Map<Int, Int> = emptyMap(),
    @SerialName("extra_minutes") val extraMinutes: Map<Int, Int> = emptyMap(),
    /** The profile's gem balance when the snapshot was taken (SPEC §16.2); not per day. */
    @SerialName("gem_balance") val gemBalance: Int = 0,
    /** Rule id -> the files whose gems the server has booked on [day] (SPEC §16.2). */
    @SerialName("gems_paid") val gemsPaid: Map<Int, List<Int>> = emptyMap()
)

@Serializable
data class Snapshot(
    @SerialName("server_time") val serverTime: String,
    @SerialName("time_zone") val timeZone: String,
    @SerialName("group_ids") val groupIds: List<Int>,
    val rules: List<SnapshotRule>,
    val usage: SnapshotUsage
)

/** One play while offline, booked by the server once (event_id) after reconnecting. */
@Serializable
data class OfflineEvent(
    @SerialName("event_id") val eventId: String,
    @SerialName("media_id") val mediaId: Int,
    @SerialName("group_id") val groupId: Int,
    @SerialName("started_at") val startedAt: String,
    val seconds: Double,
    @SerialName("position_ms") val positionMs: Long
)

@Serializable
data class BatchRequest(val events: List<OfflineEvent>)

@Serializable
data class BatchResponse(val accepted: Int, val duplicates: Int, @SerialName("unknown_media") val unknownMedia: Int)
