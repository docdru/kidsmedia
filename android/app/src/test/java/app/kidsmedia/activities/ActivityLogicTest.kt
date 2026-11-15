package app.kidsmedia.activities

import app.kidsmedia.data.Activities
import app.kidsmedia.data.ApiJson
import app.kidsmedia.data.CalcProblem
import app.kidsmedia.data.ChapterTexts
import app.kidsmedia.data.DoneRequest
import app.kidsmedia.data.ProblemAudio
import app.kidsmedia.data.ReadQuestion
import app.kidsmedia.data.StoryChapter
import app.kidsmedia.data.StoryTask
import app.kidsmedia.data.StoryWorld
import app.kidsmedia.data.ThumbRef
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import kotlinx.serialization.builtins.ListSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ActivityLogicTest {
    // Sat 7 Nov 2026 (SPEC §13.2): six chapters of four tasks, two ideas open the next chapter.
    private val saturday = LocalDate.of(2026, 11, 7)
    private val serverTime = "2026-11-07T14:20:00+01:00"
    private val tasks = ActivityLogic.KINDS.map { StoryTask(it) }

    private fun world(done: Map<Int, List<String>> = emptyMap(), unlockedUpTo: Int = 1) = StoryWorld(
        id = 1,
        chapters = (1..6).map { p ->
            StoryChapter(
                position = p,
                unlocked = p <= unlockedUpTo,
                doneKinds = done[p].orEmpty(),
                needed = 2,
                tasks = tasks
            )
        }
    )

    private fun data(world: StoryWorld, limit: Int? = null, openedToday: Int = 0) =
        Activities(serverTime = serverTime, world = world, maxNewPerDay = limit, openedToday = openedToday)

    private fun at(date: LocalDate, hour: Int, minute: Int = 0): LocalDateTime = date.atTime(hour, minute)

    private fun unlocked(a: Activities) = a.world!!.chapters.map { it.unlocked }

    // ---- unlocking offline (SPEC §13.2, §13.3) ----

    @Test
    fun aChapterIsFinishedWithEnoughIdeas() {
        val c = StoryChapter(position = 1, needed = 2, tasks = tasks)
        assertFalse(ActivityLogic.finished(c.copy(doneKinds = listOf("draw"))))
        assertTrue(ActivityLogic.finished(c.copy(doneKinds = listOf("draw", "calc"))))
        assertEquals(1, ActivityLogic.left(c.copy(doneKinds = listOf("draw"))))
        // At most all of a chapter's tasks; an older server sends no `needed`: one idea.
        assertEquals(1, ActivityLogic.needed(c.copy(needed = 3, tasks = tasks.take(1))))
        assertEquals(1, ActivityLogic.needed(StoryChapter(position = 1)))
    }

    @Test
    fun theSecondIdeaOpensTheNextChapterOffline() {
        val pending = listOf(DoneRequest(1, 1, "calc"))
        val a = ActivityLogic.applyLocal(data(world(mapOf(1 to listOf("draw")))), pending, at(saturday, 16))
        assertEquals(listOf(true, true, false, false, false, false), unlocked(a))
        // One idea isn't enough.
        val one = ActivityLogic.applyLocal(data(world()), pending, at(saturday, 16))
        assertEquals(listOf(true, false, false, false, false, false), unlocked(one))
    }

    @Test
    fun severalChaptersInOneGoWithoutALimit() {
        val done = mapOf(1 to listOf("draw", "calc"), 2 to listOf("draw"))
        val a = ActivityLogic.applyLocal(data(world(done, 2)), listOf(DoneRequest(1, 2, "read")), at(saturday, 16))
        assertEquals(listOf(true, true, true, false, false, false), unlocked(a))
    }

    @Test
    fun theDailyLimitCountsTheServersOpensOfToday() {
        val done = mapOf(1 to listOf("draw", "calc"), 2 to listOf("draw", "calc"))
        val w = world(done, unlockedUpTo = 2)
        // The server already opened chapter 2 today: with a limit of 1, chapter 3 waits.
        val today = ActivityLogic.applyLocal(data(w, limit = 1, openedToday = 1), emptyList(), at(saturday, 16))
        assertEquals(listOf(true, true, false, false, false, false), unlocked(today))
        assertEquals(ActivityBadge.Tomorrow, ActivityLogic.badge(today.world!!))
        // Sunday, still offline: a new day, chapter 3 opens.
        val sunday = ActivityLogic.applyLocal(
            data(w, limit = 1, openedToday = 1),
            emptyList(),
            at(saturday.plusDays(1), 9)
        )
        assertEquals(listOf(true, true, true, false, false, false), unlocked(sunday))
    }

    @Test
    fun theServersUnlockCountsAndNothingSkipsAChapter() {
        // Chapter 2 isn't finished, so chapter 3 stays locked even though chapter 3 has ideas done.
        val done = mapOf(1 to listOf("draw", "calc"), 2 to listOf("draw"), 3 to listOf("draw", "calc"))
        val a = ActivityLogic.applyLocal(data(world(done, 2)), emptyList(), at(saturday, 16))
        assertEquals(listOf(true, true, false, false, false, false), unlocked(a))
    }

    // ---- badge ----

    @Test
    fun readyMoreToGoAndAllDone() {
        assertEquals(ActivityBadge.Ready(1), ActivityLogic.badge(world()))
        assertEquals(ActivityBadge.MoreToGo(1, 1, false), ActivityLogic.badge(world(mapOf(1 to listOf("draw")))))
        val five = (1..5).associateWith { listOf("draw", "calc") }
        assertEquals(ActivityBadge.Ready(6), ActivityLogic.badge(world(five, 6)))
        assertEquals(ActivityBadge.MoreToGo(6, 1, true), ActivityLogic.badge(world(five + (6 to listOf("read")), 6)))
        val all = (1..6).associateWith { listOf("draw", "calc") }
        assertEquals(ActivityBadge.AllDone, ActivityLogic.badge(world(all, 6)))
        assertEquals(ActivityBadge.Waiting, ActivityLogic.badge(StoryWorld(id = 2)))
    }

    // ---- language ----

    @Test
    fun picksTheAppLanguageAndFallsBack() {
        val both = mapOf("de" to ChapterTexts(title = "Der Sturm"), "en" to ChapterTexts(title = "The storm"))
        assertEquals("de" to "Der Sturm", ActivityLogic.pick(both, "de")?.let { it.first to it.second.title })
        assertEquals("en" to "The storm", ActivityLogic.pick(both, "en")?.let { it.first to it.second.title })
        // Any other language reads English.
        assertEquals("en", ActivityLogic.pick(both, "fr")?.first)
        val germanOnly = mapOf("de" to ChapterTexts(title = "Der Sturm"))
        assertEquals("de", ActivityLogic.pick(germanOnly, "en")?.first)
        assertNull(ActivityLogic.pick(emptyMap<String, ChapterTexts>(), "de"))
    }

    @Test
    fun theStoryIsToldInTheLanguageTheParentChose() {
        // SPEC §14.7: world.lang wins over the app's language; without it (an older server)
        // the app's language is used, and an unknown value is ignored.
        val greek = StoryWorld(id = 1, lang = "el")
        assertEquals("el", ActivityLogic.storyLanguage(greek, "de"))
        val picked = ActivityLogic.pick(storm, ActivityLogic.storyLanguage(greek, "de"))
        assertEquals("el" to "Η καταιγίδα", picked?.let { it.first to it.second.title })
        assertEquals("de", ActivityLogic.storyLanguage(StoryWorld(id = 1), "de"))
        assertEquals("de", ActivityLogic.storyLanguage(StoryWorld(id = 1, lang = "fr"), "de"))
        val decoded = ApiJson.decodeFromString(StoryWorld.serializer(), """{"id": 3, "lang": "el"}""")
        assertEquals("el", decoded.lang)
    }

    @Test
    fun greekIsAnAppLanguage() {
        assertEquals("el" to "Η καταιγίδα", ActivityLogic.pick(storm, "el")?.let { it.first to it.second.title })
        assertEquals("de", ActivityLogic.pick(storm, "de")?.first)
        assertEquals("en", ActivityLogic.pick(storm, "en")?.first)
        assertEquals("en", ActivityLogic.pick(storm, "fr")?.first)
    }

    @Test
    fun missingTextsFallBackToEnglishThenGermanThenGreek() {
        // SPEC §13.1: the wanted language, then English, then German, then Greek.
        assertEquals("en", ActivityLogic.pick(storm - "el", "el")?.first)
        assertEquals("de", ActivityLogic.pick(storm - "en", "fr")?.first)
        assertEquals("de", ActivityLogic.pick(storm - "en", "en")?.first)
        assertEquals("en", ActivityLogic.pick(storm - "de", "de")?.first)
        val greekOnly = storm - "de" - "en"
        assertEquals("el", ActivityLogic.pick(greekOnly, "de")?.first)
        assertEquals("el", ActivityLogic.pick(greekOnly, "en")?.first)
        assertEquals("el", ActivityLogic.pick(greekOnly, "fr")?.first)
    }

    // Greek first, so a test can't pass by just taking the first entry.
    private val storm =
        mapOf(
            "el" to ChapterTexts(title = "Η καταιγίδα"),
            "de" to ChapterTexts(title = "Der Sturm"),
            "en" to ChapterTexts(title = "The storm")
        )

    // ---- offline "done" queue ----

    @Test
    fun pendingTapsAreQueuedOnce() {
        val a = DoneRequest(1, 3, "draw")
        val queue = ActivityLogic.mergePending(ActivityLogic.mergePending(emptyList(), a), a)
        assertEquals(listOf(a), queue)
        assertEquals(2, ActivityLogic.mergePending(queue, DoneRequest(1, 3, "write")).size)
    }

    @Test
    fun aReadTapIsQueuedWithItsAttemptsAndOnlyOnce() {
        val first = DoneRequest(1, 3, "read", attempts = 2)
        val queue = ActivityLogic.mergePending(listOf(first), DoneRequest(1, 3, "read", attempts = 1))
        assertEquals(listOf(first), queue)
        // The queue file keeps the attempts; taps of other kinds leave them out (null).
        val serializer = ListSerializer(DoneRequest.serializer())
        val json = ApiJson.encodeToString(serializer, queue + DoneRequest(1, 3, "draw"))
        val expected =
            """[{"world_id":1,"position":3,"kind":"read","attempts":2},""" +
                """{"world_id":1,"position":3,"kind":"draw"}]"""
        assertEquals(expected, json)
        assertEquals(queue + DoneRequest(1, 3, "draw"), ApiJson.decodeFromString(serializer, json))
    }

    @Test
    fun aQueuedTapKeepsTheTimeOfTheTap() {
        // 18:40 in Berlin; the tablet may be set to any zone (SPEC §14.4a).
        val at = LocalDateTime.of(2026, 11, 3, 18, 40, 12, 345_000_000).atZone(ZoneId.of("Europe/Berlin"))
        assertEquals("2026-11-03T18:40:12+01:00", ActivityLogic.doneAt(at))
        val first = DoneRequest(1, 3, "draw", doneAt = ActivityLogic.doneAt(at))
        // A second tap for the same task keeps the first one's time.
        val queue = ActivityLogic.mergePending(
            listOf(first),
            DoneRequest(1, 3, "draw", doneAt = "2026-11-03T19:30:00+01:00")
        )
        assertEquals(listOf(first), queue)
        val serializer = ListSerializer(DoneRequest.serializer())
        val json = ApiJson.encodeToString(serializer, queue)
        assertEquals("""[{"world_id":1,"position":3,"kind":"draw","done_at":"2026-11-03T18:40:12+01:00"}]""", json)
        assertEquals(queue, ApiJson.decodeFromString(serializer, json))
    }

    @Test
    fun aQueueFromAnOlderAppStaysValidWithoutTimes() {
        val old =
            """[{"world_id":1,"position":3,"kind":"read","attempts":2},""" +
                """{"world_id":1,"position":4,"kind":"draw"}]"""
        val queue = ApiJson.decodeFromString(ListSerializer(DoneRequest.serializer()), old)
        assertEquals(listOf(DoneRequest(1, 3, "read", attempts = 2), DoneRequest(1, 4, "draw")), queue)
        assertTrue(queue.all { it.doneAt == null })
        // Written back unchanged: no done_at appears.
        assertEquals(old, ApiJson.encodeToString(ListSerializer(DoneRequest.serializer()), queue))
    }

    @Test
    fun pendingTapsShowAsDoneAndOpenTheNextChapter() {
        val data = data(world(done = mapOf(1 to listOf("draw"))))
        val pending = listOf(DoneRequest(1, 1, "calc"), DoneRequest(1, 1, "calc"), DoneRequest(99, 2, "draw"))
        val view = ActivityLogic.view(data, pending, at(saturday, 16))
        val chapters = view.world!!.chapters
        assertEquals(listOf("draw", "calc"), chapters[0].doneKinds)
        assertTrue(chapters[1].doneKinds.isEmpty()) // another world's tap doesn't count
        assertEquals(listOf(true, true, false, false, false, false), chapters.map { it.unlocked })
        assertEquals(ActivityBadge.Ready(2), view.badge)
    }

    @Test
    fun theStoryOpensOnTheChapterToDoNow() {
        val done = mapOf(1 to listOf("draw", "calc"), 2 to listOf("draw"))
        assertEquals(2, ActivityLogic.firstOpen(world(done, unlockedUpTo = 2)))
        // Everything open is finished: the last open chapter.
        val finished = mapOf(1 to listOf("draw", "calc"), 2 to listOf("draw", "calc"))
        assertEquals(2, ActivityLogic.firstOpen(world(finished, unlockedUpTo = 2)))
    }

    // ---- pictures (SPEC §13.5) ----

    private val cover = ThumbRef("/api/thumbs/w1-ab12cd.webp")
    private val pic3 = ThumbRef("/api/thumbs/w1c3-9e4f20.webp")

    @Test
    fun pictureFallsBackFromChapterToCoverToNone() {
        val chapter = StoryChapter(position = 3, image = pic3)
        val bare = StoryChapter(position = 4)
        assertEquals(pic3, ActivityLogic.picture(StoryWorld(id = 1, cover = cover), chapter))
        assertEquals(pic3, ActivityLogic.picture(StoryWorld(id = 1), chapter))
        assertEquals(cover, ActivityLogic.picture(StoryWorld(id = 1, cover = cover), bare))
        assertNull(ActivityLogic.picture(StoryWorld(id = 1), bare))
    }

    @Test
    fun prefetchesTheCoverAndTheCurrentAndNextChapter() {
        // Chapters 1–2 finished, 3 open: the story opens on 3, so 3 and 4 are fetched ahead.
        val finished = listOf("draw", "calc")
        val base = world(done = mapOf(1 to finished, 2 to finished), unlockedUpTo = 3)
        val chapters = base.chapters.map { it.copy(image = ThumbRef("/api/thumbs/w1c${it.position}-x.webp")) }
        val w = base.copy(cover = cover, chapters = chapters)
        assertEquals(
            listOf(cover, chapters[2].image, chapters[3].image),
            ActivityLogic.prefetchPictures(w)
        )
        // Without pictures only the cover remains, once.
        assertEquals(listOf(cover), ActivityLogic.prefetchPictures(base.copy(cover = cover)))
        assertTrue(ActivityLogic.prefetchPictures(base).isEmpty())
    }

    // ---- wire format ----

    @Test
    fun parsesTheServerAnswerAndToleratesMissingFields() {
        val json =
            """
            {"server_time":"2026-11-05T14:20:00+01:00","unlock_time":"00:00","today_is_activity_day":true,
             "next_unlock_at":null,"max_new_per_day":1,"opened_today":1,"story_done":false,"extra":"ignored",
             "world":{"id":1,"slug":"lighthouse-island","color":"#B84A12","cover":null,
               "texts":{"de":{"name":"Leuchtturm-Insel","guide":"mit Mira"}},
               "chapters":[{"position":1,"date":"2026-11-02","unlocked":true,"done_kinds":["draw"],"needed":2,
                 "image":{"url":"/api/thumbs/w1c1-9e4f20.webp","aspect":1.0},
                 "texts":{"en":{"title":"Mira's lighthouse","intro":"Hi","outro":"Bye"}},
                 "tasks":[{"kind":"draw","minutes":15,
                   "texts":{"en":{"title":"Draw","text":"Draw it","materials":["Paper"]}}}]}]}}
            """.trimIndent()
        val a = ApiJson.decodeFromString(Activities.serializer(), json)
        assertEquals(1, a.maxNewPerDay)
        assertEquals(1, a.openedToday)
        assertEquals(2, a.world!!.chapters[0].needed)
        assertEquals("Leuchtturm-Insel", a.world!!.texts["de"]!!.name)
        assertEquals(listOf("Paper"), a.world!!.chapters[0].tasks[0].texts["en"]!!.materials)
        assertEquals(ThumbRef("/api/thumbs/w1c1-9e4f20.webp", 1.0), a.world!!.chapters[0].image)
        // The offline cache writes and reads the same types, so the picture survives it.
        val encoded = ApiJson.encodeToString(Activities.serializer(), a)
        val cached = ApiJson.decodeFromString(Activities.serializer(), encoded)
        assertEquals(a, cached)
        assertNull(cached.world!!.cover)
        assertNull(ApiJson.decodeFromString(Activities.serializer(), """{"world":null}""").world)
        // Servers before v0.14 send no level and no questions.
        assertEquals("normal", a.level)
        assertTrue(a.world!!.chapters[0].tasks[0].texts["en"]!!.questions.isEmpty())
    }

    @Test
    fun parsesTheLevelAndAReadTask() {
        val json =
            """
            {"level":"easier","world":{"id":2,"chapters":[{"position":1,"unlocked":true,
              "tasks":[{"kind":"read","minutes":10,"texts":{"en":{"title":"Pip's note",
                "text":"Pip finds a note.","materials":[],"questions":[{"question":"What does Pip find?",
                  "answers":["A shell","A note","A fish"],"correct":1}]}}}]}]}}
            """.trimIndent()
        val a = ApiJson.decodeFromString(Activities.serializer(), json)
        assertEquals("easier", a.level)
        val task = a.world!!.chapters[0].tasks[0]
        assertEquals("read", task.kind)
        val texts = task.texts["en"]!!
        assertEquals("Pip finds a note.", texts.text)
        val question = ReadQuestion("What does Pip find?", listOf("A shell", "A note", "A fish"), 1)
        assertEquals(listOf(question), texts.questions)
        val cached = ApiJson.encodeToString(Activities.serializer(), a)
        assertEquals(a, ApiJson.decodeFromString(Activities.serializer(), cached))
    }

    @Test
    fun parsesSpeechAudioAndDefaultsToNone() {
        val json =
            """
            {"world":{"id":3,"texts":{"en":{"name":"Isle","guide":"","audio":{"name":"/api/speech/aa11.m4a"}}},
              "chapters":[{"position":1,"texts":{"en":{"title":"T","intro":"I","outro":"O",
                "audio":{"title":"/api/speech/bb22.m4a","intro":"/api/speech/cc33.m4a"}}},
                "tasks":[{"kind":"read","texts":{"en":{"title":"R","text":"Long text",
                  "audio":{"title":"/api/speech/dd44.m4a"},
                  "questions":[{"question":"Q?","answers":["a","b","c"],"correct":2,
                    "audio":{"question":"/api/speech/ee55.m4a","answers":["/api/speech/ff66.m4a",null,
                      "/api/speech/0077.m4a"]}},
                    {"question":"Q2?","answers":["x","y"],"correct":0,"audio":null},
                    {"question":"Q3?","answers":["x","y"],"correct":1}]}}}]}]}}
            """.trimIndent()
        val a = ApiJson.decodeFromString(Activities.serializer(), json)
        val world = a.world!!
        assertEquals(mapOf("name" to "/api/speech/aa11.m4a"), world.texts["en"]!!.audio)
        val chapter = world.chapters[0].texts["en"]!!
        assertEquals("/api/speech/cc33.m4a", chapter.audio["intro"])
        assertNull(chapter.audio["outro"])
        val read = world.chapters[0].tasks[0].texts["en"]!!
        assertEquals(mapOf("title" to "/api/speech/dd44.m4a"), read.audio)
        val q = read.questions[0].audio!!
        assertEquals("/api/speech/ee55.m4a", q.question)
        assertEquals(listOf("/api/speech/ff66.m4a", null, "/api/speech/0077.m4a"), q.answers)
        // Nothing of a question rendered, or a server before §14.3: no audio.
        assertNull(read.questions[1].audio)
        assertNull(read.questions[2].audio)
        assertTrue(ChapterTexts().audio.isEmpty())
        // The offline cache keeps the audio.
        val cached = ApiJson.encodeToString(Activities.serializer(), a)
        assertEquals(a, ApiJson.decodeFromString(Activities.serializer(), cached))
    }

    @Test
    fun parsesACalcTaskWithProblems() {
        val json =
            """
            {"world":{"id":4,"chapters":[{"position":1,"unlocked":true,
              "tasks":[{"kind":"calc","minutes":15,"texts":{"en":{"title":"Shells","text":"Get paper ready.",
                "materials":["Paper","Pencil"],"audio":{"text":"/api/speech/aa11.m4a"},
                "problems":[{"text":"3 and 4 shells?","answer":7,"hint":"Draw them.",
                    "audio":{"text":"/api/speech/bb22.m4a","hint":null}},
                  {"text":"Double 6?","answer":12,"hint":"6 + 6.","audio":null},
                  {"text":"Half of 10?","answer":5,"hint":"Share them.","future":1}]}}}]}]}}
            """.trimIndent()
        val a = ApiJson.decodeFromString(Activities.serializer(), json)
        val texts = a.world!!.chapters[0].tasks[0].texts["en"]!!
        assertEquals(
            listOf(
                CalcProblem("3 and 4 shells?", 7, "Draw them.", ProblemAudio("/api/speech/bb22.m4a", null)),
                CalcProblem("Double 6?", 12, "6 + 6."),
                CalcProblem("Half of 10?", 5, "Share them.")
            ),
            texts.problems
        )
        // The offline cache keeps the problems, so the set can be done offline.
        val cached = ApiJson.encodeToString(Activities.serializer(), a)
        assertEquals(a, ApiJson.decodeFromString(Activities.serializer(), cached))
    }

    @Test
    fun aCalcTaskFromAnOlderServerHasNoProblems() {
        val json =
            """
            {"world":{"id":4,"chapters":[{"position":1,"tasks":[{"kind":"calc",
              "texts":{"en":{"title":"Shells","text":"How many shells?"}}}]}]}}
            """.trimIndent()
        val a = ApiJson.decodeFromString(Activities.serializer(), json)
        val texts = a.world!!.chapters[0].tasks[0].texts["en"]!!
        assertTrue(texts.problems.isEmpty())
        // Without problems the task keeps the confirm button (SPEC §15.1).
        assertTrue(CalcLogic.usable(texts.problems).isEmpty())
        val cached = ApiJson.encodeToString(Activities.serializer(), a)
        assertEquals(a, ApiJson.decodeFromString(Activities.serializer(), cached))
    }

    @Test
    fun aCalcTapSendsItsAttempts() {
        val json = ApiJson.encodeToString(DoneRequest.serializer(), DoneRequest(1, 2, "calc", attempts = 3))
        assertEquals("""{"world_id":1,"position":2,"kind":"calc","attempts":3}""", json)
    }

    @Test
    fun readIsTheFourthKind() {
        assertEquals(listOf("draw", "calc", "write", "read"), ActivityLogic.KINDS)
    }
}
