package app.kidsmedia.activities

import app.kidsmedia.data.Activities
import app.kidsmedia.data.CalcProblem
import app.kidsmedia.data.ChapterTexts
import app.kidsmedia.data.ProblemAudio
import app.kidsmedia.data.QuestionAudio
import app.kidsmedia.data.ReadQuestion
import app.kidsmedia.data.StoryChapter
import app.kidsmedia.data.StoryTask
import app.kidsmedia.data.StoryWorld
import app.kidsmedia.data.TaskTexts
import app.kidsmedia.data.WorldTexts
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SpeechLogicTest {
    private fun path(n: Int) = "/api/speech/${"%020x".format(n)}.m4a"

    private fun key(n: Int) = "%020x".format(n)

    private val base = "https://kids.example"

    // ---- sources: cache → server → TextToSpeech ----

    @Test
    fun aCachedCopyComesFirst() {
        val file = File("/data/speech/${key(1)}.m4a")
        val source = SpeechLogic.source(Clip("Hi", path(1)), base) { if (it == key(1)) file else null }
        assertEquals(ClipSource.Cached(file), source)
    }

    @Test
    fun withoutACopyTheServersFileIsPlayed() {
        val source = SpeechLogic.source(Clip("Hi", path(1)), base) { null }
        assertEquals(ClipSource.Remote("$base${path(1)}"), source)
    }

    @Test
    fun noAudioMeansTextToSpeech() {
        assertEquals(ClipSource.Speech("Hi"), SpeechLogic.source(Clip("Hi"), base) { null })
        // Not paired any more: nothing to fetch from.
        assertEquals(ClipSource.Speech("Hi"), SpeechLogic.source(Clip("Hi", path(1)), null) { null })
    }

    @Test
    fun onlySpeechPathsAreFetchedOrCached() {
        for (bad in listOf("/api/media/1/stream", "/api/speech/../../etc.m4a", "https://evil.net/x.m4a", "")) {
            assertNull(bad, SpeechLogic.keyOf(bad))
            var asked = false
            val source = SpeechLogic.source(Clip("Hi", bad), base) {
                asked = true
                null
            }
            assertEquals(ClipSource.Speech("Hi"), source)
            assertFalse(asked)
        }
        assertEquals(key(7), SpeechLogic.keyOf(path(7)))
    }

    // ---- sequences ----

    @Test
    fun aChapterIsTitleThenIntro() {
        val texts = ChapterTexts("Title", "Intro", "Outro", mapOf("intro" to path(2), "outro" to path(3)))
        assertEquals(listOf(Clip("Title"), Clip("Intro", path(2))), SpeechLogic.chapterClips(texts))
    }

    @Test
    fun aTaskIsTitleThenTheLevelText() {
        val texts = TaskTexts("Draw", "Draw the lighthouse", audio = mapOf("title" to path(1), "text" to path(2)))
        assertEquals(
            listOf(Clip("Draw", path(1)), Clip("Draw the lighthouse", path(2))),
            SpeechLogic.taskClips(texts)
        )
    }

    @Test
    fun blankTextsAreSkipped() {
        assertEquals(listOf(Clip("Intro")), SpeechLogic.chapterClips(ChapterTexts(title = " ", intro = "Intro")))
    }

    @Test
    fun aReadTaskNeverSpeaksTheReadingText() {
        val texts = TaskTexts("Pip's note", "Pip finds a note.", audio = mapOf("title" to path(1), "text" to path(2)))
        assertEquals(listOf(Clip("Pip's note", path(1))), SpeechLogic.readTitleClips(texts))
    }

    @Test
    fun aQuestionIsTheQuestionThenItsAnswers() {
        val q = ReadQuestion(
            "What does Pip find?",
            listOf("A shell", "A note", "A fish"),
            1,
            QuestionAudio(path(1), listOf(path(2), null))
        )
        assertEquals(
            listOf(
                Clip("What does Pip find?", path(1)),
                Clip("A shell", path(2)),
                Clip("A note"),
                Clip("A fish")
            ),
            SpeechLogic.questionClips(q)
        )
        val unrendered = listOf(Clip("What does Pip find?"), Clip("A shell"), Clip("A note"), Clip("A fish"))
        assertEquals(unrendered, SpeechLogic.questionClips(q.copy(audio = null)))
    }

    @Test
    fun aProblemAndItsHintAreSpokenSeparately() {
        val p = CalcProblem("3 and 4 shells?", 7, "Draw them.", ProblemAudio(path(1), null))
        assertEquals(listOf(Clip("3 and 4 shells?", path(1))), SpeechLogic.problemClips(p))
        assertEquals(listOf(Clip("Draw them.")), SpeechLogic.hintClips(p))
        assertEquals(listOf(Clip("3 and 4 shells?")), SpeechLogic.problemClips(p.copy(audio = null)))
    }

    private val calcTask = StoryTask(
        "calc",
        texts = mapOf(
            "en" to TaskTexts(
                "C",
                "Get paper ready",
                audio = mapOf("title" to path(11), "text" to path(12)),
                problems = listOf(
                    CalcProblem("P1", 7, "H1", ProblemAudio(path(13), path(14))),
                    CalcProblem("P2", 5, "H2", ProblemAudio(path(15))),
                    // Not usable: never asked, so not kept.
                    CalcProblem("", 3, "H3", ProblemAudio(path(16), path(17)))
                )
            )
        )
    )

    @Test
    fun aCalcTaskKeepsItsProblemsAndHintsOfTheCurrentAndNextChapter() {
        val chapters = listOf(
            StoryChapter(1, unlocked = true, doneKinds = listOf("calc"), tasks = listOf(calcTask)),
            StoryChapter(2, unlocked = true, tasks = listOf(calcTask)),
            StoryChapter(3, unlocked = false, tasks = listOf(StoryTask("calc")))
        )
        val world = StoryWorld(id = 5, chapters = chapters)
        assertEquals(listOf(11, 12, 13, 14, 15).map(::path), SpeechLogic.prefetch(world, "en"))
        val clips = SpeechLogic.listenClips(calcTask, calcTask.texts["en"]!!)
        assertEquals(Clip("C", path(11)), clips.first())
        assertTrue(Clip("H2") in clips)
        val referenced = SpeechLogic.referencedKeys(Activities(world = world))
        assertTrue(listOf(13, 14, 15, 16, 17).map(::key).all { it in referenced })
    }

    // ---- offline copies ----

    private fun chapter(position: Int, unlocked: Boolean, done: List<String> = emptyList(), offset: Int) = StoryChapter(
        position = position,
        unlocked = unlocked,
        doneKinds = done,
        texts = mapOf(
            "en" to ChapterTexts("T", "I", "O", mapOf("title" to path(offset), "outro" to path(offset + 1))),
            "de" to ChapterTexts("T", "I", audio = mapOf("title" to path(offset + 2)))
        ),
        tasks = listOf(
            StoryTask(
                "draw",
                texts = mapOf("en" to TaskTexts("D", "Draw", audio = mapOf("text" to path(offset + 3))))
            ),
            StoryTask(
                "read",
                texts = mapOf(
                    "en" to TaskTexts(
                        "R",
                        "Reading",
                        questions = listOf(
                            ReadQuestion(
                                "Q",
                                listOf("a", "b"),
                                0,
                                QuestionAudio(path(offset + 4), listOf(path(offset + 5)))
                            )
                        ),
                        audio = mapOf("title" to path(offset + 6), "text" to path(offset + 7))
                    )
                )
            )
        )
    )

    private val world = StoryWorld(
        id = 1,
        texts = mapOf("en" to WorldTexts("W", audio = mapOf("name" to path(999)))),
        chapters = listOf(
            chapter(1, unlocked = true, done = listOf("draw"), offset = 100),
            chapter(2, unlocked = true, offset = 200),
            chapter(3, unlocked = false, offset = 300),
            chapter(4, unlocked = false, offset = 400)
        )
    )

    @Test
    fun theCurrentAndNextChapterAreKeptInTheShownLanguage() {
        // Chapter 2 is the first open one; 3 is next. English texts; what Listen plays only.
        val expected = listOf(200, 203, 206, 204, 205, 300, 303, 306, 304, 305).map(::path)
        assertEquals(expected, SpeechLogic.prefetch(world, "en"))
        // German chapter texts; the tasks have English only, so their English speech.
        val german = listOf(202, 203, 206, 204, 205, 302, 303, 306, 304, 305).map(::path)
        assertEquals(german, SpeechLogic.prefetch(world, "de"))
    }

    @Test
    fun filesTheActivitiesNoLongerMentionArePruned() {
        val referenced = SpeechLogic.referencedKeys(Activities(world = world))
        assertTrue(key(999) in referenced)
        assertTrue(key(101) in referenced) // any field, any language, any chapter
        assertTrue(key(405) in referenced)
        assertFalse(SpeechLogic.stale("${key(203)}.m4a", referenced))
        assertTrue(SpeechLogic.stale("${key(5)}.m4a", referenced))
        assertTrue(SpeechLogic.stale("${key(203)}.part", referenced))
        assertTrue(SpeechLogic.stale("stray", referenced))
        assertTrue(SpeechLogic.referencedKeys(Activities(world = null)).isEmpty())
    }
}
