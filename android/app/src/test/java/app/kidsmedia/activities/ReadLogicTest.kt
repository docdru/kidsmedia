package app.kidsmedia.activities

import app.kidsmedia.data.ReadQuestion
import org.junit.Assert.assertEquals
import org.junit.Test

class ReadLogicTest {
    private val q1 = ReadQuestion("Who finds the note?", listOf("Mira", "Pip", "Nobody"), 1)
    private val q2 = ReadQuestion("Where is it?", listOf("In a bottle", "In a tree", "In a shoe"), 0)
    private val questions = listOf(q1, q2)

    @Test
    fun allRightFirstTimeIsOneAttempt() {
        var p = ReadLogic.start()
        assertEquals(ReadProgress(ReadStep.TEXT, 0, 0), p)
        p = ReadLogic.readIt(p, questions)
        assertEquals(ReadProgress(ReadStep.QUESTION, 1, 0), p)
        p = ReadLogic.answer(p, questions, 1)
        assertEquals(ReadProgress(ReadStep.QUESTION, 1, 1), p)
        p = ReadLogic.answer(p, questions, 0)
        assertEquals(ReadProgress(ReadStep.DONE, 1, 1), p)
    }

    @Test
    fun aWrongAnswerGoesBackToTheTextAndKeepsTheAttempts() {
        var p = ReadLogic.readIt(ReadLogic.start(), questions)
        p = ReadLogic.answer(p, questions, 1)
        p = ReadLogic.answer(p, questions, 2) // wrong on the second question
        assertEquals(ReadStep.WRONG, p.step)
        p = ReadLogic.again(p)
        assertEquals(ReadProgress(ReadStep.TEXT, 1, 0), p)
        // The second pass starts at the first question again and counts as attempt 2.
        p = ReadLogic.readIt(p, questions)
        assertEquals(ReadProgress(ReadStep.QUESTION, 2, 0), p)
        p = ReadLogic.answer(ReadLogic.answer(p, questions, 1), questions, 0)
        assertEquals(ReadProgress(ReadStep.DONE, 2, 1), p)
    }

    @Test
    fun answersOutsideTheQuestionStepAreIgnored() {
        val text = ReadLogic.start()
        assertEquals(text, ReadLogic.answer(text, questions, 1))
        val wrong = ReadProgress(ReadStep.WRONG, 1, 0)
        assertEquals(wrong, ReadLogic.answer(wrong, questions, 1))
    }

    @Test
    fun withoutUsableQuestionsReadingIsEnough() {
        assertEquals(ReadProgress(ReadStep.DONE, 1, 0), ReadLogic.readIt(ReadLogic.start(), emptyList()))
        val broken = listOf(
            ReadQuestion("No answers", emptyList(), 0),
            ReadQuestion("Bad index", listOf("a", "b", "c"), 3),
            ReadQuestion("", listOf("a", "b", "c"), 0)
        )
        assertEquals(emptyList<ReadQuestion>(), ReadLogic.usable(broken))
        assertEquals(ReadStep.DONE, ReadLogic.readIt(ReadLogic.start(), broken).step)
    }

    @Test
    fun brokenQuestionsAreSkipped() {
        val mixed = listOf(ReadQuestion("Bad index", listOf("a", "b", "c"), -1), q2)
        var p = ReadLogic.readIt(ReadLogic.start(), mixed)
        p = ReadLogic.answer(p, mixed, 0) // q2's right answer
        assertEquals(ReadProgress(ReadStep.DONE, 1, 0), p)
    }
}
