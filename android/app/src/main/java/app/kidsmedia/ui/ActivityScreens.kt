package app.kidsmedia.ui

import androidx.annotation.StringRes
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.kidsmedia.R
import app.kidsmedia.activities.ActivitiesView
import app.kidsmedia.activities.ActivityBadge
import app.kidsmedia.activities.ActivityLogic
import app.kidsmedia.activities.CalcLogic
import app.kidsmedia.activities.CalcProgress
import app.kidsmedia.activities.CalcStep
import app.kidsmedia.activities.ChapterDay
import app.kidsmedia.activities.Clip
import app.kidsmedia.activities.OpenState
import app.kidsmedia.activities.ReadLogic
import app.kidsmedia.activities.ReadProgress
import app.kidsmedia.activities.ReadStep
import app.kidsmedia.activities.SpeechLogic
import app.kidsmedia.data.CalcProblem
import app.kidsmedia.data.ChapterTexts
import app.kidsmedia.data.ReadQuestion
import app.kidsmedia.data.StoryChapter
import app.kidsmedia.data.StoryTask
import app.kidsmedia.data.StoryWorld
import app.kidsmedia.data.TaskTexts
import app.kidsmedia.data.ThumbRef
import app.kidsmedia.data.WorldTexts
import coil3.compose.AsyncImage
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

// Colours of the prototype "Kids Media — Activities (proposal)".
private val Ember = Color(0xFF8A3410)
internal val DoneGreen = Color(0xFF17784A)
private val LockedGround = Color(0xFFE3E8F2)
private val ChipGround = Color(0xFFEEF2F9)

fun kindColor(kind: String): Color = when (kind) {
    "draw" -> Color(0xFFB84A12)
    "calc" -> Color(0xFF0F7A70)
    // Purple for reading (SPEC §14.2): distinct from the others, white text at about 7:1.
    "read" -> Color(0xFF7B3FB0)
    else -> Color(0xFF4552C7)
}

@StringRes
fun kindLabel(kind: String): Int = when (kind) {
    "draw" -> R.string.activity_kind_draw
    "calc" -> R.string.activity_kind_calc
    "read" -> R.string.activity_kind_read
    else -> R.string.activity_kind_write
}

@Composable
internal fun language(): String = LocalConfiguration.current.locales[0].language

/** The story is told in the language the parent chose for it, not the app's (SPEC §14.7). */
@Composable
private fun storyLanguage(world: StoryWorld): String = ActivityLogic.storyLanguage(world, language())

@Composable
private fun worldTexts(world: StoryWorld): WorldTexts =
    ActivityLogic.pick(world.texts, storyLanguage(world))?.second ?: WorldTexts()

@Composable
private fun chapterTexts(world: StoryWorld, chapter: StoryChapter): Pair<String, ChapterTexts> =
    ActivityLogic.pick(chapter.texts, storyLanguage(world)) ?: ("en" to ChapterTexts())

@Composable
private fun taskTexts(world: StoryWorld, task: StoryTask): Pair<String, TaskTexts> =
    ActivityLogic.pick(task.texts, storyLanguage(world)) ?: ("en" to TaskTexts())

// ---- icons, drawn like the prototype's stroke icons (24 × 24 grid) -----------------

private fun strokes(kind: String): List<List<Offset>> = when (kind) {
    "draw" -> listOf(
        listOf(
            Offset(4f, 20f),
            Offset(8f, 19f),
            Offset(19f, 8f),
            Offset(16f, 5f),
            Offset(5f, 16f),
            Offset(4f, 20f)
        ),
        listOf(Offset(14f, 6f), Offset(17f, 9f))
    )
    "calc" -> listOf(
        listOf(Offset(7f, 4f), Offset(7f, 12f)),
        listOf(Offset(3f, 8f), Offset(11f, 8f)),
        listOf(Offset(13f, 16f), Offset(21f, 16f)),
        listOf(Offset(13f, 20f), Offset(21f, 20f))
    )
    // An open book: two pages and the spine.
    "read" -> listOf(
        listOf(
            Offset(12f, 7f),
            Offset(3f, 5f),
            Offset(3f, 18f),
            Offset(12f, 20f),
            Offset(21f, 18f),
            Offset(21f, 5f),
            Offset(12f, 7f)
        ),
        listOf(Offset(12f, 7f), Offset(12f, 20f))
    )
    else -> listOf(
        listOf(Offset(3f, 20f), Offset(9f, 4f), Offset(15f, 20f)),
        listOf(Offset(5.5f, 14f), Offset(12.5f, 14f)),
        listOf(Offset(17f, 20f), Offset(21f, 20f))
    )
}

@Composable
private fun StrokeIcon(lines: List<List<Offset>>, tint: Color, modifier: Modifier, width: Float = 2.4f) {
    Canvas(modifier) {
        val k = size.minDimension / 24f
        val stroke = Stroke(width = width * k, cap = StrokeCap.Round, join = StrokeJoin.Round)
        for (line in lines) {
            val path = Path()
            line.forEachIndexed { i, p ->
                if (i == 0) path.moveTo(p.x * k, p.y * k) else path.lineTo(p.x * k, p.y * k)
            }
            drawPath(path, tint, style = stroke)
        }
    }
}

@Composable
fun KindIcon(kind: String, tint: Color, modifier: Modifier = Modifier) = StrokeIcon(strokes(kind), tint, modifier)

@Composable
private fun SpeakerIcon(tint: Color, modifier: Modifier) = StrokeIcon(
    listOf(
        listOf(
            Offset(4f, 9f),
            Offset(4f, 15f),
            Offset(8f, 15f),
            Offset(13f, 19f),
            Offset(13f, 5f),
            Offset(8f, 9f),
            Offset(4f, 9f)
        ),
        listOf(Offset(16f, 9f), Offset(17.5f, 12f), Offset(16f, 15f)),
        listOf(Offset(19f, 6f), Offset(21.5f, 12f), Offset(19f, 18f))
    ),
    tint,
    modifier,
    width = 2.2f
)

/** A camera: the body with its bump, and a round lens. */
@Composable
internal fun CameraIcon(tint: Color, modifier: Modifier) = StrokeIcon(
    listOf(
        listOf(
            Offset(3f, 8f),
            Offset(7f, 8f),
            Offset(9f, 5f),
            Offset(15f, 5f),
            Offset(17f, 8f),
            Offset(21f, 8f),
            Offset(21f, 19f),
            Offset(3f, 19f),
            Offset(3f, 8f)
        ),
        (0..12).map { i ->
            val a = i * PI / 6
            Offset(12f + 3.6f * cos(a).toFloat(), 13.5f + 3.6f * sin(a).toFloat())
        }
    ),
    tint,
    modifier
)

/** The number pad's delete key: a key cap pointing left with a cross. */
@Composable
private fun BackspaceIcon(tint: Color, modifier: Modifier) = StrokeIcon(
    listOf(
        listOf(Offset(8f, 5f), Offset(21f, 5f), Offset(21f, 19f), Offset(8f, 19f), Offset(2f, 12f), Offset(8f, 5f)),
        listOf(Offset(11f, 9f), Offset(17f, 15f)),
        listOf(Offset(17f, 9f), Offset(11f, 15f))
    ),
    tint,
    modifier
)

/** Three bouncing bars while the tablet reads aloud. */
@Composable
private fun SpeakingBars(color: Color, height: Int) {
    val transition = rememberInfiniteTransition(label = "speaking")
    Row(horizontalArrangement = Arrangement.spacedBy(5.dp), verticalAlignment = Alignment.Bottom) {
        listOf(0, 200, 400).forEach { offset ->
            val scale by transition.animateFloat(
                initialValue = 0.35f,
                targetValue = 1f,
                animationSpec = infiniteRepeatable(tween(400, delayMillis = offset), RepeatMode.Reverse),
                label = "bar"
            )
            Box(
                Modifier
                    .width(8.dp)
                    .height((height * scale).dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(color)
            )
        }
    }
}

// ---- home tile (SPEC §13.7) ---------------------------------------------------------

@Composable
fun activityBadgeText(badge: ActivityBadge, unlockTime: String, today: LocalDate): String {
    val locale = LocalConfiguration.current.locales[0]
    return when (badge) {
        is ActivityBadge.Ready -> stringResource(R.string.activity_badge_ready, badge.position)
        is ActivityBadge.SeveralReady -> stringResource(R.string.activity_badge_several, badge.count)
        ActivityBadge.CatchUp -> stringResource(R.string.activity_badge_catch_up)
        ActivityBadge.DoneToday -> stringResource(R.string.activity_badge_done_today)
        ActivityBadge.AllDone -> stringResource(R.string.activity_badge_all_done)
        ActivityBadge.Waiting -> stringResource(R.string.activity_badge_waiting)
        is ActivityBadge.NextOn ->
            if (badge.date == today) {
                stringResource(R.string.activity_badge_next_today, unlockTime)
            } else {
                val day = badge.date.dayOfWeek.getDisplayName(TextStyle.FULL, locale)
                stringResource(R.string.activity_badge_next_on, day)
            }
    }
}

/** The world tile's badge while Ideas are closed (SPEC §14.4a): "Ideas open at 15:00" / "… on Monday". */
@Composable
fun ideasBadgeText(closed: OpenState.Closed, today: LocalDate): String {
    val locale = LocalConfiguration.current.locales[0]
    val at = closed.opensAt ?: return stringResource(R.string.ideas_closed)
    val time = at.format(DateTimeFormatter.ofPattern("HH:mm"))
    return if (at.toLocalDate() == today) {
        stringResource(R.string.ideas_open_at, time)
    } else {
        stringResource(R.string.ideas_open_on, at.dayOfWeek.getDisplayName(TextStyle.FULL, locale), time)
    }
}

/**
 * A world picture: [picture] cropped to fill, or without one the world colour with the kind
 * icons (SPEC §13.5). Square covers and chapter pictures fill square slots; other aspects crop.
 */
@Composable
private fun Cover(
    base: String,
    world: StoryWorld,
    picture: ThumbRef?,
    thumbSize: String,
    modifier: Modifier,
    iconSize: Int
) {
    Box(modifier.background(groupColor(world.color)), contentAlignment = Alignment.Center) {
        if (picture != null) {
            AsyncImage(
                model = thumbUrl(base, picture, thumbSize),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize()
            )
        } else {
            // [iconSize] is sized for three kinds; with four the circles shrink to keep the row's width.
            val each = iconSize * 3 / ActivityLogic.KINDS.size
            Row(horizontalArrangement = Arrangement.spacedBy((each / 4).dp)) {
                ActivityLogic.KINDS.forEach { kind ->
                    Box(
                        Modifier.size(each.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.22f)),
                        contentAlignment = Alignment.Center
                    ) { KindIcon(kind, Color.White, Modifier.size((each * 0.55f).dp)) }
                }
            }
        }
    }
}

/**
 * The story world as the first Home tile, styled like the group tiles; [stars] = the balance (§14.4).
 * Outside the opening times it stays, dimmed with a lock like a locked group tile (§14.4a);
 * tapping it then shows the reason.
 */
@Composable
fun WorldTile(base: String, view: ActivitiesView, onTap: () -> Unit, stars: Int? = null) {
    val world = view.world ?: return
    val name = worldTexts(world).name
    val closed = view.open as? OpenState.Closed
    val badge = when (closed) {
        null -> activityBadgeText(view.badge, view.data.unlockTime, view.today)
        else -> ideasBadgeText(closed, view.today)
    }
    val calm = closed == null && (view.badge == ActivityBadge.DoneToday || view.badge == ActivityBadge.AllDone)
    val label = stringResource(R.string.activity_this_week)
    Surface(
        shape = RoundedCornerShape(28.dp),
        shadowElevation = 4.dp,
        modifier = Modifier
            .alpha(if (closed != null) 0.55f else 1f)
            .clickable(onClick = onTap)
            .semantics { contentDescription = "$label: $name, $badge" }
    ) {
        Column {
            Box {
                Cover(base, world, world.cover, "md", Modifier.fillMaxWidth().aspectRatio(1f), iconSize = 56)
                stars?.let { StarChip(it, Modifier.align(Alignment.TopStart).padding(14.dp)) }
                if (closed != null) {
                    Box(
                        Modifier.align(Alignment.TopEnd).padding(14.dp).size(52.dp).clip(CircleShape)
                            .background(Color.White),
                        contentAlignment = Alignment.Center
                    ) { Icon(Icons.Filled.Lock, contentDescription = null, tint = Ink) }
                }
            }
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(label, fontSize = 15.sp, fontWeight = FontWeight.ExtraBold, color = Ember)
                Text(name, fontSize = 24.sp, fontWeight = FontWeight.Black, color = Ink, maxLines = 2)
                Text(
                    badge,
                    fontSize = 17.sp,
                    fontWeight = FontWeight.Bold,
                    color = if (calm) Color(0xFF135C3A) else Color(0xFF7A2E0C),
                    modifier = Modifier
                        .clip(RoundedCornerShape(16.dp))
                        .background(if (calm) Color(0xFFDDF3E8) else Color(0xFFFFE9D6))
                        .padding(horizontal = 12.dp, vertical = 6.dp)
                )
            }
        }
    }
}

// ---- shared bits --------------------------------------------------------------------

@Composable
private fun ListenButton(speaking: Boolean, onClick: () -> Unit) {
    val label = stringResource(if (speaking) R.string.activity_stop else R.string.activity_listen)
    Surface(
        onClick = onClick,
        shape = CircleShape,
        color = Ink,
        modifier = Modifier.size(112.dp).semantics { contentDescription = label }
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            if (speaking) SpeakingBars(Color.White, 40) else SpeakerIcon(Color.White, Modifier.size(44.dp))
            Spacer(Modifier.height(6.dp))
            Text(label, color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Black)
        }
    }
}

@Composable
private fun InfoChip(text: String, ground: Color = ChipGround, color: Color = Color(0xFF33405F)) {
    Text(
        text,
        fontSize = 20.sp,
        fontWeight = FontWeight.ExtraBold,
        color = color,
        modifier = Modifier
            .clip(RoundedCornerShape(22.dp))
            .background(ground)
            .padding(horizontal = 18.dp, vertical = 10.dp)
    )
}

// ---- story world (SPEC §13.7) -------------------------------------------------------

@Composable
private fun ChapterButton(chapter: StoryChapter, today: LocalDate, selected: Boolean, onClick: () -> Unit) {
    val locale = LocalConfiguration.current.locales[0]
    val done = chapter.doneKinds.isNotEmpty()
    val locked = !chapter.unlocked
    val day = when (val d = ActivityLogic.chapterDay(chapter, today)) {
        is ChapterDay.Weekday -> d.day.getDisplayName(TextStyle.SHORT, locale)
        ChapterDay.NextWeek -> stringResource(R.string.activity_next_week)
        ChapterDay.Unknown -> ""
    }
    val state = when {
        done -> stringResource(R.string.activity_state_done)
        locked -> stringResource(R.string.activity_state_locked)
        else -> day
    }
    val description = stringResource(R.string.activity_chapter_a11y, chapter.position, state)
    val fg = if (done) Color.White else Ink
    Surface(
        onClick = onClick,
        enabled = !locked,
        shape = RoundedCornerShape(20.dp),
        color = when {
            done -> DoneGreen
            locked -> LockedGround
            else -> Color.White
        },
        modifier = Modifier
            .size(width = 76.dp, height = 84.dp)
            .alpha(if (locked) 0.7f else 1f)
            .border(4.dp, if (selected) Ink else Color.Transparent, RoundedCornerShape(20.dp))
            .semantics { contentDescription = description }
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
            Text(day, fontSize = 14.sp, fontWeight = FontWeight.Black, color = fg, maxLines = 1)
            when {
                done -> Icon(Icons.Filled.Check, contentDescription = null, tint = fg, modifier = Modifier.size(28.dp))
                locked -> Icon(Icons.Filled.Lock, contentDescription = null, tint = fg, modifier = Modifier.size(24.dp))
                else -> Text("${chapter.position}", fontSize = 26.sp, fontWeight = FontWeight.Black, color = fg)
            }
        }
    }
}

/** [compact]: four cards in a row (SPEC §14.2), so the header's icon and label get smaller. */
@Composable
private fun KindCard(
    world: StoryWorld,
    task: StoryTask,
    chip: String?,
    modifier: Modifier,
    compact: Boolean = false,
    onClick: () -> Unit
) {
    val color = kindColor(task.kind)
    val kindName = stringResource(kindLabel(task.kind))
    val texts = taskTexts(world, task).second
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(28.dp),
        shadowElevation = 4.dp,
        modifier = modifier.semantics {
            contentDescription = "$kindName: ${texts.title}" + (chip?.let { ", $it" } ?: "")
        }
    ) {
        Column {
            Row(
                Modifier
                    .fillMaxWidth()
                    .height(88.dp)
                    .background(color)
                    .padding(horizontal = if (compact) 14.dp else 20.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(if (compact) 10.dp else 14.dp)
            ) {
                Box(
                    Modifier
                        .size(if (compact) 44.dp else 56.dp)
                        .clip(CircleShape)
                        .background(Color.White.copy(alpha = 0.2f)),
                    contentAlignment = Alignment.Center
                ) { KindIcon(task.kind, Color.White, Modifier.size(if (compact) 24.dp else 30.dp)) }
                Text(
                    kindName,
                    fontSize = if (compact) 22.sp else 28.sp,
                    fontWeight = FontWeight.Black,
                    color = Color.White,
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                chip?.let {
                    Text(
                        it,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Black,
                        color = color,
                        modifier = Modifier
                            .clip(RoundedCornerShape(16.dp))
                            .background(Color.White)
                            .padding(horizontal = 12.dp, vertical = 5.dp)
                    )
                }
            }
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    texts.title,
                    fontSize = 24.sp,
                    fontWeight = FontWeight.Black,
                    color = Ink,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    lineHeight = 28.sp
                )
                Text(
                    stringResource(R.string.activity_minutes, task.minutes),
                    fontSize = 17.sp,
                    fontWeight = FontWeight.Bold,
                    color = Muted
                )
            }
        }
    }
}

fun introKey(world: StoryWorld, position: Int) = "intro-${world.id}-$position"

fun taskKey(world: StoryWorld, position: Int, kind: String) = "task-${world.id}-$position-$kind"

@Composable
fun StoryScreen(
    base: String,
    view: ActivitiesView,
    position: Int,
    speaking: String?,
    onBack: () -> Unit,
    onSelect: (Int) -> Unit,
    onListen: (key: String, clips: List<Clip>, language: String) -> Unit,
    onTask: (String) -> Unit
) {
    val world = view.world ?: return
    val chapter = world.chapters.firstOrNull { it.position == position } ?: world.chapters.firstOrNull() ?: return
    val locale = LocalConfiguration.current.locales[0]
    val wt = worldTexts(world)
    val (lang, ct) = chapterTexts(world, chapter)
    val fullDay = ActivityLogic.dateOf(chapter)?.dayOfWeek?.getDisplayName(TextStyle.FULL, locale)
    val day = fullDay?.uppercase(locale)
    val choose = when {
        !chapter.unlocked ->
            fullDay?.let { stringResource(R.string.activity_locked_on, it) }
                ?: stringResource(R.string.activity_locked_later)
        chapter.doneKinds.isNotEmpty() -> stringResource(R.string.activity_choose_extra)
        else -> stringResource(R.string.activity_choose)
    }
    val key = introKey(world, chapter.position)
    val tasks = ActivityLogic.KINDS.mapNotNull { k -> chapter.tasks.firstOrNull { it.kind == k } }

    @Composable
    fun Card(task: StoryTask, modifier: Modifier, compact: Boolean) {
        val chip = when {
            task.kind in chapter.doneKinds -> stringResource(R.string.activity_chip_done)
            chapter.doneKinds.isNotEmpty() -> stringResource(R.string.activity_chip_extra)
            else -> null
        }
        KindCard(world, task, chip, modifier.alpha(if (chapter.unlocked) 1f else 0.5f), compact) { onTask(task.kind) }
    }

    @Composable
    fun Chapters(modifier: Modifier = Modifier) {
        Row(modifier, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            world.chapters.forEach { c ->
                ChapterButton(c, view.today, c.position == chapter.position) { onSelect(c.position) }
            }
        }
    }

    @Composable
    fun Picture(size: Int) = Cover(
        base,
        world,
        ActivityLogic.picture(world, chapter),
        "md",
        Modifier.size(size.dp).clip(RoundedCornerShape(24.dp)),
        iconSize = 36
    )

    @Composable
    fun Heading() {
        Text(
            stringResource(R.string.activity_chapter_label, chapter.position, day ?: ""),
            fontSize = 16.sp,
            fontWeight = FontWeight.Black,
            color = Ember
        )
        Text(ct.title, fontSize = 30.sp, fontWeight = FontWeight.Black, color = Ink, lineHeight = 36.sp)
    }

    @Composable
    fun Intro() = Text(
        ct.intro,
        fontSize = 22.sp,
        fontWeight = FontWeight.Bold,
        color = Color(0xFF33405F),
        lineHeight = 30.sp
    )

    BoxWithConstraints(Modifier.fillMaxSize().background(Ground)) {
        // Portrait (a tablet held upright): the chapters get their own row, the story text the
        // card's full width, and the four activities a 2 x 2 grid; the screen scrolls if needed.
        val narrow = maxWidth < NARROW
        Column(
            Modifier
                .fillMaxSize()
                .then(if (narrow) Modifier.verticalScroll(rememberScrollState()) else Modifier)
                .padding(horizontal = if (narrow) 24.dp else 40.dp, vertical = 28.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                BackButton(onBack)
                Column(Modifier.weight(1f)) {
                    Text(
                        wt.name,
                        fontSize = 36.sp,
                        fontWeight = FontWeight.Black,
                        color = Ink,
                        maxLines = if (narrow) 2 else 1,
                        lineHeight = 42.sp,
                        overflow = TextOverflow.Ellipsis
                    )
                    Text(wt.guide, fontSize = 18.sp, fontWeight = FontWeight.Bold, color = Muted, maxLines = 1)
                }
                if (!narrow) Chapters()
            }
            if (narrow) Chapters(Modifier.horizontalScroll(rememberScrollState()))
            Surface(shape = RoundedCornerShape(32.dp), shadowElevation = 3.dp, modifier = Modifier.fillMaxWidth()) {
                if (narrow) {
                    Column(Modifier.padding(22.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(20.dp)
                        ) {
                            Picture(130)
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) { Heading() }
                            ListenButton(speaking == key) { onListen(key, SpeechLogic.chapterClips(ct), lang) }
                        }
                        Intro()
                    }
                } else {
                    Row(
                        Modifier.padding(22.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(24.dp)
                    ) {
                        Picture(170)
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Heading()
                            Intro()
                        }
                        ListenButton(speaking == key) { onListen(key, SpeechLogic.chapterClips(ct), lang) }
                    }
                }
            }
            Text(choose, fontSize = 28.sp, fontWeight = FontWeight.Black, color = Ink, lineHeight = 34.sp)
            if (narrow) {
                tasks.chunked(2).forEach { pair ->
                    Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                        pair.forEach { Card(it, Modifier.weight(1f).height(200.dp), compact = false) }
                        if (pair.size == 1) Spacer(Modifier.weight(1f))
                    }
                }
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                    tasks.forEach { Card(it, Modifier.weight(1f).height(220.dp), compact = tasks.size >= 4) }
                }
            }
        }
    }
}

/** Below this width (a tablet held upright) the story screen stacks its parts. */
private val NARROW = 840.dp

// ---- task (SPEC §13.7) --------------------------------------------------------------

/**
 * A draw, calc or write task (SPEC §13.7, §15.1). Each finishes with its check: draw and write
 * with a photo ([onPicture], when the tablet [hasCamera]), calc with its problem set ([onStartCalc]
 * after the introduction). Without a check the "I'm done!" button asks first, then [onDone].
 */
@Composable
fun TaskScreen(
    view: ActivitiesView,
    position: Int,
    kind: String,
    speaking: String?,
    hasCamera: Boolean,
    onBack: () -> Unit,
    onListen: (key: String, clips: List<Clip>, language: String) -> Unit,
    onPicture: () -> Unit,
    onStartCalc: (List<CalcProblem>) -> Unit,
    onDone: () -> Unit
) {
    val world = view.world ?: return
    val chapter = world.chapters.firstOrNull { it.position == position } ?: return
    val task = chapter.tasks.firstOrNull { it.kind == kind } ?: return
    val color = kindColor(kind)
    val (lang, tt) = taskTexts(world, task)
    val ct = chapterTexts(world, chapter).second
    val key = taskKey(world, position, kind)
    var confirming by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
            BackButton(onBack)
            InfoChip(stringResource(kindLabel(kind)), ground = color, color = Color.White)
            Text(
                stringResource(R.string.activity_chapter_short, position, ct.title),
                fontSize = 22.sp,
                fontWeight = FontWeight.ExtraBold,
                color = Muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        Surface(
            shape = RoundedCornerShape(36.dp),
            shadowElevation = 3.dp,
            modifier = Modifier.fillMaxWidth().weight(1f)
        ) {
            Column {
                Box(Modifier.fillMaxWidth().height(12.dp).background(color))
                Column(
                    Modifier.fillMaxHeight().padding(horizontal = 48.dp, vertical = 32.dp),
                    verticalArrangement = Arrangement.spacedBy(22.dp)
                ) {
                    Text(tt.title, fontSize = 44.sp, fontWeight = FontWeight.Black, color = Ink, lineHeight = 50.sp)
                    Text(tt.text, fontSize = 32.sp, fontWeight = FontWeight.Bold, color = Ink, lineHeight = 44.sp)
                    Spacer(Modifier.weight(1f))
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        tt.materials.take(6).forEach { InfoChip(it) }
                        InfoChip(stringResource(R.string.activity_minutes, task.minutes))
                    }
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(20.dp)
                    ) {
                        ListenButton(speaking == key) { onListen(key, SpeechLogic.taskClips(tt), lang) }
                        Spacer(Modifier.weight(1f))
                        when {
                            kind == "calc" && CalcLogic.usable(tt.problems).isNotEmpty() ->
                                DoneButton({ onStartCalc(tt.problems) }, label = R.string.activity_lets_start) {
                                    ButtonIcon(Icons.Filled.PlayArrow)
                                }
                            (kind == "draw" || kind == "write") && hasCamera ->
                                DoneButton(onPicture, label = R.string.activity_take_picture) {
                                    CameraIcon(Color.White, Modifier.size(46.dp))
                                }
                            else -> DoneButton({ confirming = true })
                        }
                    }
                }
            }
        }
    }
    if (confirming) {
        ConfirmDoneDialog(
            onYes = {
                confirming = false
                onDone()
            },
            onNo = { confirming = false }
        )
    }
}

/** "Did you really finish?" in front of a task without a check (SPEC §15.1). */
@Composable
internal fun ConfirmDoneDialog(onYes: () -> Unit, onNo: () -> Unit) {
    AlertDialog(
        onDismissRequest = onNo,
        title = {
            Text(
                stringResource(R.string.done_confirm_title),
                fontSize = 32.sp,
                fontWeight = FontWeight.Black,
                color = Ink,
                lineHeight = 40.sp
            )
        },
        confirmButton = {
            Button(
                onClick = onYes,
                colors = ButtonDefaults.buttonColors(containerColor = DoneGreen),
                modifier = Modifier.heightIn(min = 72.dp)
            ) { Text(stringResource(R.string.done_confirm_yes), fontSize = 26.sp, fontWeight = FontWeight.Black) }
        },
        dismissButton = {
            OutlinedButton(onClick = onNo, modifier = Modifier.heightIn(min = 72.dp)) {
                Text(
                    stringResource(R.string.done_confirm_no),
                    fontSize = 26.sp,
                    fontWeight = FontWeight.Black,
                    color = Ink
                )
            }
        }
    )
}

@Composable
internal fun ButtonIcon(image: ImageVector, tint: Color = Color.White) =
    Icon(image, contentDescription = null, tint = tint, modifier = Modifier.size(42.dp))

/** The big green button of a task; [icon] in front of the label, a check by default. */
@Composable
internal fun DoneButton(
    onClick: () -> Unit,
    @StringRes label: Int = R.string.activity_done_button,
    color: Color = DoneGreen,
    textColor: Color = Color.White,
    icon: @Composable () -> Unit = { ButtonIcon(Icons.Filled.Check) }
) {
    Surface(onClick = onClick, shape = RoundedCornerShape(52.dp), color = color, shadowElevation = 6.dp) {
        Row(
            Modifier.height(104.dp).padding(horizontal = 44.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            icon()
            Text(
                stringResource(label),
                fontSize = 34.sp,
                fontWeight = FontWeight.Black,
                color = textColor
            )
        }
    }
}

// ---- done (SPEC §13.7) --------------------------------------------------------------

@Composable
fun TaskDoneScreen(base: String, view: ActivitiesView, position: Int, onBack: () -> Unit) {
    val world = view.world ?: return
    val chapter = world.chapters.firstOrNull { it.position == position } ?: return
    val ct = chapterTexts(world, chapter).second
    val picture = ActivityLogic.picture(world, chapter)
    Column(
        Modifier.fillMaxSize().background(Ember).padding(56.dp),
        verticalArrangement = Arrangement.spacedBy(26.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        if (picture != null) {
            // The chapter's picture with the star pinned to its corner.
            Box(Modifier.size(260.dp)) {
                Cover(
                    base,
                    world,
                    picture,
                    "md",
                    Modifier
                        .padding(top = 20.dp, end = 20.dp)
                        .fillMaxSize()
                        .clip(RoundedCornerShape(32.dp))
                        .border(6.dp, Color.White, RoundedCornerShape(32.dp)),
                    iconSize = 48
                )
                Box(
                    Modifier.align(Alignment.TopEnd).size(84.dp).clip(CircleShape).background(Color.White),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Filled.Star,
                        contentDescription = null,
                        tint = Color(0xFFFFB800),
                        modifier = Modifier.size(60.dp)
                    )
                }
            }
        } else {
            Box(
                Modifier.size(190.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.16f)),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    Icons.Filled.Star,
                    contentDescription = null,
                    tint = Color(0xFFFFD34D),
                    modifier = Modifier.size(120.dp)
                )
            }
        }
        Text(
            stringResource(R.string.activity_done_title, position),
            fontSize = 54.sp,
            fontWeight = FontWeight.Black,
            color = Color.White
        )
        Text(ct.outro, fontSize = 28.sp, fontWeight = FontWeight.Bold, color = Color.White, lineHeight = 38.sp)
        Surface(onClick = onBack, shape = RoundedCornerShape(46.dp), color = Color.White) {
            Box(Modifier.height(92.dp).padding(horizontal = 40.dp), contentAlignment = Alignment.Center) {
                Text(
                    stringResource(R.string.activity_back_to_story),
                    fontSize = 30.sp,
                    fontWeight = FontWeight.Black,
                    color = Ink
                )
            }
        }
    }
}

// ---- reading (SPEC §14.2) -----------------------------------------------------------

/** Reading text: large type, 1.5 line height, lines of about 40 characters (≈ 20 em wide). */
private val ReadTextSize = 32.sp
private val ReadLineHeight = 48.sp
private const val READ_LINE_EMS = 20

/**
 * A `read` task: the text (Listen reads only the title, never the reading text), then
 * "I've read it" and the questions one at a time. A wrong answer sends the child back to the
 * text with a friendly word; [progress] keeps the attempts (ReadLogic).
 */
@Composable
fun ReadScreen(
    view: ActivitiesView,
    position: Int,
    progress: ReadProgress,
    speaking: String?,
    onBack: () -> Unit,
    onListen: (key: String, clips: List<Clip>, language: String) -> Unit,
    onReadIt: (List<ReadQuestion>) -> Unit,
    onAnswer: (List<ReadQuestion>, Int) -> Unit,
    onAgain: () -> Unit
) {
    val world = view.world ?: return
    val chapter = world.chapters.firstOrNull { it.position == position } ?: return
    val task = chapter.tasks.firstOrNull { it.kind == "read" } ?: return
    val color = kindColor(task.kind)
    val (lang, tt) = taskTexts(world, task)
    val ct = chapterTexts(world, chapter).second
    val questions = ReadLogic.usable(tt.questions)
    val key = taskKey(world, position, task.kind)
    Column(
        Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
            BackButton(onBack)
            InfoChip(stringResource(kindLabel(task.kind)), ground = color, color = Color.White)
            Text(
                stringResource(R.string.activity_chapter_short, position, ct.title),
                fontSize = 22.sp,
                fontWeight = FontWeight.ExtraBold,
                color = Muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        Surface(
            shape = RoundedCornerShape(36.dp),
            shadowElevation = 3.dp,
            modifier = Modifier.fillMaxWidth().weight(1f)
        ) {
            Column {
                Box(Modifier.fillMaxWidth().height(12.dp).background(color))
                when (progress.step) {
                    ReadStep.QUESTION -> {
                        val index = progress.question.coerceIn(0, (questions.size - 1).coerceAtLeast(0))
                        questions.getOrNull(index)?.let { q ->
                            ReadQuestionPage(
                                q,
                                index,
                                questions.size,
                                color,
                                speaking == "$key-q$index",
                                onListen = { onListen("$key-q$index", SpeechLogic.questionClips(q), lang) },
                                onAnswer = { onAnswer(questions, it) }
                            )
                        }
                    }
                    ReadStep.WRONG -> ReadWrongPage(color, onAgain)
                    ReadStep.TEXT, ReadStep.DONE -> {
                        ReadTextPage(
                            tt.title,
                            tt.text,
                            speaking == "$key-title",
                            onListen = { onListen("$key-title", SpeechLogic.readTitleClips(tt), lang) },
                            onReadIt = { onReadIt(questions) }
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ReadTextPage(title: String, text: String, speaking: Boolean, onListen: () -> Unit, onReadIt: () -> Unit) {
    val lineWidth = with(LocalDensity.current) { (ReadTextSize * READ_LINE_EMS).toDp() }
    Column(
        Modifier.fillMaxHeight().padding(horizontal = 56.dp, vertical = 32.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(24.dp)) {
            Text(
                title,
                fontSize = 40.sp,
                fontWeight = FontWeight.Black,
                color = Ink,
                lineHeight = 48.sp,
                modifier = Modifier.weight(1f)
            )
            // Listen reads the title only: the child reads the text (SPEC §14.2).
            ListenButton(speaking, onListen)
        }
        Box(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())) {
            Text(
                text,
                fontSize = ReadTextSize,
                lineHeight = ReadLineHeight,
                fontWeight = FontWeight.SemiBold,
                color = Ink,
                modifier = Modifier.widthIn(max = lineWidth).padding(vertical = 8.dp)
            )
        }
        Row(Modifier.fillMaxWidth()) {
            Spacer(Modifier.weight(1f))
            DoneButton(onReadIt, label = R.string.read_done_reading)
        }
    }
}

@Composable
private fun ReadQuestionPage(
    question: ReadQuestion,
    index: Int,
    count: Int,
    color: Color,
    speaking: Boolean,
    onListen: () -> Unit,
    onAnswer: (Int) -> Unit
) {
    Column(
        Modifier.fillMaxHeight().verticalScroll(rememberScrollState()).padding(horizontal = 56.dp, vertical = 32.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)
    ) {
        if (count > 1) {
            Text(
                stringResource(R.string.read_question_count, index + 1, count),
                fontSize = 20.sp,
                fontWeight = FontWeight.Black,
                color = color
            )
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(24.dp)) {
            Text(
                question.question,
                fontSize = 36.sp,
                fontWeight = FontWeight.Black,
                color = Ink,
                lineHeight = 46.sp,
                modifier = Modifier.weight(1f)
            )
            ListenButton(speaking, onListen)
        }
        question.answers.forEachIndexed { i, answer ->
            Surface(
                onClick = { onAnswer(i) },
                shape = RoundedCornerShape(28.dp),
                color = Color.White,
                shadowElevation = 2.dp,
                modifier = Modifier
                    .widthIn(max = 760.dp)
                    .fillMaxWidth()
                    .heightIn(min = 88.dp)
                    .border(4.dp, color, RoundedCornerShape(28.dp))
            ) {
                Box(Modifier.padding(horizontal = 28.dp, vertical = 18.dp), contentAlignment = Alignment.CenterStart) {
                    Text(answer, fontSize = 28.sp, fontWeight = FontWeight.ExtraBold, color = Ink, lineHeight = 36.sp)
                }
            }
        }
    }
}

@Composable
private fun ReadWrongPage(color: Color, onAgain: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(48.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            Modifier.size(150.dp).clip(CircleShape).background(color.copy(alpha = 0.14f)),
            contentAlignment = Alignment.Center
        ) { KindIcon("read", color, Modifier.size(84.dp)) }
        Text(stringResource(R.string.read_wrong_title), fontSize = 44.sp, fontWeight = FontWeight.Black, color = Ink)
        Text(
            stringResource(R.string.read_wrong),
            fontSize = 30.sp,
            fontWeight = FontWeight.Bold,
            color = Color(0xFF33405F),
            lineHeight = 42.sp
        )
        Surface(onClick = onAgain, shape = RoundedCornerShape(46.dp), color = color, shadowElevation = 4.dp) {
            Box(Modifier.height(92.dp).padding(horizontal = 44.dp), contentAlignment = Alignment.Center) {
                Text(
                    stringResource(R.string.read_again),
                    fontSize = 30.sp,
                    fontWeight = FontWeight.Black,
                    color = Color.White
                )
            }
        }
    }
}

// ---- maths problems (SPEC §15.2) ----------------------------------------------------

private val PadKeySize = 84.dp
private val PadGap = 14.dp
private val HintGround = Color(0xFFFFF1C2)
private val HintInk = Color(0xFF5C4100)
private val TryAgainInk = Color(0xFF9A3412)

/**
 * A `calc` task's problems, one at a time (SPEC §15.2): the story in large type with Listen,
 * "Problem 2 of 4", the answer box and the app's own number pad (no system keyboard). After the
 * second wrong answer on a problem its hint shows, with Listen, until it is solved.
 */
@Composable
fun CalcScreen(
    view: ActivitiesView,
    position: Int,
    progress: CalcProgress,
    speaking: String?,
    onBack: () -> Unit,
    onListen: (key: String, clips: List<Clip>, language: String) -> Unit,
    onDigit: (Int) -> Unit,
    onDelete: () -> Unit,
    onCheck: (List<CalcProblem>) -> Unit
) {
    val world = view.world ?: return
    val chapter = world.chapters.firstOrNull { it.position == position } ?: return
    val task = chapter.tasks.firstOrNull { it.kind == "calc" } ?: return
    val color = kindColor(task.kind)
    val (lang, tt) = taskTexts(world, task)
    val ct = chapterTexts(world, chapter).second
    val problems = CalcLogic.usable(tt.problems)
    val index = progress.problem.coerceIn(0, (problems.size - 1).coerceAtLeast(0))
    val problem = problems.getOrNull(index) ?: return
    val key = taskKey(world, position, task.kind)
    val typing = progress.step == CalcStep.PROBLEM

    @Composable
    fun Story(modifier: Modifier) {
        Column(modifier, verticalArrangement = Arrangement.spacedBy(20.dp)) {
            Text(
                stringResource(R.string.calc_problem_count, index + 1, problems.size),
                fontSize = 20.sp,
                fontWeight = FontWeight.Black,
                color = color
            )
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                Text(
                    problem.text,
                    fontSize = 34.sp,
                    fontWeight = FontWeight.Bold,
                    color = Ink,
                    lineHeight = 46.sp,
                    modifier = Modifier.weight(1f)
                )
                ListenButton(speaking == "$key-p$index") {
                    onListen("$key-p$index", SpeechLogic.problemClips(problem), lang)
                }
            }
            when {
                progress.step == CalcStep.RIGHT || progress.step == CalcStep.DONE ->
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        ButtonIcon(Icons.Filled.Check, tint = DoneGreen)
                        Text(
                            stringResource(R.string.calc_right),
                            fontSize = 36.sp,
                            fontWeight = FontWeight.Black,
                            color = DoneGreen
                        )
                    }
                progress.tryAgain ->
                    Text(
                        stringResource(R.string.calc_wrong),
                        fontSize = 30.sp,
                        fontWeight = FontWeight.Black,
                        color = TryAgainInk,
                        lineHeight = 38.sp
                    )
            }
            if (progress.showHint && problem.hint.isNotBlank()) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(28.dp))
                        .background(HintGround)
                        .padding(22.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(20.dp)
                ) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(
                            stringResource(R.string.calc_hint),
                            fontSize = 20.sp,
                            fontWeight = FontWeight.Black,
                            color = HintInk
                        )
                        Text(
                            problem.hint,
                            fontSize = 26.sp,
                            fontWeight = FontWeight.Bold,
                            color = HintInk,
                            lineHeight = 34.sp
                        )
                    }
                    ListenButton(speaking == "$key-h$index") {
                        onListen("$key-h$index", SpeechLogic.hintClips(problem), lang)
                    }
                }
            }
        }
    }

    @Composable
    fun Pad() {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(20.dp)) {
            AnswerBox(progress, color)
            NumberPad(
                enabled = typing,
                canCheck = typing && progress.entry.isNotEmpty(),
                onDigit = onDigit,
                onDelete = onDelete,
                onCheck = { onCheck(tt.problems) }
            )
        }
    }

    Column(
        Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
            BackButton(onBack)
            InfoChip(stringResource(kindLabel(task.kind)), ground = color, color = Color.White)
            Text(
                stringResource(R.string.activity_chapter_short, position, ct.title),
                fontSize = 22.sp,
                fontWeight = FontWeight.ExtraBold,
                color = Muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        Surface(
            shape = RoundedCornerShape(36.dp),
            shadowElevation = 3.dp,
            modifier = Modifier.fillMaxWidth().weight(1f)
        ) {
            Column {
                Box(Modifier.fillMaxWidth().height(12.dp).background(color))
                BoxWithConstraints(Modifier.fillMaxSize()) {
                    // Portrait: the story above the pad, scrolling if needed; landscape: side by side.
                    if (maxWidth < NARROW) {
                        Column(
                            Modifier
                                .fillMaxSize()
                                .verticalScroll(rememberScrollState())
                                .padding(horizontal = 32.dp, vertical = 28.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(28.dp)
                        ) {
                            Story(Modifier.fillMaxWidth())
                            Pad()
                        }
                    } else {
                        Row(
                            Modifier.fillMaxSize().padding(horizontal = 48.dp, vertical = 28.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(40.dp)
                        ) {
                            Story(Modifier.weight(1f).verticalScroll(rememberScrollState()))
                            Pad()
                        }
                    }
                }
            }
        }
    }
}

/** The answer typed so far; green once right, framed in the task colour while typing. */
@Composable
private fun AnswerBox(progress: CalcProgress, color: Color) {
    val right = progress.step == CalcStep.RIGHT || progress.step == CalcStep.DONE
    val frame = when {
        right -> DoneGreen
        progress.tryAgain -> TryAgainInk
        else -> color
    }
    val description = stringResource(R.string.calc_answer_a11y, progress.entry)
    Box(
        Modifier
            .size(width = PadKeySize * 3 + PadGap * 2, height = 104.dp)
            .clip(RoundedCornerShape(28.dp))
            .background(if (right) Color(0xFFDDF3E8) else Color.White)
            .border(5.dp, frame, RoundedCornerShape(28.dp))
            .semantics { contentDescription = description },
        contentAlignment = Alignment.Center
    ) {
        Text(progress.entry, fontSize = 56.sp, fontWeight = FontWeight.Black, color = if (right) DoneGreen else Ink)
    }
}

/** 1–9, then delete, 0 and ✓; keys of 84 dp. At most three digits (CalcLogic). */
@Composable
private fun NumberPad(
    enabled: Boolean,
    canCheck: Boolean,
    onDigit: (Int) -> Unit,
    onDelete: () -> Unit,
    onCheck: () -> Unit
) {
    @Composable
    fun Digit(digit: Int) = PadKey("$digit", enabled, Color.White, { onDigit(digit) }) {
        Text("$digit", fontSize = 40.sp, fontWeight = FontWeight.Black, color = Ink)
    }

    Column(verticalArrangement = Arrangement.spacedBy(PadGap)) {
        listOf(listOf(1, 2, 3), listOf(4, 5, 6), listOf(7, 8, 9)).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(PadGap)) { row.forEach { Digit(it) } }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(PadGap)) {
            PadKey(stringResource(R.string.calc_delete), enabled, ChipGround, onDelete) {
                BackspaceIcon(Ink, Modifier.size(40.dp))
            }
            Digit(0)
            PadKey(stringResource(R.string.calc_check), canCheck, DoneGreen, onCheck) {
                Icon(Icons.Filled.Check, contentDescription = null, tint = Color.White, modifier = Modifier.size(48.dp))
            }
        }
    }
}

@Composable
private fun PadKey(
    description: String,
    enabled: Boolean,
    ground: Color,
    onClick: () -> Unit,
    content: @Composable () -> Unit
) {
    Surface(
        onClick = onClick,
        enabled = enabled,
        shape = RoundedCornerShape(24.dp),
        color = ground,
        shadowElevation = 2.dp,
        modifier = Modifier
            .size(PadKeySize)
            .alpha(if (enabled) 1f else 0.5f)
            .border(3.dp, LockedGround, RoundedCornerShape(24.dp))
            .semantics { contentDescription = description }
    ) { Box(contentAlignment = Alignment.Center) { content() } }
}
