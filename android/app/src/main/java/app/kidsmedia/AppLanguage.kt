package app.kidsmedia

import android.app.Activity
import android.app.LocaleManager
import android.content.Context
import android.content.ContextWrapper
import android.content.res.Configuration
import android.os.Build
import android.os.LocaleList
import androidx.core.content.edit

/**
 * The app's interface language (SPEC §4.3, §14.7), chosen by the child with the flags on Home.
 * Until a flag is tapped the app follows the system.
 *
 * Android 13+ keeps the choice as the per-app language (`LocaleManager`, which rebuilds the
 * activity). Older versions store it here and apply it to the activity's context in
 * [MainActivity.attachBaseContext]; [set] then rebuilds the activity.
 */
object AppLanguage {
    /** The interface languages with their flags, in the order Home shows them. */
    val CHOICES = listOf("en" to "🇬🇧", "de" to "🇩🇪", "el" to "🇬🇷")

    private const val PREFS = "app_language"
    private const val KEY = "tag"

    /** The flag for a language tag, or null for one the app doesn't offer. */
    fun flag(tag: String): String? = CHOICES.firstOrNull { it.first == tag }?.second

    /** The chosen tag ("en", "de", "el"), or "" while the app follows the system. */
    fun chosen(context: Context): String = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        context.getSystemService(LocaleManager::class.java).applicationLocales.toLanguageTags()
    } else {
        prefs(context).getString(KEY, "").orEmpty()
    }

    /** Choose [tag] and show the interface in it right away. */
    fun set(context: Context, tag: String) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.getSystemService(LocaleManager::class.java).applicationLocales =
                if (tag.isEmpty()) LocaleList.getEmptyLocaleList() else LocaleList.forLanguageTags(tag)
            return
        }
        if (chosen(context) == tag) return
        prefs(context).edit { putString(KEY, tag) }
        context.findActivity()?.recreate()
    }

    /** Before Android 13: [base] with the chosen language, for `attachBaseContext`. */
    fun wrap(base: Context): Context {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) return base
        val tag = prefs(base).getString(KEY, "").orEmpty()
        if (tag.isEmpty()) return base
        val config = Configuration(base.resources.configuration)
        config.setLocales(LocaleList.forLanguageTags(tag))
        return base.createConfigurationContext(config)
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private tailrec fun Context.findActivity(): Activity? = when (this) {
        is Activity -> this
        is ContextWrapper -> baseContext.findActivity()
        else -> null
    }
}
