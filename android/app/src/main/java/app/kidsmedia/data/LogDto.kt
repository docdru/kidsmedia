package app.kidsmedia.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Wire types of the gem and star logs (SPEC §16.6, §14.4b): why a balance changed. */

@Serializable
data class GemLogEntry(
    val id: Int,
    val delta: Int,
    /** `admin` (a grown-up gave or took gems) | `spend` (a listen). */
    val kind: String,
    /** admin: the grown-up's note, as written. */
    val note: String? = null,
    /** spend: the file's title. */
    val title: String? = null,
    /** spend: the file's picture, while the file exists. */
    val thumb: ThumbRef? = null,
    @SerialName("created_at") val createdAt: String
)

@Serializable
data class GemLog(val balance: Int = 0, val entries: List<GemLogEntry> = emptyList())

@Serializable
data class StarTask(
    @SerialName("world_id") val worldId: Int,
    @SerialName("world_name") val worldName: String = "",
    val position: Int,
    val kind: String = "",
    val title: String = "",
    val picture: ThumbRef? = null
)

@Serializable
data class StarSwap(
    @SerialName("extra_items") val extraItems: Int = 0,
    @SerialName("extra_minutes") val extraMinutes: Int = 0,
    val groups: List<String> = emptyList()
)

@Serializable
data class StarLogEntry(
    val id: Int,
    val delta: Int,
    /** `earn` (before v0.24) | `chapter` | `bonus` | `reopen` | `swap` | `wish`. */
    val kind: String,
    val task: StarTask? = null,
    val swap: StarSwap? = null,
    val wish: Map<String, WishTexts>? = null,
    @SerialName("created_at") val createdAt: String
)

@Serializable
data class StarLog(val stars: Int = 0, val entries: List<StarLogEntry> = emptyList())
