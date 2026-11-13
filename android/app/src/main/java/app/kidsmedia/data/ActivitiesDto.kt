package app.kidsmedia.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire types of the activities API (SPEC §13.3, §14). Texts are keyed by language ("de", "en", "el").
 * `audio` maps a field name to its server-rendered speech, `/api/speech/<key>.m4a` (§14.3); a field
 * without an entry is read by the tablet's TextToSpeech.
 */

@Serializable
data class WorldTexts(val name: String = "", val guide: String = "", val audio: Map<String, String> = emptyMap())

@Serializable
data class ChapterTexts(
    val title: String = "",
    val intro: String = "",
    val outro: String = "",
    val audio: Map<String, String> = emptyMap()
)

/** Speech of a question and of each of its answers (SPEC §14.3); null where a text isn't rendered yet. */
@Serializable
data class QuestionAudio(val question: String? = null, val answers: List<String?> = emptyList())

/**
 * A multiple-choice question of a `read` task (SPEC §14.2); [correct] is the index into [answers].
 * [audio] is null when nothing of it is rendered.
 */
@Serializable
data class ReadQuestion(
    val question: String = "",
    val answers: List<String> = emptyList(),
    val correct: Int = 0,
    val audio: QuestionAudio? = null
)

/** Speech of a maths problem's story and of its hint (SPEC §15.2); null where a text isn't rendered yet. */
@Serializable
data class ProblemAudio(val text: String? = null, val hint: String? = null)

/**
 * One story problem of a `calc` task (SPEC §15.2): [answer] is an integer 0–999 the tablet checks
 * offline, [hint] says how to work it out (never the answer). [audio] is null on servers before v0.16.
 */
@Serializable
data class CalcProblem(
    val text: String = "",
    val answer: Int = 0,
    val hint: String = "",
    val audio: ProblemAudio? = null
)

/**
 * A task's texts in one language. [text] is already the child's level (SPEC §14.1); for a
 * `read` task it is the reading text and [questions] are the questions for that level (§14.2);
 * for a `calc` task it is the introduction and [problems] are the problems for that level (§15.2).
 * Older servers send neither, so both default to empty.
 */
@Serializable
data class TaskTexts(
    val title: String = "",
    val text: String = "",
    val materials: List<String> = emptyList(),
    val questions: List<ReadQuestion> = emptyList(),
    val audio: Map<String, String> = emptyMap(),
    val problems: List<CalcProblem> = emptyList()
)

@Serializable
data class StoryTask(val kind: String, val minutes: Int = 10, val texts: Map<String, TaskTexts> = emptyMap())

@Serializable
data class StoryChapter(
    val position: Int,
    /** Local date (`YYYY-MM-DD`) the chapter falls on; null past the server's horizon. */
    val date: String? = null,
    val unlocked: Boolean = false,
    @SerialName("done_kinds") val doneKinds: List<String> = emptyList(),
    /** Chapter picture (SPEC §13.5); null falls back to the world cover. */
    val image: ThumbRef? = null,
    val texts: Map<String, ChapterTexts> = emptyMap(),
    val tasks: List<StoryTask> = emptyList()
)

@Serializable
data class StoryWorld(
    val id: Int,
    val slug: String = "",
    @SerialName("age_band") val ageBand: String = "",
    val color: String = "#B84A12",
    /** The language the parent tells this story in (SPEC §14.7); null from an older server. */
    val lang: String? = null,
    val cover: ThumbRef? = null,
    val texts: Map<String, WorldTexts> = emptyMap(),
    val chapters: List<StoryChapter> = emptyList()
)

/** An opening-time window for Ideas (SPEC §14.4a): [days] 0 = Monday ... 6 = Sunday, times `HH:MM`. */
@Serializable
data class OpenWindow(val days: List<Int> = emptyList(), val from: String = "", val to: String = "")

@Serializable
data class Activities(
    @SerialName("server_time") val serverTime: String = "",
    /** The child's level, `easier` | `normal` | `harder` (SPEC §14.1); older servers don't send it. */
    val level: String = "normal",
    @SerialName("unlock_time") val unlockTime: String = "07:00",
    @SerialName("today_is_activity_day") val todayIsActivityDay: Boolean = false,
    @SerialName("next_unlock_at") val nextUnlockAt: String? = null,
    val world: StoryWorld? = null,
    /**
     * Opening times (SPEC §14.4a); empty = always open, which is also what older servers mean.
     * The tablet decides from [windows] with its trusted clock; [openNow] and [opensAt] (local
     * `YYYY-MM-DDTHH:MM[:SS]`) are the server's view at [serverTime].
     */
    val windows: List<OpenWindow> = emptyList(),
    @SerialName("open_now") val openNow: Boolean = true,
    @SerialName("opens_at") val opensAt: String? = null
)

/**
 * "I'm done!" (SPEC §13.3). [attempts] only for `read` and `calc` tasks (§14.2, §15.2). [doneAt] is the
 * trusted-clock time of the tap in the server's time zone, ISO-8601 with offset, so a tap queued offline counts
 * for when it happened (§14.4a); taps queued by older app versions have none. Null is not sent.
 */
@Serializable
data class DoneRequest(
    @SerialName("world_id") val worldId: Int,
    val position: Int,
    val kind: String,
    val attempts: Int? = null,
    @SerialName("done_at") val doneAt: String? = null
)

/**
 * A photo of a drawing or a story the server hasn't stored yet (SPEC §15.3 Offline): [file] is the
 * JPEG's name in the app's photo queue folder, [doneAt] the trusted-clock time it was taken, as
 * for [DoneRequest]. Kept as JSON next to the queued taps.
 */
@Serializable
data class PendingPhoto(
    @SerialName("world_id") val worldId: Int,
    val position: Int,
    val kind: String,
    @SerialName("done_at") val doneAt: String,
    val file: String
) {
    /** How the photo shows until it is sent: as a done tap for its task. */
    fun asDone(): DoneRequest = DoneRequest(worldId, position, kind, doneAt = doneAt)
}
