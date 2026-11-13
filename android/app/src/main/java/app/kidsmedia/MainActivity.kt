package app.kidsmedia

import android.content.ComponentName
import android.content.Context
import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import app.kidsmedia.activities.CalcStep
import app.kidsmedia.activities.Speaker
import app.kidsmedia.activities.SpeechLogic
import app.kidsmedia.playback.PlaybackService
import app.kidsmedia.ui.AppViewModel
import app.kidsmedia.ui.CalcScreen
import app.kidsmedia.ui.GemsScreen
import app.kidsmedia.ui.Ground
import app.kidsmedia.ui.GroupScreen
import app.kidsmedia.ui.HomeScreen
import app.kidsmedia.ui.KidsTheme
import app.kidsmedia.ui.MiniPlayer
import app.kidsmedia.ui.PhotoScreen
import app.kidsmedia.ui.PinPad
import app.kidsmedia.ui.PlayerScreen
import app.kidsmedia.ui.ReadScreen
import app.kidsmedia.ui.ReasonScreen
import app.kidsmedia.ui.RewardsScreen
import app.kidsmedia.ui.Screen
import app.kidsmedia.ui.SetPinScreen
import app.kidsmedia.ui.SettingsDot
import app.kidsmedia.ui.SettingsScreen
import app.kidsmedia.ui.SetupScreen
import app.kidsmedia.ui.StoryScreen
import app.kidsmedia.ui.TaskDoneScreen
import app.kidsmedia.ui.TaskScreen
import app.kidsmedia.ui.hasCamera
import app.kidsmedia.ui.pinErrorText
import com.google.common.util.concurrent.ListenableFuture

class MainActivity : ComponentActivity() {
    private val graph get() = (application as KidsMediaApp).graph

    private val viewModel: AppViewModel by viewModels {
        viewModelFactory { initializer { AppViewModel(graph) } }
    }

    private var controller: ListenableFuture<MediaController>? = null

    /** Reads activity texts aloud (SPEC §13.7, §14.3); one engine for the activity's lifetime. */
    private lateinit var speaker: Speaker

    /** The interface language chosen in the app, before Android 13 (SPEC §14.7). */
    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(AppLanguage.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        speaker = Speaker(
            this,
            graph.http,
            resolve = { clip -> SpeechLogic.source(clip, graph.prefs.baseUrl, graph.speech::file) },
            beforeSpeaking = graph.playback::pause
        )
        // Full screen: the status bar, the navigation bar and a tablet's taskbar are hidden; a
        // swipe from the edge shows them for a moment. Whatever stays covered (a camera cutout,
        // or bars that can't be hidden) is kept free by the safe-drawing padding.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        hideSystemBars()
        setContent {
            KidsTheme {
                Box(Modifier.fillMaxSize().background(Ground).windowInsetsPadding(WindowInsets.safeDrawing)) {
                    App(viewModel, speaker)
                }
            }
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // Dialogs, the keyboard or the notification shade can bring the bars back.
        if (hasFocus) hideSystemBars()
    }

    private fun hideSystemBars() {
        WindowCompat.getInsetsController(window, window.decorView).apply {
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            hide(WindowInsetsCompat.Type.systemBars())
        }
    }

    override fun onDestroy() {
        speaker.shutdown()
        super.onDestroy()
    }

    @OptIn(UnstableApi::class) // PlaybackService is a Media3 service
    override fun onStart() {
        super.onStart()
        // Binding a controller starts the playback service, which owns the player
        // and the once-a-second rule ticker (SPEC §4.3).
        controller =
            MediaController
                .Builder(this, SessionToken(this, ComponentName(this, PlaybackService::class.java)))
                .buildAsync()
        viewModel.startPolling()
    }

    /** At the parent's volume cap, "louder" does nothing instead of flashing up and back. */
    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_UP && graph.volume.atCap()) {
            graph.volume.enforce()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onStop() {
        speaker.stop()
        viewModel.stopPolling()
        controller?.let { MediaController.releaseFuture(it) }
        controller = null
        super.onStop()
    }
}

@Composable
private fun App(vm: AppViewModel, speaker: Speaker) {
    val pairing by vm.pairing.collectAsStateWithLifecycle()
    val setupError by vm.setupError.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    val library by vm.library.collectAsStateWithLifecycle()
    val gemState by vm.gems.collectAsStateWithLifecycle()
    val offline by vm.offline.collectAsStateWithLifecycle()
    val screen by vm.screen.collectAsStateWithLifecycle()
    val now by vm.nowPlaying.collectAsStateWithLifecycle()
    val block by vm.block.collectAsStateWithLifecycle()
    val player by vm.player.collectAsStateWithLifecycle()

    val loaded by vm.loaded.collectAsStateWithLifecycle()
    val pinHash by vm.pinHash.collectAsStateWithLifecycle()
    val pinError by vm.pinError.collectAsStateWithLifecycle()
    val device by vm.device.collectAsStateWithLifecycle()
    val update by vm.update.collectAsStateWithLifecycle()
    val activities by vm.activities.collectAsStateWithLifecycle()
    val speaking by speaker.speaking.collectAsStateWithLifecycle()
    val rewards by vm.rewards.collectAsStateWithLifecycle()
    val rewardNotice by vm.rewardNotice.collectAsStateWithLifecycle()
    val rewardBusy by vm.rewardBusy.collectAsStateWithLifecycle()
    val reading by vm.reading.collectAsStateWithLifecycle()
    val calc by vm.calc.collectAsStateWithLifecycle()
    var changingPin by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val camera = remember { hasCamera(context) }

    // Reading aloud belongs to one screen (and one step of a read or calc task): leaving it stops the voice.
    LaunchedEffect(screen, reading.step, reading.question, calc.step, calc.problem) { speaker.stop() }

    if (!loaded) return
    val paired = pairing
    if (paired == null) {
        SetupScreen(setupError, busy, vm::pair)
        return
    }
    if (pinHash == null) {
        // First run, after pairing: the parent sets the PIN (SPEC §8.6).
        SetPinScreen(pinError, vm::setPin, onCancel = null)
        return
    }
    if (screen == Screen.ParentGate) {
        BackHandler { vm.back() }
        PinPad(stringResource(R.string.pin_enter_title), pinErrorText(pinError), vm::checkPin, onCancel = vm::back)
        return
    }
    if (screen == Screen.Settings) {
        BackHandler { if (changingPin) changingPin = false else vm.closeSettings() }
        if (changingPin) {
            SetPinScreen(
                pinError,
                onSet = { a, b ->
                    vm.setPin(a, b)
                    if (a == b) changingPin = false
                },
                onCancel = {
                    vm.clearPinError()
                    changingPin = false
                }
            )
        } else {
            SettingsScreen(
                paired.baseUrl,
                device,
                update,
                vm::checkForUpdate,
                vm::installUpdate,
                { changingPin = true },
                vm::unpair,
                vm::closeSettings
            )
        }
        return
    }
    block?.let {
        BackHandler { vm.dismissBlock() }
        ReasonScreen(it, vm::dismissBlock)
        return
    }
    val base = paired.baseUrl
    val groups = library?.groups.orEmpty()
    BackHandler(enabled = screen != Screen.Home) { vm.back() }
    val stars = rewards?.stars
    // Shown while an enabled gem rule exists (SPEC §16.3).
    val gems = gemState.takeIf { it.used }?.balance
    val home: @Composable () -> Unit = {
        HomeScreen(
            base,
            groups,
            offline,
            vm::tapGroup,
            activities,
            vm::openStory,
            stars,
            vm::openRewards,
            gems,
            vm::openGems
        )
    }
    val story = activities
    Box(Modifier.fillMaxSize()) {
        when (val s = screen) {
            Screen.Home -> home()
            is Screen.Story ->
                if (story?.world == null) {
                    home()
                } else {
                    StoryScreen(
                        base,
                        story,
                        s.position,
                        speaking,
                        vm::back,
                        vm::selectChapter,
                        speaker::toggle,
                        { kind -> vm.openTask(s.position, kind) }
                    )
                }
            is Screen.Task ->
                when {
                    story?.world == null -> home()
                    s.kind == "read" -> {
                        ReadScreen(
                            story,
                            s.position,
                            reading,
                            speaking,
                            vm::back,
                            speaker::toggle,
                            onReadIt = { questions -> vm.readIt(s.position, questions) },
                            onAnswer = { questions, answer -> vm.answerRead(s.position, questions, answer) },
                            onAgain = vm::readAgain
                        )
                    }
                    s.kind == "calc" && calc.step != CalcStep.INTRO -> {
                        CalcScreen(
                            story,
                            s.position,
                            calc,
                            speaking,
                            vm::back,
                            speaker::toggle,
                            onDigit = vm::calcDigit,
                            onDelete = vm::calcDelete,
                            onCheck = { problems -> vm.calcCheck(s.position, problems) }
                        )
                    }
                    else -> {
                        TaskScreen(
                            story,
                            s.position,
                            s.kind,
                            speaking,
                            camera,
                            vm::back,
                            speaker::toggle,
                            onPicture = { vm.openPhoto(s.position, s.kind) },
                            onStartCalc = { problems -> vm.startCalc(s.position, problems) },
                            onDone = { vm.finishTask(s.position, s.kind) }
                        )
                    }
                }
            is Screen.Photo ->
                if (story?.world == null) {
                    home()
                } else {
                    PhotoScreen(
                        vm::newShot,
                        vm::back,
                        onSend = { shot -> vm.sendPhoto(s.position, s.kind, shot) },
                        onDiscard = vm::discardShot,
                        onDone = { vm.finishTask(s.position, s.kind) }
                    )
                }
            is Screen.TaskDone ->
                if (story?.world == null) home() else TaskDoneScreen(base, story, s.position, vm::back)
            Screen.Rewards -> {
                val shown = rewards
                if (shown == null) {
                    home()
                } else {
                    RewardsScreen(
                        base,
                        shown,
                        activities,
                        offline,
                        rewardNotice,
                        rewardBusy,
                        vm::back,
                        vm::redeemSwap,
                        vm::requestWish
                    )
                }
            }
            is Screen.Group -> {
                val group = groups.firstOrNull { it.id == s.groupId }
                if (group == null) {
                    home()
                } else {
                    GroupScreen(base, group, vm::back) { index -> vm.play(group.id, index) }
                }
            }
            Screen.Player -> {
                val playing = now
                if (playing == null) {
                    home()
                } else {
                    PlayerScreen(base, playing, player, vm::back, vm::togglePlay, vm::seekBy, vm::seekTo, gems)
                }
            }
            Screen.Gems -> GemsScreen(base, gemState.balance, groups, vm::back, vm::tapGroup)
            Screen.ParentGate, Screen.Settings -> Unit
        }
        if (screen == Screen.Home) {
            SettingsDot(vm::openParentGate, Modifier.align(Alignment.TopEnd).padding(8.dp))
        }
        val playing = now
        // Not over a task's big "I'm done!" button, nor over the camera.
        val task = screen is Screen.Task || screen is Screen.TaskDone || screen is Screen.Photo
        val covers = screen == Screen.Player || task
        if (playing != null && !covers) {
            Box(Modifier.align(Alignment.BottomCenter).padding(horizontal = 40.dp, vertical = 20.dp)) {
                MiniPlayer(base, playing, player?.isPlaying == true, vm::openPlayer, vm::togglePlay)
            }
        }
    }
}
