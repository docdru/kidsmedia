package app.kidsmedia.data

import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/**
 * Parent PIN (SPEC §4.3: stored hashed). PBKDF2 with a random salt, so the
 * stored value doesn't give the 4 digits away at a glance. Format:
 * `pbkdf2$<iterations>$<salt b64>$<hash b64>`.
 */
object Pin {
    const val LENGTH = 4
    private const val ITERATIONS = 120_000

    fun isValid(pin: String): Boolean = pin.length == LENGTH && pin.all { it in '0'..'9' }

    fun hash(pin: String, salt: ByteArray = randomSalt(), iterations: Int = ITERATIONS): String {
        require(isValid(pin)) { "PIN must be $LENGTH digits" }
        val b64 = Base64.getEncoder()
        return "pbkdf2\$$iterations\$${b64.encodeToString(salt)}\$${b64.encodeToString(derive(pin, salt, iterations))}"
    }

    fun verify(pin: String, stored: String?): Boolean {
        if (stored == null || !isValid(pin)) return false
        val parts = stored.split('$')
        if (parts.size != 4 || parts[0] != "pbkdf2") return false
        val iterations = parts[1].toIntOrNull() ?: return false
        val b64 = Base64.getDecoder()
        val salt = runCatching { b64.decode(parts[2]) }.getOrNull() ?: return false
        val expected = runCatching { b64.decode(parts[3]) }.getOrNull() ?: return false
        return MessageDigest.isEqual(derive(pin, salt, iterations), expected)
    }

    private fun randomSalt(): ByteArray = ByteArray(16).also { SecureRandom().nextBytes(it) }

    private fun derive(pin: String, salt: ByteArray, iterations: Int): ByteArray {
        val spec = PBEKeySpec(pin.toCharArray(), salt, iterations, 256)
        return try {
            SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
        } finally {
            spec.clearPassword()
        }
    }
}

/** Wrong PINs lock the pad for a while (5 tries, then 60 s), so a child can't just try all 10 000. */
class PinAttempts(private val clock: () -> Long, private val maxTries: Int = 5, private val lockMs: Long = 60_000) {
    private var failures = 0
    private var lockedUntil = 0L

    fun lockedForMs(): Long = maxOf(0L, lockedUntil - clock())

    /** Returns true when the attempt may be checked at all. */
    fun allowed(): Boolean = lockedForMs() == 0L

    fun record(success: Boolean) {
        if (success) {
            failures = 0
            return
        }
        failures++
        if (failures >= maxTries) {
            failures = 0
            lockedUntil = clock() + lockMs
        }
    }
}
