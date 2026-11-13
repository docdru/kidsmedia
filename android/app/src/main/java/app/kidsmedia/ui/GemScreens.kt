package app.kidsmedia.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.kidsmedia.R
import app.kidsmedia.data.LibraryGroup
import coil3.compose.AsyncImage

/** Gems 💎 on the tablet (SPEC §16.4). */
private const val GEM = "💎"
private const val GEMS_PER_ROW = 5
private const val MISSING_ALPHA = 0.25f
private val GemGround = Color(0xFFDDF3FF)
private val GemInk = Color(0xFF0B4A6F)

/**
 * The balance next to the stars, big and friendly like [StarButton]. On Home a tap opens the
 * gems screen ([onClick], SPEC §16.4); in the player it is only shown. When the balance drops
 * (gems spent at the count), a short "💎 −2" floats up from it.
 */
@Composable
fun GemCounter(balance: Int, modifier: Modifier = Modifier, onClick: (() -> Unit)? = null) {
    val description = stringResource(R.string.gems_a11y, pluralStringResource(R.plurals.gems_count, balance, balance))
    var previous by remember { mutableIntStateOf(balance) }
    var spent by remember { mutableIntStateOf(0) }
    val rise = remember { Animatable(0f) }
    LaunchedEffect(balance) {
        val drop = previous - balance
        previous = balance
        spent = 0
        if (drop > 0) {
            spent = drop
            rise.snapTo(0f)
            rise.animateTo(1f, tween(durationMillis = 1600))
            spent = 0
        }
    }
    Box(modifier) {
        val content = @Composable {
            Row(
                Modifier.height(80.dp).padding(start = 16.dp, end = 28.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Text(GEM, fontSize = 40.sp)
                Text("$balance", fontSize = 36.sp, fontWeight = FontWeight.Black, color = GemInk)
            }
        }
        val shape = RoundedCornerShape(40.dp)
        val described = Modifier.semantics { contentDescription = description }
        if (onClick != null) {
            Surface(onClick, shape = shape, color = GemGround, shadowElevation = 4.dp, modifier = described) {
                content()
            }
        } else {
            Surface(shape = shape, color = GemGround, shadowElevation = 4.dp, modifier = described) { content() }
        }
        if (spent > 0) {
            Text(
                stringResource(R.string.gems_spent, spent),
                fontSize = 28.sp,
                fontWeight = FontWeight.Black,
                color = GemInk,
                modifier =
                Modifier
                    .align(Alignment.TopCenter)
                    .clearAndSetSemantics {}
                    .graphicsLayer {
                        translationY = -rise.value * 96.dp.toPx()
                        alpha = 1f - rise.value
                    }
            )
        }
    }
}

/** "💎 2" on an item row until the file is paid today (SPEC §16.4). */
@Composable
fun GemChip(cost: Int, modifier: Modifier = Modifier) {
    val description = stringResource(R.string.gems_cost_a11y, pluralStringResource(R.plurals.gems_count, cost, cost))
    Text(
        stringResource(R.string.badge_gems, cost),
        fontWeight = FontWeight.Bold,
        color = GemInk,
        modifier =
        modifier
            .clip(RoundedCornerShape(12.dp))
            .background(GemGround)
            .padding(horizontal = 10.dp, vertical = 2.dp)
            .semantics { contentDescription = description }
    )
}

/**
 * The reason screen's gems as pictures (SPEC §16.4): one 💎 per gem needed, the owned ones
 * bright and the missing ones faded, so a child who can't read yet sees what is missing.
 */
@Composable
fun GemPictures(need: Int, have: Int, modifier: Modifier = Modifier) {
    Column(
        modifier.clearAndSetSemantics {},
        verticalArrangement = Arrangement.spacedBy(8.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        gemPictures(need, have).chunked(GEMS_PER_ROW).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                row.forEach { owned ->
                    Text(GEM, fontSize = 64.sp, modifier = Modifier.alpha(if (owned) 1f else MISSING_ALPHA))
                }
            }
        }
    }
}

/**
 * The gems screen, a tap on the balance on Home (SPEC §16.4): the gems owned as pictures, what
 * they open (every group whose new files cost gems, with the price, bright when there are
 * enough), and where gems come from. A tap on a group opens it.
 */
@Composable
fun GemsScreen(base: String, balance: Int, groups: List<LibraryGroup>, onBack: () -> Unit, onGroup: (Int) -> Unit) {
    val priced = groups.filter { it.status.gemCost != null }
    Column(
        Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
            BackButton(onBack)
            Text(stringResource(R.string.gems_title), fontSize = 36.sp, fontWeight = FontWeight.Black, color = Ink)
        }
        val owned = pluralStringResource(R.plurals.gems_count, balance, balance)
        Column(
            Modifier.fillMaxWidth().semantics(mergeDescendants = true) { contentDescription = owned },
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            when {
                balance == 0 -> GemPictures(need = 1, have = 0)
                balance <= MAX_PICTURES -> GemPictures(need = balance, have = balance)
                else -> Text("$GEM $balance", fontSize = 64.sp, fontWeight = FontWeight.Black, color = GemInk)
            }
            Text(
                if (balance == 0) stringResource(R.string.gems_none) else owned,
                fontSize = 26.sp,
                fontWeight = FontWeight.Bold,
                color = GemInk
            )
        }
        if (priced.isNotEmpty()) {
            Text(stringResource(R.string.gems_opens), fontSize = 24.sp, fontWeight = FontWeight.Black, color = Ink)
            LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                items(priced, key = { it.id }) { g -> GemGroupRow(base, g, balance) { onGroup(g.id) } }
            }
        } else {
            Spacer(Modifier.weight(1f))
        }
        Text(stringResource(R.string.gems_from), fontSize = 20.sp, color = Muted)
    }
}

@Composable
private fun GemGroupRow(base: String, group: LibraryGroup, balance: Int, onClick: () -> Unit) {
    val cost = group.status.gemCost ?: return
    val enough = balance >= cost
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(24.dp),
        color = Color.White,
        shadowElevation = 2.dp,
        modifier = Modifier.fillMaxWidth().alpha(if (enough) 1f else 0.6f)
    ) {
        Row(
            Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Box(Modifier.size(72.dp).clip(RoundedCornerShape(16.dp)).background(groupColor(group.color))) {
                AsyncImage(
                    model = thumbUrl(base, group.cover, "sm"),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize()
                )
            }
            Text(
                group.name,
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
                color = Ink,
                maxLines = 2,
                modifier = Modifier.weight(1f)
            )
            GemChip(cost)
            if (!enough) Icon(Icons.Filled.Lock, contentDescription = null, tint = Ink)
        }
    }
}

/** More gems than this are shown as a number, not as pictures. */
private const val MAX_PICTURES = 15
