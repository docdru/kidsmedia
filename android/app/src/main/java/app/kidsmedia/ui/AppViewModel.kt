package app.kidsmedia.ui

import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.kidsmedia.BuildConfig
import app.kidsmedia.Graph
import app.kidsmedia.activities.ActivitiesView
import app.kidsmedia.activities.ActivityLogic
import app.kidsmedia.activities.CalcLogic
import app.kidsmedia.activities.CalcProgress
import app.kidsmedia.activities.CalcStep
import app.kidsmedia.activities.OpenLogic
import app.kidsmedia.activities.OpenState
import app.kidsmedia.activities.PhotoLogic
import app.kidsmedia.activities.PhotoOutcome
import app.kidsmedia.activities.ReadLogic
import app.kidsmedia.activities.ReadProgress
import app.kidsmedia.activities.ReadStep
import app.kidsmedia.data.Activities
import app.kidsmedia.data.ApiException
import app.kidsmedia.data.AppRelease
import app.kidsmedia.data.CalcProblem
import app.kidsmedia.data.DeviceMe
import app.kidsmedia.data.DoneRequest
import app.kidsmedia.data.Gems
import app.kidsmedia.data.Library
import app.kidsmedia.data.PairRequest
import app.kidsmedia.data.Pairing
import app.kidsmedia.data.PendingPhoto
import app.kidsmedia.data.Pin
import app.kidsmedia.data.PinAttempts
import app.kidsmedia.data.ReadQuestion
import app.kidsmedia.data.Rewards
import app.kidsmedia.data.ServerUrl
import app.kidsmedia.playback.Block
import app.kidsmedia.update.UpdateException
import app.kidsmedia.update.Updater
import coil3.SingletonImageLoader
import coil3.request.ImageRequest
import java.io.File
import java.io.IOException
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonPrimitive

sealed interface Screen {
    data object Home : Screen

    data class Group(val groupId: Int) : Screen

    data object Player : Screen

    /** The current story world, showing chapter [position] (SPEC §13.7). */
    data class Story(val position: Int) : Screen

    data class Task(val position: Int, val kind: String) : Screen

    /** The in-app camera for a draw or write task, then "Send this picture?" (SPEC §15.3). */
    data class Photo(val position: Int, val kind: String) : Screen

    /** "Chapter N done!" after a task. */
    data class TaskDone(val position: Int) : Screen

    /** Stars, sticker album, swaps and wishes (SPEC §14.4). */
    data object Rewards : Screen

    /** The gems owned and what they open, a tap on the balance on Home (SPEC §16.4). */
    data object Gems : Screen

    /** PIN pad in front of the settings (SPEC §4.3 parent gate). */
    data object ParentGate : Screen

    data object Settings : Screen
}

/** Self-update in the parent settings (SPEC §4.3). */
sealed interface UpdateState {
    data object Idle : UpdateState

    data object Checking : UpdateState

    data object UpToDate : UpdateState

    data class Available(val release: AppRelease) : UpdateState

    data object Installing : UpdateState

    data class Failed(val reason: String) : UpdateState
}

/** State of the PIN pad: wrong PIN, or locked for a while after too many tries. */
sealed interface PinError {
    data object Wrong : PinError

    data object Mismatch : PinError

    data class Locked(val seconds: Long) : PinError
}

enum class SetupError { INVALID_URL, HTTPS_REQUIRED, BAD_CODE, OFFLINE, RATE_LIMITED }

/** A short line on the Rewards screen after a tap. */
enum class RewardNotice { OFFLINE, SWAPPED }

class AppViewModel(private val graph: Graph) : ViewModel() {
    val pairing = graph.prefs.pairing
    val nowPlaying = graph.playback.nowPlaying
    val block: StateFlow<Block?> = graph.playback.block
    val player = graph.playback.player

    private val _library = MutableStateFlow<Library?>(null)
    val library: StateFlow<Library?> = _library.asStateFlow()

    /** The gem balance shown on Home (SPEC §16.4); offline the snapshot's minus the gems spent since. */
    private val _gems = MutableStateFlow(Gems())
    val gems: StateFlow<Gems> = _gems.asStateFlow()

    private val _activities = MutableStateFlow<ActivitiesView?>(null)
    val activities: StateFlow<ActivitiesView?> = _activities.asStateFlow()

    /** The last rewards answer; null before the first one, or on servers without rewards (SPEC §14.4). */
    private val _rewards = MutableStateFlow<Rewards?>(null)
    val rewards: StateFlow<Rewards?> = _rewards.asStateFlow()

    private val _rewardNotice = MutableStateFlow<RewardNotice?>(null)
    val rewardNotice: StateFlow<RewardNotice?> = _rewardNotice.asStateFlow()

    private val _rewardBusy = MutableStateFlow(false)
    val rewardBusy: StateFlow<Boolean> = _rewardBusy.asStateFlow()

    /** Where the child is in the open `read` task (SPEC §14.2). */
    private val _reading = MutableStateFlow(ReadLogic.start())
    val reading: StateFlow<ReadProgress> = _reading.asStateFlow()

    /** Where the child is in the open `calc` task (SPEC §15.2). */
    private val _calc = MutableStateFlow(CalcLogic.start())
    val calc: StateFlow<CalcProgress> = _calc.asStateFlow()

    /** The short "Right!" before the next problem. */
    private var calcNext: Job? = null

    /** One photo upload at a time, so a refresh and a "Send" never send the same photo twice. */
    private val photoUploads = Mutex()

    private val _offline = MutableStateFlow(false)
    val offline: StateFlow<Boolean> = _offline.asStateFlow()

    private val _screen = MutableStateFlow<Screen>(Screen.Home)
    val screen: StateFlow<Screen> = _screen.asStateFlow()

    private val _setupError = MutableStateFlow<SetupError?>(null)
    val setupError: StateFlow<SetupError?> = _setupError.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    private var poller: Job? = null
    private var ticker: Job? = null

    /** The last activities answer published, re-evaluated every minute for the opening times (§14.4a). */
    private var lastActivities: Activities? = null

    /** Trusted time the open task screen was opened: for the grace after closing (§14.4a). */
    private var taskOpenedAt: LocalDateTime? = null

    val loaded = graph.prefs.loaded
    val pinHash = graph.prefs.pinHash

    private val _pinError = MutableStateFlow<PinError?>(null)
    val pinError: StateFlow<PinError?> = _pinError.asStateFlow()

    private val attempts = PinAttempts({ SystemClock.elapsedRealtime() })

    private val _update = MutableStateFlow<UpdateState>(UpdateState.Idle)
    val update: StateFlow<UpdateState> = _update.asStateFlow()

    private val _device = MutableStateFlow<DeviceMe?>(null)
    val device: StateFlow<DeviceMe?> = _device.asStateFlow()

    /** Refresh the library now and every minute while the UI is visible. */
    fun startPolling() {
        poller?.cancel()
        poller =
            viewModelScope.launch {
                while (isActive) {
                    refresh()
                    autoUpdate()
                    delay(60_000)
                }
            }
        // Opening times (SPEC §14.4a) change on the minute, online or not: re-evaluate then.
        ticker?.cancel()
        ticker =
            viewModelScope.launch {
                while (isActive) {
                    val nowMs = withContext(Dispatchers.IO) { graph.offline.now() }.toInstant().toEpochMilli()
                    delay(60_000 - nowMs % 60_000 + 200)
                    lastActivities?.let { publish(it) }
                }
            }
    }

    /**
     * Install a newer app from the server on its own (SPEC §4.3): release images carry
     * the APK of their tag, so the tablet follows the server. Android 12+ only, never
     * while something plays, and not while the parent is in the settings.
     */
    private suspend fun autoUpdate() {
        val playing = graph.playback.player.value?.isPlaying == true
        if (pairing.value == null || _screen.value == Screen.Settings) return
        val state = graph.prefs.autoUpdate()
        val now = System.currentTimeMillis()
        if (!Updater.shouldAutoCheck(now, state.checkedAtMs, Build.VERSION.SDK_INT, playing)) return
        graph.prefs.autoUpdateChecked(now)
        try {
            val release = graph.updater.latest() ?: return
            if (!Updater.shouldAutoInstall(release, BuildConfig.VERSION_CODE, state.attemptedCode)) return
            Log.i("kidsmedia", "updating to ${release.versionName} (${release.versionCode})")
            graph.updater.install(release, automatic = true)
            // Committed. If it works, the app restarts as this version; if Android drops it,
            // it isn't downloaded again automatically and waits for the parent settings.
            graph.prefs.autoUpdateAttempted(release.versionCode)
        } catch (e: IOException) {
            Log.w("kidsmedia", "automatic update: $e")
        }
    }

    fun stopPolling() {
        poller?.cancel()
        ticker?.cancel()
    }

    suspend fun refresh() {
        if (pairing.value == null) return
        try {
            val library = graph.api.library()
            _library.value = library
            _gems.value = library.gems
            _offline.value = false
            graph.prefs.saveMaxVolume(library.maxVolumePercent)
            // Offline mode (M7): send queued plays, refresh the snapshot and the copies.
            viewModelScope.launch {
                runCatching { graph.offline.sync(library) }.onFailure { Log.w("kidsmedia", "offline sync: $it") }
            }
            viewModelScope.launch {
                refreshActivities()
                // After the queued "done" taps, so their stars are in.
                refreshRewards()
            }
        } catch (e: ApiException) {
            if (e.status == 401) graph.prefs.clear() // revoked: back to pairing (SPEC §5.1)
        } catch (e: IOException) {
            _offline.value = true
            // Show the last known library; only downloaded items can play (M7).
            if (_library.value == null) _library.value = withContext(Dispatchers.IO) { graph.offline.cachedLibrary() }
            val offlineGems = withContext(Dispatchers.IO) { graph.offline.gemBalance() }
            _library.value?.gems?.let { _gems.value = it.copy(balance = offlineGems ?: it.balance) }
            graph.activities.cached()?.let { publish(it) }
            if (_rewards.value == null) _rewards.value = graph.activities.cachedRewards()
        }
    }

    // ---- activities (SPEC §13) ----

    /**
     * Send queued "done" taps, then queued photos oldest first (SPEC §15.3), then fetch the
     * activities; offline, show the cached ones.
     */
    private suspend fun refreshActivities() {
        try {
            for (request in graph.activities.pending()) {
                try {
                    graph.api.activityDone(request)
                    graph.activities.removePending(request)
                } catch (e: ApiException) {
                    // 404 unknown_task or 409 activity_locked: the parent changed the plan; drop it.
                    // 409 activities_closed: the tap (its done_at) was after the grace (§14.4a).
                    // 409 activity_too_old: the server no longer takes a tap that old.
                    // 422: the request itself can't be taken (it would fail every time).
                    if (e.status == 404 || e.status == 409 || e.status == 422) {
                        graph.activities.removePending(request)
                    }
                }
            }
            graph.activities.pruneQueuedFiles()
            for (photo in PhotoLogic.uploadOrder(graph.activities.pendingPhotos())) uploadPhoto(photo)
            val fresh = graph.api.activities()
            graph.activities.save(fresh)
            publish(fresh)
            prefetchPictures(fresh)
            prefetchSpeech(fresh)
        } catch (e: ApiException) {
            Log.w("kidsmedia", "activities: $e")
        } catch (e: IOException) {
            graph.activities.cached()?.let { publish(it) }
        }
    }

    /**
     * The trusted clock in the server's time zone (SPEC §14.4a): opening times and chapter
     * unlocks are the server's local times, whatever zone the tablet is set to.
     */
    private suspend fun serverNow(data: Activities? = lastActivities): ZonedDateTime = withContext(Dispatchers.IO) {
        graph.offline.now().atZoneSameInstant(graph.offline.zone(data?.serverTime))
    }

    private suspend fun localNow(data: Activities? = lastActivities): LocalDateTime = serverNow(data).toLocalDateTime()

    private suspend fun publish(data: Activities) {
        val now = localNow(data)
        // A photo not sent yet shows its task as done, like a queued tap (SPEC §15.3).
        val pending = graph.activities.pending() + graph.activities.pendingPhotos().map(PendingPhoto::asDone)
        val view = ActivityLogic.view(data, pending, now)
        lastActivities = data
        _activities.value = view
        enforceOpening(view, now)
    }

    /**
     * Ideas closed (SPEC §14.4a): the World screen goes back to Home with the reason; a task
     * screen stays while "I'm done!" is still accepted (the grace), then goes too.
     */
    private fun enforceOpening(view: ActivitiesView, now: LocalDateTime) {
        val closed = view.open as? OpenState.Closed ?: return
        when (_screen.value) {
            is Screen.Story -> closeIdeas(closed, view.today)
            is Screen.Task, is Screen.Photo -> if (!mayFinish(view, now)) closeIdeas(closed, view.today)
            else -> Unit
        }
    }

    private fun mayFinish(view: ActivitiesView, now: LocalDateTime): Boolean {
        val opened = taskOpenedAt ?: return false
        return OpenLogic.mayFinish(view.data.windows, opened, now)
    }

    /** Back to Home with "Ideas are resting until …" on the reason screen. */
    private fun closeIdeas(closed: OpenState.Closed, today: LocalDate) {
        taskOpenedAt = null
        _screen.value = Screen.Home
        graph.playback.showBlock(Block(IDEAS_CLOSED, closedParams(closed, today)))
    }

    /** `time` (HH:mm) of the next opening, and `day` (0 = Monday) if it isn't [today]. */
    private fun closedParams(closed: OpenState.Closed, today: LocalDate): Map<String, JsonPrimitive> {
        val at = closed.opensAt ?: return emptyMap()
        val time = "time" to JsonPrimitive(at.format(DateTimeFormatter.ofPattern("HH:mm")))
        val day = "day" to JsonPrimitive(at.dayOfWeek.value - 1)
        return if (at.toLocalDate() == today) mapOf(time) else mapOf(time, day)
    }

    /**
     * Load the next pictures into Coil's disk cache (SPEC §13.5) while online, so the story
     * still shows them offline. Same `md` URLs as the screens, so the cache keys match.
     */
    private fun prefetchPictures(data: Activities) {
        val base = pairing.value?.baseUrl ?: return
        val world = data.world ?: return
        val loader = SingletonImageLoader.get(graph.app)
        for (ref in ActivityLogic.prefetchPictures(world)) {
            val url = thumbUrl(base, ref, "md") ?: continue
            loader.enqueue(ImageRequest.Builder(graph.app).data(url).build())
        }
    }

    /**
     * Keep the speech of the current and next chapter for offline use (SPEC §14.3), in the
     * texts the screens show for the app's language.
     */
    private fun prefetchSpeech(data: Activities) {
        val language = graph.app.resources.configuration.locales[0].language
        viewModelScope.launch {
            runCatching { graph.speech.sync(data, language) }.onFailure { Log.w("kidsmedia", "speech sync: $it") }
        }
    }

    /** The world tile: the story, or while Ideas are closed the friendly reason (SPEC §14.4a). */
    fun openStory() {
        val view = _activities.value ?: return
        val world = view.world ?: return
        viewModelScope.launch {
            when (val state = OpenLogic.openState(view.data.windows, localNow(view.data))) {
                is OpenState.Closed -> closeIdeas(state, view.today)
                OpenState.Open -> _screen.value = Screen.Story(ActivityLogic.firstOpen(world))
            }
        }
    }

    fun selectChapter(position: Int) {
        val chapter = _activities.value?.world?.chapters?.firstOrNull { it.position == position } ?: return
        if (chapter.unlocked) _screen.value = Screen.Story(position)
    }

    fun openTask(position: Int, kind: String) {
        val chapter = _activities.value?.world?.chapters?.firstOrNull { it.position == position } ?: return
        if (!chapter.unlocked || chapter.tasks.none { it.kind == kind }) return
        val view = _activities.value ?: return
        viewModelScope.launch {
            val now = localNow(view.data)
            when (val state = OpenLogic.openState(view.data.windows, now)) {
                is OpenState.Closed -> closeIdeas(state, view.today)
                OpenState.Open -> {
                    taskOpenedAt = now
                    _reading.value = ReadLogic.start()
                    // Leaving a problem set starts it again next time (SPEC §15.2).
                    calcNext?.cancel()
                    _calc.value = CalcLogic.start()
                    _screen.value = Screen.Task(position, kind)
                }
            }
        }
    }

    // ---- reading (SPEC §14.2) ----

    /** "I've read it": on to the questions [questions] (those shown, in the shown language). */
    fun readIt(position: Int, questions: List<ReadQuestion>) {
        _reading.value = ReadLogic.readIt(_reading.value, questions)
        finishReadIfDone(position)
    }

    fun answerRead(position: Int, questions: List<ReadQuestion>, answer: Int) {
        _reading.value = ReadLogic.answer(_reading.value, questions, answer)
        finishReadIfDone(position)
    }

    /** After a wrong answer: back to the text. */
    fun readAgain() {
        _reading.value = ReadLogic.again(_reading.value)
    }

    private fun finishReadIfDone(position: Int) {
        val progress = _reading.value
        if (progress.step == ReadStep.DONE) finishTask(position, "read", progress.attempts)
    }

    // ---- maths problems (SPEC §15.2) ----

    /** "Let's start": the first of [problems] (those shown, in the shown language). */
    fun startCalc(position: Int, problems: List<CalcProblem>) {
        _calc.value = CalcLogic.begin(_calc.value, problems)
        finishCalcIfDone(position)
    }

    fun calcDigit(digit: Int) {
        _calc.value = CalcLogic.digit(_calc.value, digit)
    }

    fun calcDelete() {
        _calc.value = CalcLogic.delete(_calc.value)
    }

    /** The ✓ key: a right answer shows "Right!" for a moment, then the next problem. */
    fun calcCheck(position: Int, problems: List<CalcProblem>) {
        val checked = CalcLogic.check(_calc.value, problems)
        _calc.value = checked
        if (checked.step == CalcStep.RIGHT) {
            calcNext?.cancel()
            calcNext = viewModelScope.launch {
                delay(RIGHT_MS)
                _calc.value = CalcLogic.next(_calc.value)
            }
        }
        finishCalcIfDone(position)
    }

    private fun finishCalcIfDone(position: Int) {
        val progress = _calc.value
        if (progress.step == CalcStep.DONE) finishTask(position, "calc", progress.attempts)
    }

    // ---- photos (SPEC §15.3) ----

    /** "Take a picture" on a draw or write task: the camera, after clearing pictures never sent. */
    fun openPhoto(position: Int, kind: String) {
        if (_screen.value != Screen.Task(position, kind)) return
        viewModelScope.launch {
            graph.activities.clearShots()
            _screen.value = Screen.Photo(position, kind)
        }
    }

    /** A file in the app's private storage for the camera to write the next picture to. */
    fun newShot(): File = graph.activities.newShot()

    /** "Take again": the picture shown goes. */
    fun discardShot(shot: File) {
        viewModelScope.launch(Dispatchers.IO) { shot.delete() }
    }

    /**
     * "Send": the photo is queued with its task and time first, so the task shows as done at
     * once and the photo survives being offline (SPEC §15.3), then uploaded.
     */
    fun sendPhoto(position: Int, kind: String, shot: File) {
        val view = _activities.value ?: return
        val world = view.world ?: return
        viewModelScope.launch {
            val at = serverNow(view.data)
            if (closedPastGrace(view, at.toLocalDateTime())) return@launch
            val doneAt = ActivityLogic.doneAt(at)
            val photo = graph.activities.queuePhoto(shot, world.id, position, kind, doneAt)
            if (photo == null) {
                // The picture's file is gone: back to the task, so "Take a picture" starts again.
                _screen.value = Screen.Task(position, kind)
                return@launch
            }
            taskOpenedAt = null
            _screen.value = Screen.TaskDone(position)
            publish(view.data)
            try {
                val answer = uploadPhoto(photo)
                if (answer == null) {
                    refreshActivities() // refused, or kept for later: show what is true now
                } else {
                    graph.activities.save(answer)
                    publish(answer)
                    prefetchPictures(answer)
                    prefetchSpeech(answer)
                    refreshRewards() // a new star, maybe a new sticker
                }
            } catch (e: IOException) {
                Log.i("kidsmedia", "activity photo queued offline: $e")
            }
        }
    }

    /**
     * Upload [photo] if it is still queued. Returns the server's answer once it is stored; null if
     * it was refused for good (then forgotten) or is kept for later (a timeout or 429).
     * Offline the IOException goes to the caller and the photo stays queued.
     */
    private suspend fun uploadPhoto(photo: PendingPhoto): Activities? = photoUploads.withLock {
        if (photo !in graph.activities.pendingPhotos()) return@withLock null
        val file = graph.activities.photoFile(photo)
        if (file == null) {
            graph.activities.removePhoto(photo)
            return@withLock null
        }
        try {
            graph.api.activityPhoto(photo, file).also { graph.activities.removePhoto(photo) }
        } catch (e: ApiException) {
            Log.w("kidsmedia", "activity photo: $e")
            if (PhotoLogic.outcome(e.status) != PhotoOutcome.KEEP) graph.activities.removePhoto(photo)
            null
        }
    }

    /** Past the grace after closing the server would refuse a task (§14.4a): say why instead. */
    private fun closedPastGrace(view: ActivitiesView, now: LocalDateTime): Boolean {
        val state = OpenLogic.openState(view.data.windows, now)
        if (state is OpenState.Closed && !mayFinish(view, now)) {
            closeIdeas(state, view.today)
            return true
        }
        return false
    }

    /**
     * "I'm done!": shown as done at once, saved for offline, then sent (SPEC §13.3).
     * [attempts] only for `read` and `calc` tasks (§14.2, §15.2); it is queued with the tap, and
     * so is the tap's time, so a tap sent later counts for when it happened (§14.4a).
     */
    fun finishTask(position: Int, kind: String, attempts: Int? = null) {
        val view = _activities.value ?: return
        val world = view.world ?: return
        viewModelScope.launch {
            val at = serverNow(view.data)
            val request = DoneRequest(world.id, position, kind, attempts, ActivityLogic.doneAt(at))
            if (closedPastGrace(view, at.toLocalDateTime())) return@launch
            taskOpenedAt = null
            _screen.value = Screen.TaskDone(position)
            graph.activities.addPending(request)
            publish(view.data)
            try {
                val answer = graph.api.activityDone(request)
                graph.activities.removePending(request)
                graph.activities.save(answer)
                publish(answer)
                prefetchPictures(answer)
                prefetchSpeech(answer)
                refreshRewards() // a new star, maybe a new sticker
            } catch (e: ApiException) {
                if (e.status == 404 || e.status == 409) {
                    graph.activities.removePending(request)
                    refreshActivities()
                }
            } catch (e: IOException) {
                Log.i("kidsmedia", "activity done queued offline: $e")
            }
        }
    }

    // ---- rewards (SPEC §14.4) ----

    /** Fetch the rewards; offline, keep showing the cached ones. */
    private suspend fun refreshRewards() {
        try {
            val fresh = graph.api.rewards()
            graph.activities.saveRewards(fresh)
            _rewards.value = fresh
        } catch (e: ApiException) {
            // A server without rewards (404) hides the stars.
            if (e.status == 404) _rewards.value = null else Log.w("kidsmedia", "rewards: $e")
        } catch (e: IOException) {
            if (_rewards.value == null) _rewards.value = graph.activities.cachedRewards()
        }
    }

    fun openGems() {
        _screen.value = Screen.Gems
    }

    fun openRewards() {
        if (_rewards.value == null) return
        _rewardNotice.value = null
        _screen.value = Screen.Rewards
        viewModelScope.launch { refreshRewards() }
    }

    /** "Swap N stars": the bonus is the server's (§3.5), so the library is refreshed for the new limits. */
    fun redeemSwap(swapId: Int) = rewardAction(RewardNotice.SWAPPED) { graph.api.redeemSwap(swapId) }

    /** "I'd like this": a request the parent approves or declines in the admin. */
    fun requestWish(wishId: Int) = rewardAction(null) { graph.api.requestWish(wishId) }

    private fun rewardAction(success: RewardNotice?, action: suspend () -> Rewards) {
        if (_rewardBusy.value) return
        _rewardBusy.value = true
        _rewardNotice.value = null
        viewModelScope.launch {
            try {
                val answer = action()
                graph.activities.saveRewards(answer)
                _rewards.value = answer
                _rewardNotice.value = success
                if (success == RewardNotice.SWAPPED) refresh()
            } catch (e: ApiException) {
                // 409 not_enough_stars, swap_limit_reached, request_pending: show what is true now.
                Log.i("kidsmedia", "reward action: $e")
                refreshRewards()
            } catch (e: IOException) {
                _rewardNotice.value = RewardNotice.OFFLINE
            } finally {
                _rewardBusy.value = false
            }
        }
    }

    fun pair(serverInput: String, code: String) {
        val url =
            when (val r = ServerUrl.normalize(serverInput)) {
                is ServerUrl.Result.Ok -> r.url
                ServerUrl.Result.HttpsRequired -> return fail(SetupError.HTTPS_REQUIRED)
                ServerUrl.Result.Invalid -> return fail(SetupError.INVALID_URL)
            }
        viewModelScope.launch {
            _busy.value = true
            _setupError.value = null
            try {
                val answer =
                    graph.api.pair(
                        url,
                        PairRequest(code.trim(), deviceName = Build.MODEL, appVersion = BuildConfig.VERSION_NAME)
                    )
                graph.prefs.save(Pairing(url, answer.token))
                _screen.value = Screen.Home
                refresh()
            } catch (e: ApiException) {
                _setupError.value = if (e.status == 429) SetupError.RATE_LIMITED else SetupError.BAD_CODE
            } catch (e: IOException) {
                _setupError.value = SetupError.OFFLINE
            } finally {
                _busy.value = false
            }
        }
    }

    private fun fail(error: SetupError) {
        _setupError.value = error
    }

    fun openGroup(groupId: Int) {
        _screen.value = Screen.Group(groupId)
    }

    fun openPlayer() {
        if (nowPlaying.value != null) _screen.value = Screen.Player
    }

    fun back() {
        _screen.value =
            when (val s = _screen.value) {
                Screen.Player -> nowPlaying.value?.let { Screen.Group(it.group.id) } ?: Screen.Home
                is Screen.Group -> Screen.Home
                is Screen.Story -> Screen.Home
                is Screen.Task, is Screen.TaskDone -> storyOrHome(s)
                // Leaving the camera never finishes the task (SPEC §15.1): back to the task.
                is Screen.Photo -> Screen.Task(s.position, s.kind)
                Screen.Rewards, Screen.Gems -> Screen.Home
                Screen.Home -> s
                Screen.ParentGate, Screen.Settings -> Screen.Home
            }
    }

    /** Back from a task to its chapter, or to Home with the reason once Ideas are closed (§14.4a). */
    private fun storyOrHome(screen: Screen): Screen {
        taskOpenedAt = null
        val position = when (screen) {
            is Screen.Task -> screen.position
            is Screen.TaskDone -> screen.position
            else -> return Screen.Home
        }
        val view = _activities.value
        val closed = view?.open as? OpenState.Closed ?: return Screen.Story(position)
        graph.playback.showBlock(Block(IDEAS_CLOSED, closedParams(closed, view.today)))
        return Screen.Home
    }

    fun play(groupId: Int, index: Int) {
        val group = library.value?.groups?.firstOrNull { it.id == groupId } ?: return
        viewModelScope.launch {
            if (!graph.playback.play(group, index)) return@launch
            _screen.value = Screen.Player
            // A new listen spends its gems at the count (SPEC §16.2), booked by the heartbeat
            // after it: refresh then, so the balance (and its "💎 −2") updates without waiting.
            val item = group.items.getOrNull(index)
            if (_gems.value.used && group.status.gemCost != null && item?.paidToday == false) {
                delay(GEM_REFRESH_MS)
                refresh()
            }
        }
    }

    /** A tile that is locked shows its reason straight away (SPEC §8.1). */
    fun tapGroup(groupId: Int) {
        val group = library.value?.groups?.firstOrNull { it.id == groupId } ?: return
        val reason = group.status.reason
        if (!group.status.allowed && reason != "item_limit") {
            graph.playback.showBlock(Block(reason ?: "not_allowed", group.status.params))
        } else {
            openGroup(groupId)
        }
    }

    fun dismissBlock() {
        graph.playback.dismissBlock()
        _screen.value = Screen.Home
        viewModelScope.launch { refresh() }
    }

    // ---- parent gate (SPEC §4.3, §8.5) ----

    fun openParentGate() {
        _pinError.value = null
        _screen.value = Screen.ParentGate
    }

    fun checkPin(pin: String) {
        if (!attempts.allowed()) {
            _pinError.value = PinError.Locked((attempts.lockedForMs() + 999) / 1000)
            return
        }
        viewModelScope.launch {
            val ok = withContext(Dispatchers.Default) { Pin.verify(pin, pinHash.value) }
            attempts.record(ok)
            if (ok) {
                _pinError.value = null
                _screen.value = Screen.Settings
                loadDevice()
            } else {
                val locked = attempts.lockedForMs()
                _pinError.value = if (locked > 0) PinError.Locked((locked + 999) / 1000) else PinError.Wrong
            }
        }
    }

    /** First run (§8.6) and "Change PIN": the same 4 digits twice. */
    fun setPin(first: String, second: String) {
        if (first != second) {
            _pinError.value = PinError.Mismatch
            return
        }
        viewModelScope.launch {
            graph.prefs.savePin(first)
            _pinError.value = null
            if (_screen.value == Screen.Settings) return@launch
            _screen.value = Screen.Home
        }
    }

    fun clearPinError() {
        _pinError.value = null
    }

    private fun loadDevice() {
        viewModelScope.launch {
            _device.value = runCatching { graph.api.me() }.getOrNull()
        }
    }

    /** Settings → "Pair again": forget the token; the setup screen follows. */
    fun unpair() {
        graph.playback.stop()
        _screen.value = Screen.Home
        _library.value = null
        _activities.value = null
        _rewards.value = null
        viewModelScope.launch { graph.prefs.clear() }
    }

    fun checkForUpdate() {
        _update.value = UpdateState.Checking
        viewModelScope.launch {
            _update.value =
                try {
                    val release = graph.updater.latest()
                    if (release != null && Updater.isNewer(release, BuildConfig.VERSION_CODE)) {
                        UpdateState.Available(release)
                    } else {
                        UpdateState.UpToDate
                    }
                } catch (e: IOException) {
                    UpdateState.Failed("offline")
                }
        }
    }

    fun installUpdate(release: AppRelease) {
        _update.value = UpdateState.Installing
        viewModelScope.launch {
            _update.value =
                try {
                    graph.updater.install(release)
                    UpdateState.Idle // Android shows its own confirmation from here
                } catch (e: UpdateException) {
                    UpdateState.Failed(e.reason)
                } catch (e: IOException) {
                    UpdateState.Failed("offline")
                }
        }
    }

    fun closeSettings() {
        _update.value = UpdateState.Idle
        _screen.value = Screen.Home
    }

    fun togglePlay() = graph.playback.togglePlay()

    fun seekBy(ms: Long) = graph.playback.seekBy(ms)

    fun seekTo(ms: Long) = graph.playback.seekTo(ms)

    companion object {
        /** App-side reason for the reason screen; the server's 409 code for the same (§14.4a). */
        const val IDEAS_CLOSED = "activities_closed"

        /** How long "Right!" shows before the next problem (SPEC §15.2). */
        private const val RIGHT_MS = 1200L

        /** After a paid start: past the heartbeat that books the count (15 s, SPEC §3.3). */
        private const val GEM_REFRESH_MS = 20_000L
    }
}
