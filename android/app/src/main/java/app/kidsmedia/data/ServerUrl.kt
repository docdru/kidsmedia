package app.kidsmedia.data

/**
 * Normalizes what the parent typed as the server address.
 *
 * SPEC §7: HTTPS is required, except for private addresses (handy while testing at home).
 */
object ServerUrl {
    sealed interface Result {
        data class Ok(val url: String) : Result

        data object Invalid : Result

        data object HttpsRequired : Result
    }

    fun normalize(input: String): Result {
        var text = input.trim().trimEnd('/')
        if (text.isEmpty()) return Result.Invalid
        if (!text.contains("://")) text = "https://$text"
        val scheme = text.substringBefore("://").lowercase()
        val rest = text.substringAfter("://")
        val host = rest.substringBefore('/').substringBefore(':').lowercase()
        if (host.isEmpty() || host.any { it.isWhitespace() }) return Result.Invalid
        return when (scheme) {
            "https" -> Result.Ok("https://$rest")
            "http" -> if (isPrivate(host)) Result.Ok("http://$rest") else Result.HttpsRequired
            else -> Result.Invalid
        }
    }

    fun isPrivate(host: String): Boolean {
        if (host == "localhost" || host.endsWith(".local")) return true
        val parts = host.split('.').map { it.toIntOrNull() ?: return false }
        if (parts.size != 4 || parts.any { it !in 0..255 }) return false
        val (a, b) = parts
        return a == 10 || a == 127 || (a == 172 && b in 16..31) || (a == 192 && b == 168)
    }
}
