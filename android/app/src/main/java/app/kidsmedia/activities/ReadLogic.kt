package app.kidsmedia.activities

import app.kidsmedia.data.ReadQuestion

/** Where the child is in a `read` task (SPEC §14.2). */
enum class ReadStep { TEXT, QUESTION, WRONG, DONE }

/**
 * Progress through a `read` task. [attempts] counts the passes through the questions so far,
 * the current one included; [question] is the index of the question being shown.
 */
data class ReadProgress(val step: ReadStep = ReadStep.TEXT, val attempts: Int = 0, val question: Int = 0)

/**
 * The reading flow (SPEC §14.2): text → "I've read it" → the questions one at a time. A wrong
 * answer goes back to the text; all right finishes the task. Pure, so it is tested on the JVM.
 */
object ReadLogic {
    /** Questions the tablet can ask: three answers (at least two) and a valid [ReadQuestion.correct]. */
    fun usable(questions: List<ReadQuestion>): List<ReadQuestion> =
        questions.filter { it.question.isNotBlank() && it.answers.size >= 2 && it.correct in it.answers.indices }

    fun start(): ReadProgress = ReadProgress()

    /** "I've read it": a new pass through the questions, or done at once if there are none. */
    fun readIt(progress: ReadProgress, questions: List<ReadQuestion>): ReadProgress {
        val attempts = progress.attempts + 1
        return if (usable(questions).isEmpty()) {
            ReadProgress(ReadStep.DONE, attempts, 0)
        } else {
            ReadProgress(ReadStep.QUESTION, attempts, 0)
        }
    }

    fun answer(progress: ReadProgress, questions: List<ReadQuestion>, answer: Int): ReadProgress {
        val asked = usable(questions)
        if (progress.step != ReadStep.QUESTION) return progress
        val current = asked.getOrNull(progress.question) ?: return progress.copy(step = ReadStep.DONE)
        return when {
            answer != current.correct -> progress.copy(step = ReadStep.WRONG)
            progress.question + 1 >= asked.size -> progress.copy(step = ReadStep.DONE)
            else -> progress.copy(question = progress.question + 1)
        }
    }

    /** "Read it again" after a wrong answer: back to the text, attempts kept. */
    fun again(progress: ReadProgress): ReadProgress = progress.copy(step = ReadStep.TEXT, question = 0)
}
