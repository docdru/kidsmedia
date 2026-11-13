package app.kidsmedia.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.kidsmedia.R
import app.kidsmedia.activities.ActivitiesView
import app.kidsmedia.activities.ActivityLogic
import app.kidsmedia.activities.RewardLogic
import app.kidsmedia.activities.StickerSlot
import app.kidsmedia.activities.SwapState
import app.kidsmedia.data.AlbumPage
import app.kidsmedia.data.Rewards
import app.kidsmedia.data.Swap
import app.kidsmedia.data.Wish
import coil3.compose.AsyncImage

// Rewards (SPEC §14.4): stars, the sticker album, swaps and wishes.
private val StarGold = Color(0xFFFFB800)
private val StarGround = Color(0xFFFFF1C2)
private val StarInk = Color(0xFF6B4A00)
private val SwapGreen = Color(0xFF17784A)
private val WishPink = Color(0xFFB0306A)
private val SilhouetteGround = Color(0xFFE3E8F2)

// ---- star counter (Home, world tile) ------------------------------------------------

/** The big star button in the Home top bar: the balance, and the way to the Rewards screen. */
@Composable
fun StarButton(stars: Int, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val description = stringResource(
        R.string.rewards_button_a11y,
        pluralStringResource(R.plurals.reward_stars, stars, stars)
    )
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(40.dp),
        color = StarGround,
        shadowElevation = 4.dp,
        modifier = modifier.semantics { contentDescription = description }
    ) {
        Row(
            Modifier.height(80.dp).padding(start = 16.dp, end = 28.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Icon(Icons.Filled.Star, contentDescription = null, tint = StarGold, modifier = Modifier.size(56.dp))
            Text("$stars", fontSize = 36.sp, fontWeight = FontWeight.Black, color = StarInk)
        }
    }
}

/** A small star counter, on the world tile's picture. */
@Composable
fun StarChip(stars: Int, modifier: Modifier = Modifier) {
    val description = pluralStringResource(R.plurals.reward_stars, stars, stars)
    Row(
        modifier
            .clip(RoundedCornerShape(20.dp))
            .background(Color.White)
            .padding(start = 8.dp, end = 14.dp, top = 4.dp, bottom = 4.dp)
            .semantics { contentDescription = description },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        Icon(Icons.Filled.Star, contentDescription = null, tint = StarGold, modifier = Modifier.size(28.dp))
        Text("$stars", fontSize = 20.sp, fontWeight = FontWeight.Black, color = StarInk)
    }
}

// ---- rewards screen -----------------------------------------------------------------

@Composable
fun RewardsScreen(
    base: String,
    rewards: Rewards,
    activities: ActivitiesView?,
    offline: Boolean,
    notice: RewardNotice?,
    busy: Boolean,
    onBack: () -> Unit,
    onSwap: (Int) -> Unit,
    onWish: (Int) -> Unit
) {
    // Swaps and wishes need the server; offline the cached rewards are only shown.
    val cantAct = offline || notice == RewardNotice.OFFLINE
    val current = activities?.world
    Column(Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
            BackButton(onBack)
            Text(
                stringResource(R.string.rewards_title),
                fontSize = 36.sp,
                fontWeight = FontWeight.Black,
                color = Ink,
                modifier = Modifier.weight(1f)
            )
            val balance = pluralStringResource(R.plurals.reward_stars, rewards.stars, rewards.stars)
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.semantics(mergeDescendants = true) { contentDescription = balance }
            ) {
                Icon(Icons.Filled.Star, contentDescription = null, tint = StarGold, modifier = Modifier.size(64.dp))
                Text("${rewards.stars}", fontSize = 44.sp, fontWeight = FontWeight.Black, color = StarInk)
            }
        }
        when {
            cantAct -> Notice(stringResource(R.string.rewards_offline), Color(0xFF8A5300))
            notice == RewardNotice.SWAPPED -> Notice(stringResource(R.string.reward_swapped), SwapGreen)
        }
        Spacer(Modifier.height(16.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(bottom = 120.dp)) {
            item(key = "album") { SectionTitle(stringResource(R.string.rewards_album)) }
            if (rewards.album.isEmpty()) {
                item(key = "album-empty") {
                    Text(stringResource(R.string.rewards_album_empty), fontSize = 22.sp, color = Muted)
                }
            }
            items(rewards.album, key = { "page-${it.worldId}" }) { page ->
                val chapters = current?.takeIf { it.id == page.worldId }?.chapters?.size
                AlbumPageCard(base, page, RewardLogic.slots(page, chapters))
            }
            if (rewards.swaps.isNotEmpty()) {
                item(key = "swaps") { SectionTitle(stringResource(R.string.rewards_swaps)) }
                items(rewards.swaps, key = { "swap-${it.id}" }) { swap ->
                    SwapRow(swap, rewards.stars, enabled = !cantAct && !busy) { onSwap(swap.id) }
                }
            }
            if (rewards.wishes.isNotEmpty()) {
                item(key = "wishes") { SectionTitle(stringResource(R.string.rewards_wishes)) }
                items(rewards.wishes, key = { "wish-${it.id}" }) { wish ->
                    WishRow(wish, rewards.stars, enabled = !cantAct && !busy) { onWish(wish.id) }
                }
            }
        }
    }
}

@Composable
private fun Notice(text: String, ground: Color) {
    Text(
        text,
        color = Color.White,
        fontSize = 20.sp,
        fontWeight = FontWeight.Bold,
        modifier = Modifier
            .padding(top = 12.dp)
            .clip(RoundedCornerShape(14.dp))
            .background(ground)
            .padding(horizontal = 16.dp, vertical = 12.dp)
    )
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, fontSize = 28.sp, fontWeight = FontWeight.Black, color = Ink, modifier = Modifier.padding(top = 8.dp))
}

// ---- album --------------------------------------------------------------------------

@Composable
private fun AlbumPageCard(base: String, page: AlbumPage, slots: List<StickerSlot>) {
    val name = ActivityLogic.pick(page.texts, language())?.second?.name.orEmpty()
    Surface(shape = RoundedCornerShape(28.dp), shadowElevation = 3.dp, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text(name, fontSize = 24.sp, fontWeight = FontWeight.Black, color = Ink, maxLines = 1)
            LazyRow(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                items(slots, key = { it.position }) { slot -> StickerTile(base, page, slot) }
            }
        }
    }
}

/**
 * An earned sticker is the chapter picture (else the world cover, else a star), loaded like
 * the story's pictures so it shows offline once seen. A sticker still to come is a silhouette.
 */
@Composable
private fun StickerTile(base: String, page: AlbumPage, slot: StickerSlot) {
    val description = if (slot.earned) {
        stringResource(R.string.rewards_sticker_earned, slot.position)
    } else {
        stringResource(R.string.rewards_sticker_locked, slot.position)
    }
    val shape = RoundedCornerShape(22.dp)
    Box(
        Modifier
            .size(128.dp)
            .clip(shape)
            .background(if (slot.earned) StarGround else SilhouetteGround)
            .semantics { contentDescription = description },
        contentAlignment = Alignment.Center
    ) {
        val picture = slot.sticker?.image ?: page.cover
        when {
            slot.earned && picture != null -> {
                AsyncImage(
                    model = thumbUrl(base, picture, "md"),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize()
                )
            }
            slot.earned -> {
                Icon(Icons.Filled.Star, contentDescription = null, tint = StarGold, modifier = Modifier.size(72.dp))
            }
            else -> {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(Icons.Filled.Lock, contentDescription = null, tint = Muted, modifier = Modifier.size(32.dp))
                    Text("${slot.position}", fontSize = 26.sp, fontWeight = FontWeight.Black, color = Muted)
                }
            }
        }
    }
}

// ---- swaps and wishes ---------------------------------------------------------------

@Composable
private fun swapLabel(swap: Swap): String {
    // LocalResources (not LocalContext) so the text follows a configuration change.
    val resources = LocalResources.current
    return RewardLogic.swapLabel(
        swap,
        items = { resources.getQuantityString(R.plurals.reward_swap_items, it, it) },
        minutes = { resources.getQuantityString(R.plurals.reward_swap_minutes, it, it) },
        join = { amount, groups -> resources.getString(R.string.reward_swap_label, amount, groups) }
    ) ?: swap.groups.joinToString(", ")
}

@Composable
private fun RewardRow(title: String, subtitle: String?, stars: Int, accent: Color, action: @Composable () -> Unit) {
    Surface(shape = RoundedCornerShape(26.dp), shadowElevation = 2.dp, modifier = Modifier.fillMaxWidth()) {
        Row(
            Modifier.padding(horizontal = 22.dp, vertical = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(18.dp)
        ) {
            Box(
                Modifier.size(64.dp).clip(CircleShape).background(accent.copy(alpha = 0.14f)),
                contentAlignment = Alignment.Center
            ) {
                Icon(Icons.Filled.Star, contentDescription = null, tint = StarGold, modifier = Modifier.size(40.dp))
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(title, fontSize = 26.sp, fontWeight = FontWeight.Black, color = Ink, maxLines = 2)
                Text(
                    pluralStringResource(R.plurals.reward_stars, stars, stars),
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                    color = StarInk
                )
                subtitle?.let { Text(it, fontSize = 18.sp, fontWeight = FontWeight.Bold, color = Muted) }
            }
            action()
        }
    }
}

@Composable
private fun BigButton(text: String, color: Color, enabled: Boolean, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        enabled = enabled,
        shape = RoundedCornerShape(40.dp),
        color = color,
        modifier = Modifier.alpha(if (enabled) 1f else 0.4f)
    ) {
        Box(Modifier.heightIn(min = 76.dp).padding(horizontal = 28.dp), contentAlignment = Alignment.Center) {
            Text(text, fontSize = 24.sp, fontWeight = FontWeight.Black, color = Color.White)
        }
    }
}

@Composable
private fun StatusChip(text: String, ground: Color, color: Color) {
    Text(
        text,
        fontSize = 22.sp,
        fontWeight = FontWeight.Black,
        color = color,
        modifier = Modifier
            .clip(RoundedCornerShape(22.dp))
            .background(ground)
            .padding(horizontal = 18.dp, vertical = 12.dp)
    )
}

@Composable
private fun SwapRow(swap: Swap, stars: Int, enabled: Boolean, onSwap: () -> Unit) {
    val state = RewardLogic.swapState(swap, stars)
    val subtitle = when (state) {
        SwapState.LIMIT_REACHED -> stringResource(R.string.reward_swap_limit)
        SwapState.NOT_ENOUGH_STARS -> {
            val missing = swap.stars - stars
            pluralStringResource(R.plurals.reward_need_more, missing, missing)
        }
        SwapState.AVAILABLE -> null
    }
    RewardRow(swapLabel(swap), subtitle, swap.stars, SwapGreen) {
        BigButton(
            pluralStringResource(R.plurals.reward_swap_button, swap.stars, swap.stars),
            SwapGreen,
            enabled = enabled && state == SwapState.AVAILABLE,
            onClick = onSwap
        )
    }
}

@Composable
private fun WishRow(wish: Wish, stars: Int, enabled: Boolean, onWish: () -> Unit) {
    val name = ActivityLogic.pick(wish.texts, language())?.second?.name.orEmpty()
    val status = wish.request?.status
    val subtitle = if (status != "pending" && stars < wish.stars) {
        val missing = wish.stars - stars
        pluralStringResource(R.plurals.reward_need_more, missing, missing)
    } else {
        null
    }
    RewardRow(name, subtitle, wish.stars, WishPink) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            when (status) {
                "pending" -> StatusChip(stringResource(R.string.wish_pending), Color(0xFFFFE9D6), Color(0xFF7A2E0C))
                "approved" -> StatusChip(stringResource(R.string.wish_approved), Color(0xFFDDF3E8), Color(0xFF135C3A))
                "declined" -> StatusChip(stringResource(R.string.wish_declined), Color(0xFFEEF2F9), Color(0xFF33405F))
            }
            // One pending request per wish (SPEC §14.4); after an answer the child may wish again.
            if (status != "pending") {
                BigButton(
                    stringResource(R.string.wish_ask),
                    WishPink,
                    enabled = enabled && stars >= wish.stars,
                    onClick = onWish
                )
            }
        }
    }
}
