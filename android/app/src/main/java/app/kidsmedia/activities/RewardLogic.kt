package app.kidsmedia.activities

import app.kidsmedia.data.AlbumPage
import app.kidsmedia.data.Sticker
import app.kidsmedia.data.Swap

/** Whether a swap's button can be tapped (SPEC §14.4). */
enum class SwapState { AVAILABLE, NOT_ENOUGH_STARS, LIMIT_REACHED }

/** One place on an album page: the sticker if earned, else a silhouette. */
data class StickerSlot(val position: Int, val sticker: Sticker?) {
    val earned: Boolean get() = sticker?.earnedOn != null
}

object RewardLogic {
    fun swapState(swap: Swap, stars: Int): SwapState {
        val max = swap.maxPerDay
        return when {
            max != null && swap.usedToday >= max -> SwapState.LIMIT_REACHED
            stars < swap.stars -> SwapState.NOT_ENOUGH_STARS
            else -> SwapState.AVAILABLE
        }
    }

    /**
     * The swap's label, built from the amount and the rule's groups (SPEC §14.4), e.g.
     * "1 more story · Audiobooks". [items] and [minutes] give the localized amount.
     * Null if the swap carries no amount.
     */
    fun swapLabel(
        swap: Swap,
        items: (Int) -> String,
        minutes: (Int) -> String,
        join: (String, String) -> String
    ): String? {
        val extraItems = swap.extraItems ?: 0
        val extraMinutes = swap.extraMinutes ?: 0
        val amount = when {
            extraItems > 0 -> items(extraItems)
            extraMinutes > 0 -> minutes(extraMinutes)
            else -> return null
        }
        val groups = swap.groups.map { it.trim() }.filter { it.isNotEmpty() }.joinToString(", ")
        return if (groups.isEmpty()) amount else join(amount, groups)
    }

    /**
     * The places on a page in chapter order: every sticker the server sent, plus silhouettes
     * up to [chapterCount] (known for the current world) for the chapters still to come.
     */
    fun slots(page: AlbumPage, chapterCount: Int?): List<StickerSlot> {
        val byPosition = page.stickers.associateBy { it.position }
        val positions = (byPosition.keys + (1..(chapterCount ?: 0))).toSortedSet()
        return positions.map { StickerSlot(it, byPosition[it]) }
    }
}
