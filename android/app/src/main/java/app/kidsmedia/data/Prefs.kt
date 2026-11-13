package app.kidsmedia.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.withContext

private val Context.dataStore by preferencesDataStore(name = "kidsmedia")

data class Pairing(val baseUrl: String, val token: String)

/**
 * Automatic updates (SPEC §4.3), kept across restarts: Android kills the app in the
 * background often, and a check that only lived in memory would run again each time.
 * [checkedAtMs]: wall clock of the last check. [attemptedCode]: the last version an
 * automatic update was committed for; still older than that after a restart means
 * Android didn't install it (e.g. it wanted a confirmation).
 */
data class AutoUpdateState(val checkedAtMs: Long? = null, val attemptedCode: Int? = null)

/** Server address and device token (SPEC §4.3 storage). Exposed synchronously for OkHttp. */
class Prefs(private val context: Context, scope: CoroutineScope) : Credentials {
    private val urlKey = stringPreferencesKey("server_url")
    private val tokenKey = stringPreferencesKey("device_token")
    private val pinKey = stringPreferencesKey("parent_pin")
    private val autoCheckedKey = longPreferencesKey("auto_update_checked_at")
    private val autoAttemptedKey = intPreferencesKey("auto_update_attempted_code")
    private val maxVolumeKey = intPreferencesKey("max_volume_percent")

    /** False until DataStore has been read once, so the UI doesn't flash the setup screen. */
    val loaded: StateFlow<Boolean> =
        context.dataStore.data.map { true }.stateIn(scope, SharingStarted.Eagerly, false)

    /** Hashed parent PIN (SPEC §4.3), or null before it is set. */
    val pinHash: StateFlow<String?> =
        context.dataStore.data.map { it[pinKey] }.stateIn(scope, SharingStarted.Eagerly, null)

    val pairing: StateFlow<Pairing?> =
        context.dataStore.data
            .map { p ->
                val url = p[urlKey]
                val token = p[tokenKey]
                if (url != null && token != null) Pairing(url, token) else null
            }.stateIn(scope, SharingStarted.Eagerly, null)

    /**
     * The volume cap from the server, kept so it holds after a restart and while offline.
     * It stays when the tablet is re-paired: only the server lifts it.
     */
    val maxVolumePercent: StateFlow<Int?> =
        context.dataStore.data.map { it[maxVolumeKey] }.stateIn(scope, SharingStarted.Eagerly, null)

    override val baseUrl: String? get() = pairing.value?.baseUrl
    override val token: String? get() = pairing.value?.token

    suspend fun save(pairing: Pairing) {
        context.dataStore.edit {
            it[urlKey] = pairing.baseUrl
            it[tokenKey] = pairing.token
        }
    }

    suspend fun savePin(pin: String) {
        val hashed = withContext(Dispatchers.Default) { Pin.hash(pin) }
        context.dataStore.edit { it[pinKey] = hashed }
    }

    suspend fun saveMaxVolume(percent: Int?) {
        if (percent == maxVolumePercent.value) return
        context.dataStore.edit {
            if (percent == null) {
                it.remove(maxVolumeKey)
            } else {
                it[maxVolumeKey] = percent
            }
        }
    }

    suspend fun autoUpdate(): AutoUpdateState = context.dataStore.data.first().let {
        AutoUpdateState(it[autoCheckedKey], it[autoAttemptedKey])
    }

    suspend fun autoUpdateChecked(atMs: Long) {
        context.dataStore.edit { it[autoCheckedKey] = atMs }
    }

    suspend fun autoUpdateAttempted(versionCode: Int) {
        context.dataStore.edit { it[autoAttemptedKey] = versionCode }
    }

    /** Forget the token (revoked or re-pair). The server URL and PIN stay. */
    suspend fun clear() {
        context.dataStore.edit {
            it.remove(tokenKey)
        }
    }
}
