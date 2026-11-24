package app.kidsmedia.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.kidsmedia.R
import app.kidsmedia.activities.LogDay
import app.kidsmedia.activities.LogLogic
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import kotlin.math.abs

// Rows of the gem and star logs (SPEC §16.6, §14.4b): a picture, what happened, when, and
// the change in big friendly numbers.
private val Gained = Color(0xFF17784A)
private val GainedGround = Color(0xFFDDF3E8)
private val Lost = Color(0xFFA8323C)
private val LostGround = Color(0xFFFBE3E5)

/** "Today, 14:20", "Yesterday, 08:10" or a short date with the time, in the tablet's language. */
@Composable
fun logTimeText(createdAt: String): String {
    val locale = LocalConfiguration.current.locales[0]
    val zone = ZoneId.systemDefault()
    val time = LogLogic.time(createdAt, LocalDate.now(zone), zone) ?: return ""
    val clock = time.at.format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(locale))
    return when (time.day) {
        LogDay.TODAY -> stringResource(R.string.log_today, clock)
        LogDay.YESTERDAY -> stringResource(R.string.log_yesterday, clock)
        LogDay.EARLIER -> time.at.format(DateTimeFormatter.ofLocalizedDateTime(FormatStyle.SHORT).withLocale(locale))
    }
}

/**
 * One log row. [picture] fills a 72 dp square; [unit] draws the star or the gem next to the
 * number. Gains are green with "+", losses red with "−", so the direction shows without
 * reading.
 */
@Composable
fun LogRow(
    title: String,
    subtitle: String?,
    createdAt: String,
    delta: Int,
    unit: @Composable () -> Unit,
    picture: @Composable () -> Unit
) {
    val time = logTimeText(createdAt)
    val sign = if (delta > 0) "+" else if (delta < 0) "−" else ""
    val description = listOfNotNull(title, subtitle, time, "$sign${abs(delta)}").joinToString(", ")
    Surface(
        shape = RoundedCornerShape(24.dp),
        color = Color.White,
        shadowElevation = 2.dp,
        modifier = Modifier.fillMaxWidth().semantics(mergeDescendants = true) { contentDescription = description }
    ) {
        Row(
            Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Box(Modifier.size(72.dp).clip(RoundedCornerShape(16.dp)), contentAlignment = Alignment.Center) {
                picture()
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, fontSize = 22.sp, fontWeight = FontWeight.Bold, color = Ink, maxLines = 2)
                subtitle?.takeIf { it.isNotBlank() }?.let {
                    Text(it, fontSize = 18.sp, color = Muted, maxLines = 2)
                }
                if (time.isNotEmpty()) Text(time, fontSize = 16.sp, color = Muted)
            }
            DeltaChip(delta, unit)
        }
    }
}

@Composable
private fun DeltaChip(delta: Int, unit: @Composable () -> Unit) {
    val gained = delta >= 0
    Row(
        Modifier
            .clip(RoundedCornerShape(22.dp))
            .background(if (gained) GainedGround else LostGround)
            .padding(horizontal = 14.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        Text(
            (if (delta > 0) "+" else if (delta < 0) "−" else "") + abs(delta),
            fontSize = 28.sp,
            fontWeight = FontWeight.Black,
            color = if (gained) Gained else Lost
        )
        unit()
    }
}

/** The log's heading, and a line when there is nothing to show yet or no connection. */
@Composable
fun LogHeader(empty: Boolean, unavailable: Boolean) {
    Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(stringResource(R.string.log_title), fontSize = 28.sp, fontWeight = FontWeight.Black, color = Ink)
        when {
            unavailable -> Text(stringResource(R.string.log_offline), fontSize = 20.sp, color = Muted)
            empty -> Text(stringResource(R.string.log_empty), fontSize = 20.sp, color = Muted)
        }
    }
}
