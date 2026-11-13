package app.kidsmedia.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.kidsmedia.AppLanguage
import app.kidsmedia.R

/**
 * The child's language choice on Home (SPEC §14.7): one round button with the flag in use;
 * a tap opens the three flags, a tap on one switches the interface. No parent PIN.
 */
@Composable
fun LanguageFlags(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val current = language()
    var open by remember { mutableStateOf(false) }
    val group = stringResource(R.string.settings_language)
    val currentName = stringResource(languageName(current))
    Box(modifier) {
        FlagButton(AppLanguage.flag(current) ?: "🌐", selected = false, "$group: $currentName") { open = true }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            Row(
                Modifier.padding(horizontal = 12.dp, vertical = 4.dp).semantics { contentDescription = group },
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                for ((tag, flag) in AppLanguage.CHOICES) {
                    val selected = tag == current
                    FlagButton(flag, selected, stringResource(languageName(tag))) {
                        open = false
                        if (!selected) AppLanguage.set(context, tag)
                    }
                }
            }
        }
    }
}

@Composable
private fun FlagButton(flag: String, selected: Boolean, name: String, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        shape = CircleShape,
        color = Color.White,
        shadowElevation = if (selected) 4.dp else 2.dp,
        border = if (selected) BorderStroke(4.dp, Ink) else null,
        modifier =
        Modifier
            .size(72.dp)
            .semantics {
                contentDescription = name
                this.selected = selected
            }
    ) {
        Box(contentAlignment = Alignment.Center) { Text(flag, fontSize = 36.sp) }
    }
}

private fun languageName(tag: String): Int = when (tag) {
    "de" -> R.string.language_de
    "el" -> R.string.language_el
    else -> R.string.language_en
}
