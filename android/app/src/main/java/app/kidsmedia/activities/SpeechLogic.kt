package app.kidsmedia.activities

import app.kidsmedia.data.Activities
import app.kidsmedia.data.CalcProblem
import app.kidsmedia.data.ChapterTexts
import app.kidsmedia.data.ReadQuestion
import app.kidsmedia.data.StoryTask
import app.kidsmedia.data.StoryWorld
import app.kidsmedia.data.TaskTexts
import java.io.File

/** One piece of what Listen reads: a shown text and its server-rendered speech, if any (SPEC §14.3). */
data class Clip(val text: String, val audio: String? = null)

/** Where a [Clip] is played from. */
sealed interface ClipSource {
    /** A copy kept for offline use. */
    data class Cached(val file: File) : ClipSource

    /** The server's file, with the device token. */
    data class Remote(val url: String) : ClipSource

    /** No rendered audio: the tablet's TextToSpeech. */
    data class Speech(val text: String) : ClipSource
}

/**
 * Read aloud on the server (SPEC §14.3): which clips Listen plays, where each is played from,
 * and which files are kept for offline use. Pure, so it is tested on the JVM.
 */
object SpeechLogic {
    private val PATH = Regex("^/api/speech/([0-9a-f]{8,64})\\.m4a$")

    /** The content key of a speech path, or null if it isn't one; it names the cached file. */
    fun keyOf(path: String?): String? = path?.let { PATH.matchEntire(it)?.groupValues?.get(1) }

    fun fileName(key: String) = "$key.m4a"

    /** Cache first, then the server, then TextToSpeech. */
    fun source(clip: Clip, base: String?, cached: (String) -> File?): ClipSource {
        val key = keyOf(clip.audio) ?: return ClipSource.Speech(clip.text)
        cached(key)?.let { return ClipSource.Cached(it) }
        return if (base == null) ClipSource.Speech(clip.text) else ClipSource.Remote(base + clip.audio)
    }

    private fun clips(vararg pairs: Pair<String, String?>): List<Clip> =
        pairs.filter { it.first.isNotBlank() }.map { (text, audio) -> Clip(text, audio) }

    /** The story card: chapter title, then intro. */
    fun chapterClips(texts: ChapterTexts): List<Clip> =
        clips(texts.title to texts.audio["title"], texts.intro to texts.audio["intro"])

    /** A draw/calc/write task: title, then the text of the child's level. */
    fun taskClips(texts: TaskTexts): List<Clip> =
        clips(texts.title to texts.audio["title"], texts.text to texts.audio["text"])

    /** A `read` task's text page: the title only, never the reading text (SPEC §14.2). */
    fun readTitleClips(texts: TaskTexts): List<Clip> = clips(texts.title to texts.audio["title"])

    /** A question, then its answers in order. */
    fun questionClips(question: ReadQuestion): List<Clip> = clips(
        question.question to question.audio?.question,
        *question.answers.mapIndexed { i, a -> a to question.audio?.answers?.getOrNull(i) }.toTypedArray()
    )

    /** A maths problem's story (SPEC §15.2). */
    fun problemClips(problem: CalcProblem): List<Clip> = clips(problem.text to problem.audio?.text)

    /** A maths problem's hint, shown after the second wrong answer. */
    fun hintClips(problem: CalcProblem): List<Clip> = clips(problem.hint to problem.audio?.hint)

    /** Everything Listen can play for [task]. */
    fun listenClips(task: StoryTask, texts: TaskTexts): List<Clip> = when (task.kind) {
        "read" -> readTitleClips(texts) + ReadLogic.usable(texts.questions).flatMap(::questionClips)
        "calc" -> taskClips(texts) + CalcLogic.usable(texts.problems).flatMap { problemClips(it) + hintClips(it) }
        else -> taskClips(texts)
    }

    /**
     * Speech paths to keep for offline use (SPEC §14.3): what Listen plays in the chapter the
     * story opens on and the next one, in the texts shown: the story's language (SPEC §14.7),
     * [language] (the app's) only when the server doesn't send one.
     */
    fun prefetch(world: StoryWorld, language: String): List<String> {
        val storyLanguage = ActivityLogic.storyLanguage(world, language)
        val clips = ActivityLogic.ahead(world).flatMap { c ->
            val chapter = ActivityLogic.pick(c.texts, storyLanguage)?.second?.let(::chapterClips).orEmpty()
            val tasks = c.tasks.flatMap { t ->
                ActivityLogic.pick(t.texts, storyLanguage)?.let { (_, texts) -> listenClips(t, texts) }.orEmpty()
            }
            chapter + tasks
        }
        return clips.mapNotNull { it.audio }.filter { keyOf(it) != null }.distinct()
    }

    /** Keys of every speech file the activities mention, in any language. */
    fun referencedKeys(data: Activities): Set<String> {
        val world = data.world ?: return emptySet()
        val questions = { q: ReadQuestion -> listOf(q.audio?.question) + q.audio?.answers.orEmpty() }
        val problems = { p: CalcProblem -> listOf(p.audio?.text, p.audio?.hint) }
        val tasks = { t: StoryTask ->
            t.texts.values.flatMap { it.audio.values + it.questions.flatMap(questions) + it.problems.flatMap(problems) }
        }
        val chapters = world.chapters.flatMap { c -> c.texts.values.flatMap { it.audio.values } }
        val allTasks = world.chapters.flatMap { it.tasks }.flatMap(tasks)
        val paths = world.texts.values.flatMap { it.audio.values } + chapters + allTasks
        return paths.mapNotNull(::keyOf).toSet()
    }

    /** Is the cached file [name] no longer needed? Unfinished downloads and stray files are too. */
    fun stale(name: String, referenced: Set<String>): Boolean =
        name.removeSuffix(".m4a").let { key -> name == key || key !in referenced }
}
