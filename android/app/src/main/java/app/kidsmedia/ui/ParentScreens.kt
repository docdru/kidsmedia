package app.kidsmedia.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.kidsmedia.BuildConfig
import app.kidsmedia.R
import app.kidsmedia.data.AppRelease
import app.kidsmedia.data.DeviceMe
import app.kidsmedia.data.Pin
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** How long the settings dot must be held (SPEC §4.3). */
const val PARENT_HOLD_MS = 2_000L

/**
 * The small settings dot. Only a 2-second press opens the parent gate, so a
 * child tapping around doesn't end up there.
 */
@Composable
fun SettingsDot(onLongHold: () -> Unit, modifier: Modifier = Modifier) {
    val description = stringResource(R.string.parent_dot)
    val onHold by rememberUpdatedState(onLongHold)
    Box(
        modifier
            .size(64.dp)
            .semantics { contentDescription = description }
            .pointerInput(Unit) {
                awaitEachGesture {
                    awaitFirstDown()
                    val released = withTimeoutOrNull(PARENT_HOLD_MS) { waitForUpOrCancellation() }
                    if (released == null) onHold()
                }
            },
        contentAlignment = Alignment.Center
    ) {
        Box(Modifier.size(14.dp).clip(CircleShape).background(Muted.copy(alpha = 0.45f)))
    }
}

/** How long the fourth dot stays visible before the pad clears for the next entry. */
private const val PIN_CLEAR_DELAY_MS = 200L

/**
 * Four dots and a big keypad. Submits by itself after the fourth digit and then
 * clears, so the next entry (confirming a new PIN, or retrying after "Wrong PIN")
 * starts empty. The same error twice in a row doesn't change `message`, so
 * clearing can't depend on it.
 */
@Composable
fun PinPad(title: String, message: String?, onPin: (String) -> Unit, onCancel: (() -> Unit)?) {
    var digits by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    Column(
        Modifier.fillMaxSize().background(Ground).padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(title, fontSize = 30.sp, fontWeight = FontWeight.Black, color = Ink)
        Spacer(Modifier.height(20.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
            repeat(Pin.LENGTH) { i ->
                Box(
                    Modifier.size(22.dp).clip(CircleShape).background(
                        if (i <
                            digits.length
                        ) {
                            Ink
                        } else {
                            Color(0xFFCBD3E6)
                        }
                    )
                )
            }
        }
        Spacer(Modifier.height(14.dp))
        Text(message ?: " ", color = Color(0xFFA3332A), fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(14.dp))
        val rows = listOf(listOf("1", "2", "3"), listOf("4", "5", "6"), listOf("7", "8", "9"), listOf("", "0", "<"))
        Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
            for (row in rows) {
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    for (key in row) {
                        if (key.isEmpty()) {
                            Spacer(Modifier.size(84.dp))
                        } else {
                            val description = if (key == "<") stringResource(R.string.pin_delete) else key
                            Surface(
                                onClick = {
                                    if (key == "<") {
                                        digits = digits.dropLast(1)
                                    } else if (digits.length < Pin.LENGTH) {
                                        digits += key
                                        if (digits.length == Pin.LENGTH) {
                                            onPin(digits)
                                            scope.launch {
                                                delay(PIN_CLEAR_DELAY_MS)
                                                digits = ""
                                            }
                                        }
                                    }
                                },
                                shape = CircleShape,
                                shadowElevation = 2.dp,
                                modifier = Modifier.size(84.dp).semantics { contentDescription = description }
                            ) {
                                Box(contentAlignment = Alignment.Center) {
                                    Text(
                                        if (key ==
                                            "<"
                                        ) {
                                            "⌫"
                                        } else {
                                            key
                                        },
                                        fontSize = 30.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = Ink
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
        if (onCancel != null) {
            Spacer(Modifier.height(20.dp))
            TextButton(onClick = onCancel) { Text(stringResource(R.string.cancel), fontSize = 20.sp) }
        }
    }
}

@Composable
fun pinErrorText(error: PinError?): String? = when (error) {
    null -> null
    PinError.Wrong -> stringResource(R.string.pin_wrong)
    PinError.Mismatch -> stringResource(R.string.pin_mismatch)
    is PinError.Locked -> stringResource(R.string.pin_locked, error.seconds.toInt())
}

/** First run (SPEC §8.6) and "Change PIN": enter it, then confirm it. */
@Composable
fun SetPinScreen(error: PinError?, onSet: (String, String) -> Unit, onCancel: (() -> Unit)?) {
    var first by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(error) { if (error == PinError.Mismatch) first = null }
    PinPad(
        title = stringResource(if (first == null) R.string.pin_set_title else R.string.pin_confirm_title),
        message = pinErrorText(error),
        onPin = { pin ->
            val f = first
            if (f == null) first = pin else onSet(f, pin)
        },
        onCancel = onCancel
    )
}

/** Behind the parent gate (SPEC §8.5). */
@Composable
fun SettingsScreen(
    serverUrl: String,
    device: DeviceMe?,
    update: UpdateState,
    onCheckUpdate: () -> Unit,
    onInstall: (AppRelease) -> Unit,
    onChangePin: () -> Unit,
    onUnpair: () -> Unit,
    onClose: () -> Unit
) {
    var confirmUnpair by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(20.dp)) {
            BackButton(onClose)
            Text(stringResource(R.string.settings_title), fontSize = 34.sp, fontWeight = FontWeight.Black, color = Ink)
        }
        Spacer(Modifier.height(20.dp))
        Surface(shape = RoundedCornerShape(22.dp), shadowElevation = 2.dp, modifier = Modifier.width(640.dp)) {
            Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                InfoRow(stringResource(R.string.settings_server), serverUrl)
                HorizontalDivider()
                InfoRow(stringResource(R.string.settings_device), device?.name ?: "–")
                HorizontalDivider()
                InfoRow(
                    stringResource(R.string.settings_max_volume),
                    device?.maxVolumePercent?.let { stringResource(R.string.settings_max_volume_percent, it) }
                        ?: stringResource(R.string.settings_max_volume_none)
                )
                HorizontalDivider()
                InfoRow(stringResource(R.string.settings_version), BuildConfig.VERSION_NAME)
                UpdateRow(update, onCheckUpdate, onInstall)
                HorizontalDivider()
                Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    OutlinedButton(onClick = onChangePin) { Text(stringResource(R.string.settings_change_pin)) }
                    OutlinedButton(onClick = {
                        confirmUnpair = true
                    }) { Text(stringResource(R.string.settings_unpair)) }
                }
            }
        }
    }
    if (confirmUnpair) {
        AlertDialog(
            onDismissRequest = { confirmUnpair = false },
            title = { Text(stringResource(R.string.settings_unpair)) },
            text = { Text(stringResource(R.string.settings_unpair_confirm)) },
            confirmButton = {
                Button(onClick = {
                    confirmUnpair = false
                    onUnpair()
                }) { Text(stringResource(R.string.settings_unpair)) }
            },
            dismissButton = {
                TextButton(onClick = { confirmUnpair = false }) { Text(stringResource(R.string.cancel)) }
            }
        )
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = Muted, fontWeight = FontWeight.Bold)
        Text(value, color = Ink, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun UpdateRow(update: UpdateState, onCheck: () -> Unit, onInstall: (AppRelease) -> Unit) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        val text =
            when (update) {
                UpdateState.Idle -> ""
                UpdateState.Checking -> stringResource(R.string.update_checking)
                UpdateState.UpToDate -> stringResource(R.string.update_up_to_date)
                is UpdateState.Available -> stringResource(R.string.update_available, update.release.versionName)
                UpdateState.Installing -> stringResource(R.string.update_installing)
                is UpdateState.Failed ->
                    stringResource(
                        when (update.reason) {
                            "checksum_mismatch" -> R.string.update_failed_checksum
                            "offline" -> R.string.reason_offline
                            else -> R.string.update_failed
                        }
                    )
            }
        Text(text, color = Muted)
        when (update) {
            is UpdateState.Available ->
                Button(onClick = { onInstall(update.release) }) { Text(stringResource(R.string.update_install)) }
            UpdateState.Checking, UpdateState.Installing -> Unit
            else -> OutlinedButton(onClick = onCheck) { Text(stringResource(R.string.update_check)) }
        }
    }
}
