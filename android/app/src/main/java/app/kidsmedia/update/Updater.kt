package app.kidsmedia.update

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import app.kidsmedia.data.AppRelease
import app.kidsmedia.data.KidsApi
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request

/** Why an update couldn't be installed. Shown in the parent settings. */
class UpdateException(val reason: String) : IOException(reason)

/**
 * Self-update (SPEC §4.3): download the APK with the device token, check its
 * SHA-256 against /api/app/latest, then hand it to PackageInstaller. Android
 * refuses an APK signed with a different key.
 *
 * On Android 12+ the app asks to update itself without a confirmation
 * (USER_ACTION_NOT_REQUIRED). Older versions, or Android deciding otherwise,
 * still need a tap: from the parent settings that tap is offered right away,
 * from an automatic update it waits for the parent (see [InstallReceiver]).
 */
class Updater(private val context: Context, private val http: OkHttpClient, private val api: KidsApi) {
    suspend fun latest(): AppRelease? = try {
        api.latestApp()
    } catch (e: app.kidsmedia.data.ApiException) {
        if (e.status == 404) null else throw e
    }

    suspend fun install(release: AppRelease, automatic: Boolean = false) {
        val file = download(release)
        try {
            withContext(Dispatchers.IO) { commit(file, automatic) }
        } finally {
            file.delete()
        }
    }

    private suspend fun download(release: AppRelease): File = withContext(Dispatchers.IO) {
        val file = File(context.cacheDir, "update.apk")
        val digest = MessageDigest.getInstance("SHA-256")
        http.newCall(Request.Builder().url(api.url(release.url)).get().build()).execute().use { response ->
            if (!response.isSuccessful) throw UpdateException("download_failed")
            val body = response.body ?: throw UpdateException("download_failed")
            file.outputStream().use { out ->
                body.byteStream().use { input ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buffer)
                        if (n < 0) break
                        digest.update(buffer, 0, n)
                        out.write(buffer, 0, n)
                    }
                }
            }
        }
        if (sha256Hex(digest.digest()) != release.sha256.lowercase()) {
            file.delete()
            throw UpdateException("checksum_mismatch")
        }
        file
    }

    private fun commit(file: File, automatic: Boolean) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        params.setAppPackageName(context.packageName)
        params.setSize(file.length())
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            session.openWrite("kidsmedia.apk", 0, file.length()).use { out ->
                file.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val flags =
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
                } else {
                    PendingIntent.FLAG_UPDATE_CURRENT
                }
            val intent =
                Intent(context, InstallReceiver::class.java)
                    .setPackage(context.packageName)
                    .putExtra(InstallReceiver.EXTRA_AUTOMATIC, automatic)
            val pending = PendingIntent.getBroadcast(context, sessionId, intent, flags)
            session.commit(pending.intentSender)
        }
    }

    companion object {
        fun sha256Hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it) }

        /** Newer than the installed app? */
        fun isNewer(release: AppRelease, installedCode: Int): Boolean = release.versionCode > installedCode

        /** How often the app looks for an update on its own. */
        const val AUTO_CHECK_INTERVAL_MS: Long = 6 * 60 * 60 * 1000L

        /**
         * Update on its own now? Only where Android can install without a tap (12+),
         * never while something plays (installing restarts the app), and at most
         * every [AUTO_CHECK_INTERVAL_MS]. Wall-clock times, stored across restarts;
         * [lastCheckMs] is null before the first check, and a clock that was set back
         * counts as due rather than blocking checks until it catches up.
         */
        fun shouldAutoCheck(nowMs: Long, lastCheckMs: Long?, sdkInt: Int, playing: Boolean): Boolean =
            sdkInt >= Build.VERSION_CODES.S &&
                !playing &&
                (lastCheckMs == null || nowMs < lastCheckMs || nowMs - lastCheckMs >= AUTO_CHECK_INTERVAL_MS)

        /**
         * Install [release] automatically? Only if it is newer, and not the version an
         * earlier automatic attempt was committed for ([attemptedCode]): being still older
         * means Android didn't install it, so it isn't downloaded again and again; the
         * parent can install it from the settings. A later version is tried again.
         */
        fun shouldAutoInstall(release: AppRelease, installedCode: Int, attemptedCode: Int?): Boolean =
            isNewer(release, installedCode) && release.versionCode != attemptedCode
    }
}

/**
 * PackageInstaller reports here. If Android still asks for a confirmation, it is
 * shown for an update the parent started in the settings. An automatic update
 * never puts a system dialog in front of the child: it is dropped, and the parent
 * can install it from the settings.
 */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val automatic = intent.getBooleanExtra(EXTRA_AUTOMATIC, false)
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION && automatic) {
            Log.i("kidsmedia", "automatic update needs a confirmation; left for the parent settings")
            val sessionId = intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1)
            if (sessionId >= 0) runCatching { context.packageManager.packageInstaller.abandonSession(sessionId) }
        } else if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            @Suppress("DEPRECATION")
            val confirm =
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                } else {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT)
                }
            confirm?.let { context.startActivity(it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        } else if (status != PackageInstaller.STATUS_SUCCESS) {
            Log.w("kidsmedia", "update failed: $status ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}")
        }
    }

    companion object {
        const val EXTRA_AUTOMATIC = "app.kidsmedia.update.AUTOMATIC"
    }
}
