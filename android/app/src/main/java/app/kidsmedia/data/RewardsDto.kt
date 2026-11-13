package app.kidsmedia.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Wire types of the rewards API (SPEC §14.4). Texts are keyed by language ("de", "en", "el"). */

@Serializable
data class Sticker(
    val position: Int,
    val image: ThumbRef? = null,
    /** Local date the sticker was earned; null = not earned yet. */
    @SerialName("earned_on") val earnedOn: String? = null
)

@Serializable
data class AlbumPage(
    @SerialName("world_id") val worldId: Int,
    val texts: Map<String, WorldTexts> = emptyMap(),
    val cover: ThumbRef? = null,
    val stickers: List<Sticker> = emptyList()
)

@Serializable
data class Swap(
    val id: Int,
    val stars: Int,
    @SerialName("rule_id") val ruleId: Int? = null,
    @SerialName("extra_items") val extraItems: Int? = null,
    @SerialName("extra_minutes") val extraMinutes: Int? = null,
    @SerialName("used_today") val usedToday: Int = 0,
    /** Null = no daily limit. */
    @SerialName("max_per_day") val maxPerDay: Int? = null,
    /** Names of the rule's groups, for the label. */
    val groups: List<String> = emptyList()
)

@Serializable
data class WishTexts(val name: String = "")

@Serializable
data class WishRequest(
    val id: Int,
    /** `pending` | `approved` | `declined`. */
    val status: String = "pending",
    @SerialName("created_at") val createdAt: String? = null
)

@Serializable
data class Wish(
    val id: Int,
    val texts: Map<String, WishTexts> = emptyMap(),
    val stars: Int,
    val request: WishRequest? = null
)

@Serializable
data class Rewards(
    val stars: Int = 0,
    val album: List<AlbumPage> = emptyList(),
    val swaps: List<Swap> = emptyList(),
    val wishes: List<Wish> = emptyList()
)
