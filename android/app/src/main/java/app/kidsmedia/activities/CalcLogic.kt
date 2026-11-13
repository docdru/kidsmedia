package app.kidsmedia.activities

import app.kidsmedia.data.CalcProblem

/** Where the child is in a `calc` task (SPEC §15.2). */
enum class CalcStep {
    /** The introduction with the materials, before "Let's start". */
    INTRO,

    /** A problem is shown and the child types the result. */
    PROBLEM,

    /** The answer was right: a short "Right!", then the next problem. */
    RIGHT,

    DONE
}

/**
 * Progress through a `calc` task's problem set. [problem] is the index of the problem shown,
 * [entry] the digits typed so far, [wrongHere] the wrong answers on this problem (its hint shows
 * from the second), [wrong] the wrong answers in the whole set, and [tryAgain] whether the last
 * answer was wrong ("Not quite — try again", until the child types again).
 */
data class CalcProgress(
    val step: CalcStep = CalcStep.INTRO,
    val problem: Int = 0,
    val entry: String = "",
    val wrongHere: Int = 0,
    val wrong: Int = 0,
    val tryAgain: Boolean = false
) {
    /** What `done` sends (SPEC §15.2): 1 = everything right at the first try; at most the server's 50. */
    val attempts: Int get() = (1 + wrong).coerceAtMost(CalcLogic.MAX_ATTEMPTS)

    /** After the second wrong answer the hint stays until the problem is solved. */
    val showHint: Boolean get() = wrongHere >= CalcLogic.HINT_AFTER
}

/**
 * The maths flow (SPEC §15.2): introduction → "Let's start" → the problems one at a time, typed on
 * the app's number pad. A wrong answer clears the box; there is no skipping and no showing the
 * answer. Pure, so it is tested on the JVM.
 */
object CalcLogic {
    /** Answers are 0–999, so at most three digits. */
    const val MAX_DIGITS = 3

    /** Wrong answers on one problem before its hint shows. */
    const val HINT_AFTER = 2

    /** The server takes `attempts` up to 50 (SPEC §15.2); more would be refused with 422. */
    const val MAX_ATTEMPTS = 50

    /** Problems the tablet can ask: a story and an answer the pad can type. */
    fun usable(problems: List<CalcProblem>): List<CalcProblem> =
        problems.filter { it.text.isNotBlank() && it.answer in 0..999 }

    fun start(): CalcProgress = CalcProgress()

    /** "Let's start": the first problem, or done at once if there are none. */
    fun begin(progress: CalcProgress, problems: List<CalcProblem>): CalcProgress {
        if (progress.step != CalcStep.INTRO) return progress
        return if (usable(problems).isEmpty()) CalcProgress(CalcStep.DONE) else CalcProgress(CalcStep.PROBLEM)
    }

    /** A digit key: appended up to [MAX_DIGITS]; a lone 0 is replaced, so "07" can't be typed. */
    fun digit(progress: CalcProgress, digit: Int): CalcProgress {
        if (progress.step != CalcStep.PROBLEM || digit !in 0..9) return progress
        val entry = when {
            progress.entry == "0" -> "$digit"
            progress.entry.length >= MAX_DIGITS -> return progress
            else -> progress.entry + digit
        }
        return progress.copy(entry = entry, tryAgain = false)
    }

    /** The delete key: the last digit goes. */
    fun delete(progress: CalcProgress): CalcProgress {
        if (progress.step != CalcStep.PROBLEM) return progress
        return progress.copy(entry = progress.entry.dropLast(1))
    }

    /** The ✓ key: right moves on (or finishes after the last), wrong clears the box and counts. */
    fun check(progress: CalcProgress, problems: List<CalcProblem>): CalcProgress {
        if (progress.step != CalcStep.PROBLEM) return progress
        val asked = usable(problems)
        val current = asked.getOrNull(progress.problem) ?: return progress.copy(step = CalcStep.DONE)
        val value = progress.entry.toIntOrNull() ?: return progress
        return when {
            value != current.answer -> progress.copy(
                entry = "",
                wrongHere = progress.wrongHere + 1,
                wrong = progress.wrong + 1,
                tryAgain = true
            )
            progress.problem + 1 >= asked.size -> progress.copy(step = CalcStep.DONE, tryAgain = false)
            else -> progress.copy(step = CalcStep.RIGHT, tryAgain = false)
        }
    }

    /** After the short "Right!": the next problem, with an empty box and its own count of wrong answers. */
    fun next(progress: CalcProgress): CalcProgress {
        if (progress.step != CalcStep.RIGHT) return progress
        return progress.copy(step = CalcStep.PROBLEM, problem = progress.problem + 1, entry = "", wrongHere = 0)
    }
}
