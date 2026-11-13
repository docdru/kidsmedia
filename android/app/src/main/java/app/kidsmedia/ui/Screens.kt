package app.kidsmedia.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.OptIn
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.PlayerView
import app.kidsmedia.R
import app.kidsmedia.activities.ActivitiesView
import app.kidsmedia.data.LibraryGroup
import app.kidsmedia.data.LibraryItem
import app.kidsmedia.data.ThumbRef
import app.kidsmedia.playback.Block
import app.kidsmedia.playback.NowPlaying
import coil3.compose.AsyncImage
import kotlinx.coroutines.delay

val Ink = Color(0xFF1B2440)
val Muted = Color(0xFF4A5578)
val Ground = Color(0xFFF2F6FC)

fun groupColor(hex: String): Color =
    runCatching { Color(android.graphics.Color.parseColor(hex)) }.getOrDefault(Color(0xFF4552C7))

fun thumbUrl(base: String, ref: ThumbRef?, size: String): String? = ref?.let { "$base${it.url}?size=$size" }

// ---- first run (SPEC §8.6) ----------------------------------------------------------

@Composable
fun SetupScreen(error: SetupError?, busy: Boolean, onPair: (String, String) -> Unit) {
    var server by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    Box(Modifier.fillMaxSize().background(Ground), contentAlignment = Alignment.Center) {
        Surface(shape = RoundedCornerShape(28.dp), shadowElevation = 6.dp) {
            Column(
                Modifier.width(520.dp).padding(32.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                Text(stringResource(R.string.setup_title), fontSize = 30.sp, fontWeight = FontWeight.Black, color = Ink)
                Text(stringResource(R.string.setup_help), color = Muted)
                OutlinedTextField(
                    value = server,
                    onValueChange = { server = it },
                    label = { Text(stringResource(R.string.setup_server)) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = code,
                    onValueChange = { code = it.filter(Char::isDigit).take(6) },
                    label = { Text(stringResource(R.string.setup_code)) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    modifier = Modifier.fillMaxWidth()
                )
                error?.let {
                    val text =
                        when (it) {
                            SetupError.INVALID_URL -> R.string.setup_invalid_url
                            SetupError.HTTPS_REQUIRED -> R.string.setup_https_required
                            SetupError.BAD_CODE -> R.string.setup_bad_code
                            SetupError.OFFLINE -> R.string.setup_offline
                            SetupError.RATE_LIMITED -> R.string.setup_rate_limited
                        }
                    Text(stringResource(text), color = Color(0xFFA3332A), fontWeight = FontWeight.Bold)
                }
                Button(
                    onClick = { onPair(server, code) },
                    enabled = !busy && server.isNotBlank() && code.length == 6,
                    modifier = Modifier.fillMaxWidth().height(56.dp)
                ) {
                    if (busy) {
                        CircularProgressIndicator(
                            Modifier.size(24.dp)
                        )
                    } else {
                        Text(stringResource(R.string.setup_pair))
                    }
                }
            }
        }
    }
}

// ---- home (SPEC §8.1) ---------------------------------------------------------------

@Composable
fun HomeScreen(
    base: String,
    groups: List<LibraryGroup>,
    offline: Boolean,
    onTap: (Int) -> Unit,
    activities: ActivitiesView? = null,
    onStory: () -> Unit = {},
    stars: Int? = null,
    onRewards: () -> Unit = {},
    // The gem balance while gems are used (SPEC §16.4), null = not shown.
    gems: Int? = null,
    onGems: () -> Unit = {}
) {
    val context = LocalContext.current
    Column(Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 32.dp)) {
        HomeHeader(stars, onRewards, gems, onGems)
        if (offline) OfflineBanner()
        Spacer(Modifier.height(24.dp))
        val world = activities?.takeIf { it.world != null }
        if (groups.isEmpty() && world == null) {
            Text(stringResource(R.string.home_empty), fontSize = 22.sp, color = Muted)
        }
        LazyVerticalGrid(
            columns = GridCells.Adaptive(240.dp),
            horizontalArrangement = Arrangement.spacedBy(24.dp),
            verticalArrangement = Arrangement.spacedBy(24.dp),
            contentPadding = PaddingValues(bottom = 120.dp)
        ) {
            // The current story world comes first (SPEC §13.7).
            if (world != null) item(key = "world") { WorldTile(base, world, onStory, stars) }
            items(groups, key = { it.id }) { g ->
                val locked = !g.status.allowed && g.status.reason != "item_limit"
                val badge = badgeText(context, g.status)
                Surface(
                    shape = RoundedCornerShape(28.dp),
                    shadowElevation = 4.dp,
                    modifier =
                    Modifier
                        .alpha(if (locked) 0.55f else 1f)
                        .clickable { onTap(g.id) }
                        .semantics { contentDescription = "${g.name}, $badge" }
                ) {
                    Column {
                        Box(Modifier.fillMaxWidth().aspectRatio(1f).background(groupColor(g.color))) {
                            AsyncImage(
                                model = thumbUrl(base, g.cover, "md"),
                                contentDescription = null,
                                contentScale = ContentScale.Crop,
                                modifier = Modifier.fillMaxSize()
                            )
                            if (locked) {
                                Box(
                                    Modifier.align(
                                        Alignment.TopEnd
                                    ).padding(14.dp).size(52.dp).clip(CircleShape).background(Color.White),
                                    contentAlignment = Alignment.Center
                                ) { Icon(Icons.Filled.Lock, contentDescription = null, tint = Ink) }
                            }
                        }
                        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            Text(g.name, fontSize = 24.sp, fontWeight = FontWeight.Black, color = Ink, maxLines = 2)
                            Text(
                                badge,
                                fontSize = 17.sp,
                                fontWeight = FontWeight.Bold,
                                color = Ink,
                                modifier = Modifier.clip(
                                    RoundedCornerShape(16.dp)
                                ).background(Color(0xFFE7F0FF)).padding(horizontal = 12.dp, vertical = 6.dp)
                            )
                        }
                    }
                }
            }
        }
    }
}

/**
 * Hello, then the language button (SPEC §14.7), the stars (§14.4) and the gems (§16.4), clear
 * of the parent dot in the corner. On a narrow screen (a tablet held upright) the buttons go
 * on their own line under the greeting, so neither is squeezed.
 */
@Composable
private fun HomeHeader(stars: Int?, onRewards: () -> Unit, gems: Int?, onGems: () -> Unit) {
    val hello = @Composable { modifier: Modifier ->
        Text(
            stringResource(R.string.home_hello),
            fontSize = 44.sp,
            fontWeight = FontWeight.Black,
            color = Ink,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = modifier
        )
    }
    val buttons = @Composable {
        Row(
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(end = 40.dp)
        ) {
            LanguageFlags()
            stars?.let { StarButton(it, onRewards) }
            gems?.let { GemCounter(it, onClick = onGems) }
        }
    }
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        if (maxWidth < HEADER_ONE_LINE) {
            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                hello(Modifier.padding(end = 40.dp))
                buttons()
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                hello(Modifier.weight(1f))
                buttons()
            }
        }
    }
}

/** Below this width Home's greeting and buttons don't fit on one line. */
private val HEADER_ONE_LINE = 760.dp

@Composable
fun OfflineBanner() {
    Text(
        stringResource(R.string.offline_banner),
        color = Color.White,
        fontWeight = FontWeight.Bold,
        modifier = Modifier.padding(
            top = 8.dp
        ).clip(RoundedCornerShape(12.dp)).background(Color(0xFF8A5300)).padding(12.dp)
    )
}

// ---- group (SPEC §8.2) --------------------------------------------------------------

@Composable
fun GroupScreen(base: String, group: LibraryGroup, onBack: () -> Unit, onPlay: (Int) -> Unit) {
    val context = LocalContext.current
    Column(Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
            BackButton(onBack)
            AsyncImage(
                model = thumbUrl(base, group.cover, "sm"),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.size(72.dp).clip(RoundedCornerShape(18.dp)).background(groupColor(group.color))
            )
            Column {
                Text(group.name, fontSize = 36.sp, fontWeight = FontWeight.Black, color = Ink)
                Text(badgeText(context, group.status), fontSize = 18.sp, fontWeight = FontWeight.Bold, color = Muted)
            }
        }
        Spacer(Modifier.height(20.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(bottom = 120.dp)) {
            itemsIndexed(group.items, key = { _, it -> it.id }) { index, item ->
                ItemRow(base, group, item) { onPlay(index) }
            }
        }
    }
}

@Composable
private fun ItemRow(base: String, group: LibraryGroup, item: LibraryItem, onClick: () -> Unit) {
    val minutes = (item.durationS / 60).toInt().coerceAtLeast(1)
    val progress = if (group.isVideo ||
        group.kind == "audiobook"
    ) {
        (item.positionMs / 1000.0 / item.durationS).toFloat()
    } else {
        0f
    }
    Surface(
        shape = RoundedCornerShape(22.dp),
        shadowElevation = 2.dp,
        modifier = Modifier.fillMaxWidth().alpha(if (item.allowed) 1f else 0.5f).clickable(onClick = onClick)
    ) {
        Row(
            Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(18.dp)
        ) {
            AsyncImage(
                model = thumbUrl(base, item.thumb, "sm"),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier =
                Modifier
                    .height(76.dp)
                    .aspectRatio(if (group.isVideo) 16f / 9f else 1f)
                    .clip(RoundedCornerShape(16.dp))
                    .background(groupColor(group.color))
            )
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    item.title,
                    fontSize = 22.sp,
                    fontWeight = FontWeight.ExtraBold,
                    color = Ink,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(stringResource(R.string.item_minutes, minutes), color = Muted, fontWeight = FontWeight.Bold)
                    // A new listen costs gems until the file is paid today (SPEC §16.4).
                    group.status.gemCost?.takeIf { !item.paidToday }?.let { GemChip(it) }
                    if (item.playedToday) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Filled.Check, contentDescription = null, tint = Color(0xFF135C3A))
                            Text(
                                stringResource(R.string.item_played_today),
                                color = Color(0xFF135C3A),
                                fontWeight = FontWeight.Bold
                            )
                        }
                    }
                }
                if (progress in 0.01f..0.97f) {
                    LinearProgressIndicator(progress = {
                        progress
                    }, color = groupColor(group.color), modifier = Modifier.fillMaxWidth())
                }
            }
            Box(
                Modifier.size(
                    64.dp
                ).clip(CircleShape).background(if (item.allowed) groupColor(group.color) else Color(0xFFE3E8F2)),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    if (item.allowed) Icons.Filled.PlayArrow else Icons.Filled.Lock,
                    contentDescription = stringResource(if (item.allowed) R.string.player_play else R.string.locked),
                    tint = if (item.allowed) Color.White else Ink,
                    modifier = Modifier.size(34.dp)
                )
            }
        }
    }
}

// ---- player (SPEC §8.3) -------------------------------------------------------------

@OptIn(UnstableApi::class)
@Composable
fun PlayerScreen(
    base: String,
    now: NowPlaying,
    player: Player?,
    onBack: () -> Unit,
    onToggle: () -> Unit,
    onSeek: (Long) -> Unit,
    onSeekTo: (Long) -> Unit,
    // The gem balance while gems are used, so a spend at the count shows here too (SPEC §16.4).
    gems: Int? = null
) {
    val context = LocalContext.current
    var position by remember { mutableFloatStateOf(0f) }
    var durationMs by remember { mutableLongStateOf((now.item.durationS * 1000).toLong()) }
    // While the child drags the thumb, the 500 ms poll must not move it back.
    var dragging by remember { mutableStateOf(false) }
    var playing by remember { mutableStateOf(false) }
    var buffering by remember { mutableStateOf(false) }
    var posText by remember { mutableStateOf("0:00") }
    LaunchedEffect(player) {
        while (true) {
            player?.let {
                val d = it.duration.takeIf { v -> v > 0 } ?: (now.item.durationS * 1000).toLong()
                durationMs = d
                if (!dragging) {
                    position = (it.currentPosition.toFloat() / d).coerceIn(0f, 1f)
                    posText = formatDuration(it.currentPosition / 1000.0)
                }
                playing = it.isPlaying
                buffering = it.playbackState == Player.STATE_BUFFERING
            }
            delay(500)
        }
    }
    val accent = groupColor(now.group.color)
    // Full-screen video (SPEC §8.3): one big button on the video, one to leave; back leaves too.
    var fullscreen by rememberSaveable { mutableStateOf(false) }
    if (fullscreen && now.group.isVideo && player != null) {
        BackHandler { fullscreen = false }
        Box(Modifier.fillMaxSize().background(Color.Black)) {
            VideoSurface(player, Modifier.fillMaxSize())
            if (buffering) BufferingSpinner(Modifier.align(Alignment.Center))
            FullscreenButton(
                exit = true,
                onClick = { fullscreen = false },
                modifier = Modifier.align(Alignment.TopStart).padding(20.dp)
            )
            Row(
                Modifier.align(Alignment.TopEnd).padding(20.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                gems?.let { GemCounter(it, Modifier.padding(end = 20.dp)) }
                remainingText(context, now.remainingSeconds, now.grace)?.let { RemainingPill(it) }
            }
        }
        return
    }
    Column(Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            BackButton(onBack)
            Spacer(Modifier.weight(1f))
            gems?.let { GemCounter(it, Modifier.padding(end = 20.dp)) }
            remainingText(context, now.remainingSeconds, now.grace)?.let { RemainingPill(it) }
        }
        Row(
            Modifier.fillMaxSize(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(48.dp)
        ) {
            Box(
                Modifier
                    .weight(if (now.group.isVideo) 1.6f else 1f)
                    .aspectRatio(if (now.group.isVideo) 16f / 9f else 1f)
                    .clip(RoundedCornerShape(32.dp))
                    .background(if (now.group.isVideo) Color.Black else accent),
                contentAlignment = Alignment.Center
            ) {
                if (now.group.isVideo && player != null) {
                    VideoSurface(player, Modifier.fillMaxSize())
                    FullscreenButton(
                        exit = false,
                        onClick = { fullscreen = true },
                        modifier = Modifier.align(Alignment.TopEnd).padding(16.dp)
                    )
                } else {
                    AsyncImage(
                        model = thumbUrl(base, now.item.thumb ?: now.group.cover, "lg"),
                        contentDescription = null,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize()
                    )
                }
                if (buffering) BufferingSpinner()
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(20.dp)) {
                Text(now.group.name, fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, color = Muted)
                Text(now.item.title, fontSize = 34.sp, fontWeight = FontWeight.Black, color = Ink, lineHeight = 40.sp)
                // Tap or drag to jump (touch target ≥ 64 dp, SPEC §8). Seeks only on release,
                // so dragging doesn't fire a stream request per pixel.
                Slider(
                    value = position,
                    onValueChange = {
                        dragging = true
                        position = it
                        posText = formatDuration(it * durationMs / 1000.0)
                    },
                    onValueChangeFinished = {
                        dragging = false
                        onSeekTo((position * durationMs).toLong())
                    },
                    colors = SliderDefaults.colors(
                        thumbColor = accent,
                        activeTrackColor = accent,
                        inactiveTrackColor = accent.copy(alpha = 0.25f)
                    ),
                    modifier = Modifier.fillMaxWidth().height(64.dp)
                )
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(posText, color = Muted, fontWeight = FontWeight.Bold)
                    Text(formatDuration(now.item.durationS), color = Muted, fontWeight = FontWeight.Bold)
                }
                Row(
                    horizontalArrangement = Arrangement.spacedBy(28.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    SkipButton("-15", stringResource(R.string.player_back15)) { onSeek(-15_000) }
                    FilledIconButton(
                        onClick = onToggle,
                        modifier = Modifier.size(112.dp),
                        colors = IconButtonDefaults.filledIconButtonColors(containerColor = accent)
                    ) {
                        if (playing) {
                            PauseGlyph(Color.White)
                        } else {
                            Icon(
                                Icons.Filled.PlayArrow,
                                stringResource(R.string.player_play),
                                tint = Color.White,
                                modifier = Modifier.size(64.dp)
                            )
                        }
                    }
                    SkipButton("+30", stringResource(R.string.player_fwd30)) { onSeek(30_000) }
                }
            }
        }
    }
}

@OptIn(UnstableApi::class)
@Composable
private fun VideoSurface(player: Player, modifier: Modifier = Modifier) {
    AndroidView(
        factory = { ctx -> PlayerView(ctx).apply { useController = true } },
        update = { it.player = player },
        // Entering or leaving full screen swaps the view; the old one lets go of the player.
        onRelease = { it.player = null },
        modifier = modifier
    )
}

@Composable
private fun BufferingSpinner(modifier: Modifier = Modifier) {
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        CircularProgressIndicator(
            color = Color.White,
            strokeWidth = 6.dp,
            modifier = Modifier.size(64.dp)
        )
        Text(stringResource(R.string.player_loading), color = Color.White, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun RemainingPill(text: String) {
    Text(
        text,
        fontSize = 20.sp,
        fontWeight = FontWeight.ExtraBold,
        color = Ink,
        modifier = Modifier.clip(
            RoundedCornerShape(22.dp)
        ).background(Color.White).padding(horizontal = 20.dp, vertical = 10.dp)
    )
}

/** Round 72 dp button with four corner brackets: outward to enter full screen, inward to leave. */
@Composable
private fun FullscreenButton(exit: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val description = stringResource(if (exit) R.string.player_fullscreen_exit else R.string.player_fullscreen)
    Surface(
        onClick = onClick,
        shape = CircleShape,
        color = Color.Black.copy(alpha = 0.55f),
        modifier = modifier.size(72.dp).semantics { contentDescription = description }
    ) {
        Box(contentAlignment = Alignment.Center) {
            Canvas(Modifier.size(36.dp)) {
                val w = size.width
                val arm = w * 0.36f
                val stroke = w * 0.13f
                // Each corner: the corner point and the directions of its two arms.
                val corners = listOf(
                    Offset(0f, 0f) to Offset(1f, 1f),
                    Offset(w, 0f) to Offset(-1f, 1f),
                    Offset(0f, w) to Offset(1f, -1f),
                    Offset(w, w) to Offset(-1f, -1f)
                )
                for ((corner, dir) in corners) {
                    // Leaving: the bracket's corner sits inward, its arms point outward.
                    val c = if (exit) Offset(corner.x + dir.x * arm, corner.y + dir.y * arm) else corner
                    val sx = if (exit) -dir.x else dir.x
                    val sy = if (exit) -dir.y else dir.y
                    drawLine(Color.White, c, Offset(c.x + sx * arm, c.y), stroke, StrokeCap.Round)
                    drawLine(Color.White, c, Offset(c.x, c.y + sy * arm), stroke, StrokeCap.Round)
                }
            }
        }
    }
}

@Composable
private fun PauseGlyph(color: Color) {
    val description = stringResource(R.string.player_pause)
    Row(
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        modifier = Modifier.semantics { contentDescription = description }
    ) {
        Box(Modifier.size(width = 14.dp, height = 44.dp).clip(RoundedCornerShape(4.dp)).background(color))
        Box(Modifier.size(width = 14.dp, height = 44.dp).clip(RoundedCornerShape(4.dp)).background(color))
    }
}

@Composable
private fun SkipButton(label: String, description: String, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        shape = CircleShape,
        shadowElevation = 3.dp,
        modifier = Modifier.size(80.dp).semantics { contentDescription = description }
    ) {
        Box(contentAlignment = Alignment.Center) {
            Text(label, fontSize = 22.sp, fontWeight = FontWeight.Black, color = Ink)
        }
    }
}

@Composable
fun BackButton(onBack: () -> Unit) {
    IconButton(
        onClick = onBack,
        modifier = Modifier.size(72.dp).clip(CircleShape).background(Color.White)
    ) {
        Icon(
            Icons.AutoMirrored.Filled.ArrowBack,
            stringResource(R.string.back),
            tint = Ink,
            modifier = Modifier.size(36.dp)
        )
    }
}

@Composable
fun MiniPlayer(base: String, now: NowPlaying, playing: Boolean, onOpen: () -> Unit, onToggle: () -> Unit) {
    val context = LocalContext.current
    Surface(
        color = Ink,
        shape = RoundedCornerShape(26.dp),
        modifier = Modifier.fillMaxWidth().clickable(onClick = onOpen)
    ) {
        Row(
            Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            AsyncImage(
                model = thumbUrl(base, now.item.thumb ?: now.group.cover, "sm"),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.size(56.dp).clip(RoundedCornerShape(14.dp)).background(groupColor(now.group.color))
            )
            Column(Modifier.weight(1f)) {
                Text(
                    now.item.title,
                    color = Color.White,
                    fontWeight = FontWeight.ExtraBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Text(
                    remainingText(context, now.remainingSeconds, now.grace) ?: now.group.name,
                    color = Color(0xFFC2CAE0),
                    maxLines = 1
                )
            }
            FilledIconButton(
                onClick = onToggle,
                modifier = Modifier.size(60.dp),
                colors = IconButtonDefaults.filledIconButtonColors(containerColor = Color.White)
            ) {
                if (playing) {
                    PauseGlyph(
                        Ink
                    )
                } else {
                    Icon(Icons.Filled.PlayArrow, stringResource(R.string.player_play), tint = Ink)
                }
            }
        }
    }
}

// ---- reason screen (SPEC §8.4) ------------------------------------------------------

@Composable
fun ReasonScreen(block: Block, onOk: () -> Unit) {
    val context = LocalContext.current
    val night = block.reason == "lockout"
    Column(
        Modifier.fillMaxSize().background(if (night) Color(0xFF22285C) else Color(0xFF3A47B5)).padding(48.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        if (block.reason == "gems") {
            // Needed and owned gems as pictures, for a child who can't read yet (SPEC §16.4).
            val (need, have) = gemsNeedHave(block.params)
            GemPictures(need, have)
        } else {
            Box(
                Modifier.size(160.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.16f)),
                contentAlignment = Alignment.Center
            ) {
                Icon(Icons.Filled.Lock, contentDescription = null, tint = Color.White, modifier = Modifier.size(80.dp))
            }
        }
        Spacer(Modifier.height(32.dp))
        Text(
            reasonText(context, block.reason, block.params),
            fontSize = 44.sp,
            fontWeight = FontWeight.Black,
            color = Color.White,
            lineHeight = 52.sp
        )
        Spacer(Modifier.height(36.dp))
        Button(onClick = onOk, modifier = Modifier.width(240.dp).height(88.dp), shape = RoundedCornerShape(44.dp)) {
            Text(stringResource(R.string.ok), fontSize = 30.sp, fontWeight = FontWeight.Black)
        }
    }
}

/**
 * Material's default text style has a fixed 24 sp line height, made for body text. The app's
 * headings are far larger, so a heading that wraps (a German title in portrait) drew its lines
 * on top of each other. Without a fixed value each text uses its own font's line height; texts
 * that set `lineHeight` keep it.
 */
@Composable
fun KidsTheme(content: @Composable () -> Unit) {
    MaterialTheme {
        CompositionLocalProvider(
            LocalTextStyle provides LocalTextStyle.current.copy(lineHeight = TextUnit.Unspecified),
            content = content
        )
    }
}
