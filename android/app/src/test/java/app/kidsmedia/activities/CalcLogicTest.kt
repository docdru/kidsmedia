package app.kidsmedia.activities

import app.kidsmedia.data.CalcProblem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CalcLogicTest {
    private val p1 = CalcProblem("Mira has 3 shells and finds 4 more. How many now?", 7, "Draw 3 shells, then 4 more.")
    private val p2 = CalcProblem("Pip eats 2 of 9 fish. How many are left?", 7, "Draw 9 fish and cross out 2.")
    private val p3 = CalcProblem("Double 12.", 24, "12 + 12.")
    private val problems = listOf(p1, p2, p3)

    private fun type(progress: CalcProgress, value: String): CalcProgress =
        value.fold(progress) { p, c -> CalcLogic.digit(p, c.digitToInt()) }

    private fun answer(progress: CalcProgress, value: String): CalcProgress =
        CalcLogic.check(type(progress, value), problems)

    private fun begun() = CalcLogic.begin(CalcLogic.start(), problems)

    @Test
    fun allRightFirstTimeIsOneAttempt() {
        var p = CalcLogic.start()
        assertEquals(CalcStep.INTRO, p.step)
        p = CalcLogic.begin(p, problems)
        assertEquals(CalcProgress(CalcStep.PROBLEM), p)
        p = answer(p, "7")
        assertEquals(CalcStep.RIGHT, p.step)
        p = CalcLogic.next(p)
        assertEquals(CalcProgress(CalcStep.PROBLEM, problem = 1), p)
        p = CalcLogic.next(answer(p, "7"))
        p = answer(p, "24")
        assertEquals(CalcStep.DONE, p.step)
        assertEquals(1, p.attempts)
    }

    @Test
    fun aWrongAnswerClearsTheBoxAndCounts() {
        val p = answer(begun(), "8")
        assertEquals(CalcStep.PROBLEM, p.step)
        assertEquals("", p.entry)
        assertTrue(p.tryAgain)
        assertEquals(1, p.wrongHere)
        assertEquals(2, p.attempts)
        // Typing again takes "Not quite — try again" away.
        assertFalse(CalcLogic.digit(p, 7).tryAgain)
    }

    @Test
    fun theHintShowsAfterTheSecondWrongAnswerUntilSolved() {
        var p = answer(begun(), "1")
        assertFalse(p.showHint)
        p = answer(p, "2")
        assertTrue(p.showHint)
        p = answer(p, "3")
        assertTrue(p.showHint)
        p = answer(p, "7")
        assertEquals(CalcStep.RIGHT, p.step)
        assertTrue(p.showHint) // still shown with "Right!"
        p = CalcLogic.next(p)
        assertFalse(p.showHint) // the next problem starts without
        assertEquals(0, p.wrongHere)
        assertEquals(3, p.wrong)
    }

    @Test
    fun attemptsStopAtTheServersLimit() {
        // Mashing ✓ must not make a done request the server refuses (422) forever.
        assertEquals(CalcLogic.MAX_ATTEMPTS, CalcProgress(wrong = 80).attempts)
        assertEquals(50, CalcProgress(wrong = 49).attempts)
    }

    @Test
    fun attemptsAreOnePlusTheWrongAnswersOfTheWholeSet() {
        var p = answer(begun(), "6") // 1 wrong on problem 1
        p = CalcLogic.next(answer(p, "7"))
        p = answer(p, "11") // 2 wrong on problem 2
        p = answer(p, "5")
        p = CalcLogic.next(answer(p, "7"))
        p = answer(p, "24")
        assertEquals(CalcStep.DONE, p.step)
        assertEquals(4, p.attempts)
    }

    @Test
    fun atMostThreeDigitsAndNoLeadingZero() {
        var p = type(begun(), "1234")
        assertEquals("123", p.entry)
        p = CalcLogic.delete(p)
        assertEquals("12", p.entry)
        p = CalcLogic.delete(CalcLogic.delete(CalcLogic.delete(p)))
        assertEquals("", p.entry)
        assertEquals("0", type(p, "0").entry)
        assertEquals("7", type(p, "07").entry)
        assertEquals("70", type(p, "70").entry)
        assertEquals(p, CalcLogic.digit(p, 10))
        assertEquals(p, CalcLogic.digit(p, -1))
    }

    @Test
    fun checkingAnEmptyBoxDoesNothing() {
        val p = begun()
        assertEquals(p, CalcLogic.check(p, problems))
    }

    @Test
    fun keysOutsideAProblemAreIgnored() {
        val intro = CalcLogic.start()
        assertEquals(intro, CalcLogic.digit(intro, 3))
        assertEquals(intro, CalcLogic.delete(intro))
        assertEquals(intro, CalcLogic.check(intro, problems))
        val right = answer(begun(), "7")
        assertEquals(right, CalcLogic.digit(right, 3))
        assertEquals(right, CalcLogic.check(right, problems))
        // "Let's start" and "next" only work where they belong.
        assertEquals(right, CalcLogic.begin(right, problems))
        assertEquals(begun(), CalcLogic.next(begun()))
    }

    @Test
    fun brokenProblemsAreSkippedAndNoneMeansDone() {
        val broken = listOf(CalcProblem("", 3, "h"), CalcProblem("Too big", 1000, "h"), CalcProblem("Below", -1, "h"))
        assertEquals(emptyList<CalcProblem>(), CalcLogic.usable(broken))
        assertEquals(CalcStep.DONE, CalcLogic.begin(CalcLogic.start(), broken).step)
        val mixed = broken + p3
        val p = CalcLogic.check(type(CalcLogic.begin(CalcLogic.start(), mixed), "24"), mixed)
        assertEquals(CalcStep.DONE, p.step)
        assertEquals(1, p.attempts)
    }
}
