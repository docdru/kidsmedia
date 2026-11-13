package app.kidsmedia.ui

import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TextsTest {
    private val all =
        mapOf(
            "reason" to JsonPrimitive("item_limit"),
            "message_en" to JsonPrimitive("One story a day!"),
            "message_de" to JsonPrimitive("Eine Geschichte am Tag!"),
            "message_el" to JsonPrimitive("Μία ιστορία τη μέρα!")
        )

    @Test
    fun parentMessageInTheAppLanguage() {
        // SPEC §3.2, decision 15.
        assertEquals("One story a day!", customMessage(all, "en"))
        assertEquals("Eine Geschichte am Tag!", customMessage(all, "de"))
        assertEquals("Μία ιστορία τη μέρα!", customMessage(all, "el"))
        // Any other language reads the English text.
        assertEquals("One story a day!", customMessage(all, "fr"))
    }

    @Test
    fun noParentMessageMeansTheBuiltInText() {
        // No fallback to another language: the app's own text for the code is shown instead.
        assertNull(customMessage(all - "message_el", "el"))
        assertNull(customMessage(all + ("message_el" to JsonPrimitive("  ")), "el"))
        assertNull(customMessage(emptyMap(), "en"))
    }

    @Test
    fun theGemsReasonReadsNeedAndHave() {
        // SPEC §16.2: `gems` with {need, have}.
        assertEquals(2 to 1, gemsNeedHave(mapOf("need" to JsonPrimitive(2), "have" to JsonPrimitive(1))))
        assertEquals(0 to 0, gemsNeedHave(emptyMap()))
    }

    @Test
    fun gemPicturesShowOwnedBrightAndMissingFaded() {
        // SPEC §16.4: "You need 2 💎 — you have 1" as pictures.
        assertEquals(listOf(true, false), gemPictures(2, 1))
        assertEquals(listOf(false, false, false), gemPictures(3, 0))
        assertEquals(listOf(true, true), gemPictures(2, 5)) // owned beyond the need aren't shown
        assertTrue(gemPictures(-1, 0).isEmpty())
    }
}
