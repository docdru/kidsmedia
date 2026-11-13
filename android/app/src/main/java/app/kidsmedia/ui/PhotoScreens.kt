package app.kidsmedia.ui

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import android.provider.Settings
import android.util.Log
import android.util.Size
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import app.kidsmedia.R
import app.kidsmedia.activities.PhotoLogic
import java.io.File
import java.util.concurrent.Executor
import java.util.concurrent.Executors
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext

/** Does the tablet have a camera at all (SPEC §15.3)? Without one draw and write keep the confirm button. */
fun hasCamera(context: Context): Boolean = context.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)

private const val CAMERA = Manifest.permission.CAMERA

private fun cameraAllowed(context: Context): Boolean =
    ContextCompat.checkSelfPermission(context, CAMERA) == PackageManager.PERMISSION_GRANTED

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

/**
 * The photo of a drawing or a story (SPEC §15.3): the camera permission (asked the first time),
 * the in-app camera, then "Send this picture?". [newShot] names the file a picture is written to,
 * [onSend] hands it over, [onDiscard] deletes it for "Take again". If the camera can't be used
 * after all, the confirm button of §15.1 calls [onDone].
 */
@Composable
fun PhotoScreen(
    newShot: () -> File,
    onBack: () -> Unit,
    onSend: (File) -> Unit,
    onDiscard: (File) -> Unit,
    onDone: () -> Unit
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var allowed by remember { mutableStateOf(cameraAllowed(context)) }
    var refused by rememberSaveable { mutableStateOf(false) }
    var refusedForGood by rememberSaveable { mutableStateOf(false) }
    var shot by remember { mutableStateOf<File?>(null) }
    var unavailable by remember { mutableStateOf(false) }
    var cameraError by remember { mutableStateOf(false) }
    var cameraTry by remember { mutableIntStateOf(0) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        allowed = granted
        refused = !granted
        // Android won't ask any more once it shows no rationale after a refusal: the settings then.
        val activity = context.findActivity()
        val rationale = activity != null && ActivityCompat.shouldShowRequestPermissionRationale(activity, CAMERA)
        refusedForGood = !granted && activity != null && !rationale
    }
    // Back from the app's settings: a grown-up may have allowed the camera there.
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) allowed = cameraAllowed(context)
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    LaunchedEffect(Unit) {
        if (!allowed) launcher.launch(CAMERA)
    }
    val picture = shot
    when {
        unavailable -> NoCameraPage(onBack, onDone)
        !allowed && refused -> PermissionPage(
            refusedForGood,
            onBack,
            onAsk = { launcher.launch(CAMERA) },
            onSettings = {
                val app = Uri.fromParts("package", context.packageName, null)
                val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, app)
                runCatching { context.startActivity(intent) }
            }
        )
        // Waiting for Android's question.
        !allowed -> Box(Modifier.fillMaxSize().background(Color.Black))
        picture != null -> ReviewPage(
            picture,
            onBack,
            onSend = { onSend(picture) },
            onAgain = {
                onDiscard(picture)
                shot = null
            }
        )
        // A camera that fails to start is not "no camera": try again, never finish without a photo.
        cameraError -> MessagePage(stringResource(R.string.photo_failed), null, onBack) {
            DoneButton(
                {
                    cameraError = false
                    cameraTry++
                },
                label = R.string.photo_retry
            ) { ButtonIcon(Icons.Filled.Refresh) }
        }
        else -> key(cameraTry) {
            CameraPage(
                newShot,
                onBack,
                onShot = { shot = it },
                onUnavailable = { unavailable = true },
                onError = { cameraError = true }
            )
        }
    }
}

// ---- camera -------------------------------------------------------------------------

/** About 3 MP, 4:3: enough for the long edge of 2048 the app sends, without decoding a huge frame. */
private val CaptureSize = Size(2048, 1536)

@Composable
private fun CameraPage(
    newShot: () -> File,
    onBack: () -> Unit,
    onShot: (File) -> Unit,
    onUnavailable: () -> Unit,
    onError: () -> Unit
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val scope = rememberCoroutineScope()
    val previewView = remember {
        PreviewView(context).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
    }
    val imageCapture = remember {
        val strategy = ResolutionStrategy(CaptureSize, ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER)
        val resolution = ResolutionSelector.Builder().setResolutionStrategy(strategy).build()
        ImageCapture
            .Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
            .setResolutionSelector(resolution)
            .build()
    }
    val executor = remember { Executors.newSingleThreadExecutor() }
    var busy by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }

    DisposableEffect(lifecycleOwner) {
        val future = ProcessCameraProvider.getInstance(context)
        var disposed = false
        future.addListener(
            {
                if (!disposed) {
                    try {
                        val provider = future.get()
                        // The back camera; a tablet with only a front camera can still take the picture.
                        val selector = listOf(CameraSelector.DEFAULT_BACK_CAMERA, CameraSelector.DEFAULT_FRONT_CAMERA)
                            .firstOrNull { provider.hasCamera(it) }
                        if (selector == null) {
                            onUnavailable()
                        } else {
                            val preview = Preview.Builder().build()
                            preview.setSurfaceProvider(previewView.surfaceProvider)
                            provider.unbindAll()
                            provider.bindToLifecycle(lifecycleOwner, selector, preview, imageCapture)
                        }
                    } catch (e: Exception) {
                        Log.w("kidsmedia", "camera: $e")
                        onError()
                    }
                }
            },
            ContextCompat.getMainExecutor(context)
        )
        onDispose {
            disposed = true
            if (future.isDone) runCatching { future.get().unbindAll() }
        }
    }
    DisposableEffect(Unit) {
        onDispose { executor.shutdown() }
    }

    BoxWithConstraints(Modifier.fillMaxSize().background(Color.Black)) {
        val landscape = maxWidth > maxHeight
        AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
        Box(Modifier.align(Alignment.TopStart).padding(28.dp)) { BackButton(onBack) }
        if (busy || failed) {
            Text(
                stringResource(if (busy) R.string.photo_saving else R.string.photo_failed),
                fontSize = 26.sp,
                fontWeight = FontWeight.Black,
                color = Color.White,
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = 40.dp)
                    .clip(RoundedCornerShape(24.dp))
                    .background(Color.Black.copy(alpha = 0.6f))
                    .padding(horizontal = 24.dp, vertical = 12.dp)
            )
        }
        val label = stringResource(R.string.photo_shutter)
        Surface(
            onClick = {
                busy = true
                failed = false
                val target = newShot()
                // The picture is turned the way the tablet is held now (target rotation).
                val rotation = previewView.display?.rotation ?: android.view.Surface.ROTATION_0
                scope.launch {
                    val result = runCatching { capture(imageCapture, rotation, target, executor) }
                    busy = false
                    result
                        .onSuccess(onShot)
                        .onFailure {
                            Log.w("kidsmedia", "photo: $it")
                            target.delete()
                            failed = true
                        }
                }
            },
            enabled = !busy,
            shape = CircleShape,
            color = Color.White,
            modifier = Modifier
                .align(if (landscape) Alignment.CenterEnd else Alignment.BottomCenter)
                .padding(40.dp)
                .size(112.dp)
                .border(8.dp, Color.White.copy(alpha = 0.5f), CircleShape)
                .semantics { contentDescription = label }
        ) {
            Box(Modifier.fillMaxSize().padding(14.dp).border(4.dp, Ink, CircleShape))
        }
    }
}

/**
 * Take the picture and write it to [target]: turned upright from the camera's rotation, the long
 * edge at most 2048 px, JPEG quality 85 (SPEC §15.3). The JPEG carries no EXIF data at all.
 */
private suspend fun capture(imageCapture: ImageCapture, rotation: Int, target: File, executor: Executor): File =
    suspendCancellableCoroutine { continuation ->
        imageCapture.targetRotation = rotation
        imageCapture.takePicture(
            executor,
            object : ImageCapture.OnImageCapturedCallback() {
                override fun onCaptureSuccess(image: ImageProxy) {
                    val written = runCatching { image.use { writeScaled(it, target) } }
                    written.fold({ continuation.resume(target) }, { continuation.resumeWithException(it) })
                }

                override fun onError(exception: ImageCaptureException) {
                    continuation.resumeWithException(exception)
                }
            }
        )
    }

private fun writeScaled(image: ImageProxy, target: File) {
    val source = image.toBitmap()
    val (width, height) = PhotoLogic.scaledSize(source.width, source.height)
    val matrix = Matrix().apply {
        postScale(width.toFloat() / source.width, height.toFloat() / source.height)
        postRotate(image.imageInfo.rotationDegrees.toFloat())
    }
    val upright = Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, true)
    try {
        target.outputStream().use { upright.compress(Bitmap.CompressFormat.JPEG, PhotoLogic.JPEG_QUALITY, it) }
    } finally {
        if (upright !== source) upright.recycle()
        source.recycle()
    }
}

// ---- after the shot -----------------------------------------------------------------

@Composable
private fun ReviewPage(shot: File, onBack: () -> Unit, onSend: () -> Unit, onAgain: () -> Unit) {
    var picture by remember(shot) { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(shot) {
        picture = withContext(Dispatchers.IO) { BitmapFactory.decodeFile(shot.path)?.asImageBitmap() }
    }
    Column(
        Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(20.dp)
        ) {
            BackButton(onBack)
            Text(
                stringResource(R.string.photo_send_title),
                fontSize = 40.sp,
                fontWeight = FontWeight.Black,
                color = Ink,
                lineHeight = 48.sp
            )
        }
        Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
            picture?.let {
                Image(
                    it,
                    contentDescription = null,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize().clip(RoundedCornerShape(28.dp))
                )
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(28.dp), verticalAlignment = Alignment.CenterVertically) {
            DoneButton(onAgain, label = R.string.photo_again, color = Color.White, textColor = Ink) {
                ButtonIcon(Icons.Filled.Refresh, tint = Ink)
            }
            DoneButton(onSend, label = R.string.photo_send) { ButtonIcon(Icons.AutoMirrored.Filled.Send) }
        }
    }
}

// ---- no camera ----------------------------------------------------------------------

@Composable
private fun PermissionPage(forGood: Boolean, onBack: () -> Unit, onAsk: () -> Unit, onSettings: () -> Unit) {
    MessagePage(
        stringResource(R.string.photo_permission_title),
        stringResource(R.string.photo_permission_text),
        onBack
    ) {
        if (forGood) {
            DoneButton(onSettings, label = R.string.photo_permission_settings) { ButtonIcon(Icons.Filled.Settings) }
        } else {
            DoneButton(onAsk, label = R.string.photo_permission_ask) { ButtonIcon(Icons.Filled.Refresh) }
        }
    }
}

/** The camera can't be used after all: the confirm button of §15.1. */
@Composable
private fun NoCameraPage(onBack: () -> Unit, onDone: () -> Unit) {
    var confirming by remember { mutableStateOf(false) }
    MessagePage(stringResource(R.string.photo_failed), null, onBack) {
        DoneButton({ confirming = true })
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

@Composable
private fun MessagePage(title: String, text: String?, onBack: () -> Unit, action: @Composable () -> Unit) {
    Column(Modifier.fillMaxSize().background(Ground).padding(horizontal = 40.dp, vertical = 28.dp)) {
        BackButton(onBack)
        Column(
            Modifier.weight(1f).fillMaxWidth().padding(horizontal = 40.dp),
            verticalArrangement = Arrangement.spacedBy(28.dp, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Box(
                Modifier.size(150.dp).clip(CircleShape).background(Color.White),
                contentAlignment = Alignment.Center
            ) { CameraIcon(Ink, Modifier.size(84.dp)) }
            Text(
                title,
                fontSize = 40.sp,
                fontWeight = FontWeight.Black,
                color = Ink,
                lineHeight = 48.sp,
                textAlign = TextAlign.Center
            )
            text?.let {
                Text(
                    it,
                    fontSize = 26.sp,
                    fontWeight = FontWeight.Bold,
                    color = Muted,
                    lineHeight = 36.sp,
                    textAlign = TextAlign.Center
                )
            }
            action()
        }
    }
}
