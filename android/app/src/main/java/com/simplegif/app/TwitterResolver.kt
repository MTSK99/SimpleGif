package com.simplegif.app

import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * 把 X 推文链接变成可下载的 mp4 地址。
 *
 * X 上的「GIF」实际是 H.264 编码的 mp4，所以要转成真 GIF 必须再走一次 ffmpeg。
 */
object TwitterResolver {

    private const val UA =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private val TWEET_ID = Regex(
        """(?:twitter\.com|x\.com)/(?:[A-Za-z0-9_]{1,20}|i/web|i)/status(?:es)?/(\d{1,25})""",
        RegexOption.IGNORE_CASE
    )

    private val MEDIA_URL = Regex(
        """https?://(?:video|pbs)\.twimg\.com/[^\s"'<>\\]+?\.(?:mp4|m3u8|gif)(?:\?[^\s"'<>\\]*)?""",
        RegexOption.IGNORE_CASE
    )

    /** 把 .../avc1/720x720/xxx.mp4 里的分辨率读出来，用于挑清晰的那份 */
    private val PIXELS = Regex("""/(\d{2,5})x(\d{2,5})/""")

    fun resolve(input: String): Media {
        val raw = input.trim()
        if (raw.isEmpty()) throw ResolveException("先把推文链接粘进来。")

        // 1) 输入里直接带 twimg 直链
        MEDIA_URL.find(raw)?.let { match ->
            val url = match.value
            HostGuard.check(url)
            return Media(url = url, referer = "https://x.com/", sourceName = "X")
        }

        // 2) 推文链接
        val tweetId = TWEET_ID.find(raw)?.groupValues?.get(1)
            ?: throw ResolveException(
                "没识别出推文链接。\n需要形如 https://x.com/用户名/status/1234567890123456789 的地址，\n" +
                    "也就是打开具体那条推文后用「复制链接」拿到的那种。"
            )

        val root = try {
            fetchTweet(tweetId)
        } catch (e: IOException) {
            throw ResolveException(
                "连不上解析服务，请检查手机网络。\n" +
                    "如果 X 本身也打不开，需要先开代理（VPN 模式的代理 App 会自动接管，不用单独设置）。"
            )
        }

        val candidates = ArrayList<String>()
        collectMediaUrls(root, candidates, 0)
        val best = pickBest(candidates) ?: throw ResolveException(
            "这条推文里没有可用的视频文件。\n纯文字、纯图片的推文没有可以转的东西；" +
                "只有流媒体（m3u8）的特殊推文暂时也不支持。"
        )
        HostGuard.check(best)

        val tweet = root.optJSONObject("tweet") ?: root
        val author = tweet.optJSONObject("author")?.optString("name").orEmpty()
        val text = tweet.optString("text").orEmpty()

        var width = 0
        var height = 0
        var duration = 0.0
        findHolder(root, best, 0)?.let { holder ->
            width = holder.optInt("width", 0)
            height = holder.optInt("height", 0)
            duration = holder.optDouble("duration", 0.0)
        }

        return Media(
            url = best,
            author = author,
            text = text.replace("\n", " ").take(80),
            width = width,
            height = height,
            duration = duration,
            referer = "https://x.com/",
            sourceName = "X"
        )
    }

    /** 优先 fxtwitter（字段最全），失败再退到 Twitter 自家的 syndication 接口。 */
    private fun fetchTweet(tweetId: String): JSONObject {
        val errors = ArrayList<String>()

        try {
            val payload = httpGet("https://api.fxtwitter.com/status/$tweetId")
            val root = JSONObject(payload)
            val code = root.optInt("code", 0)
            if (code == 200) return root
            errors.add("fxtwitter: ${root.optString("message").ifBlank { "code=$code" }}")
        } catch (e: HttpStatusException) {
            errors.add("fxtwitter: HTTP ${e.code}")
        } catch (e: IOException) {
            throw e
        } catch (e: Exception) {
            errors.add("fxtwitter: ${e.message ?: "解析失败"}")
        }

        try {
            val payload = httpGet("https://cdn.syndication.twimg.com/tweet-result?id=$tweetId&token=0")
            return JSONObject(payload)
        } catch (e: HttpStatusException) {
            errors.add("syndication: HTTP ${e.code}")
        } catch (e: IOException) {
            throw e
        } catch (e: Exception) {
            errors.add("syndication: ${e.message ?: "解析失败"}")
        }

        throw ResolveException(
            "这条推文解析不出来。\n可能已删除、仅粉丝可见，或解析服务临时不可用。\n" +
                errors.joinToString("\n")
        )
    }

    private fun httpGet(url: String): String {
        val request = Request.Builder()
            .url(url)
            .header("User-Agent", UA)
            .header("Accept", "application/json")
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw HttpStatusException(response.code)
            }
            return response.body?.string() ?: throw HttpStatusException(response.code)
        }
    }

    /** 深度遍历任意 JSON，只收集 .mp4（m3u8 收进来也下不成，避免选中后拿到一堆文本） */
    private fun collectMediaUrls(node: Any?, out: MutableList<String>, depth: Int) {
        if (depth > 12 || node == null) return
        when (node) {
            is JSONObject -> {
                val keys = node.keys()
                while (keys.hasNext()) {
                    collectMediaUrls(node.opt(keys.next()), out, depth + 1)
                }
            }
            is JSONArray -> {
                for (i in 0 until node.length()) {
                    collectMediaUrls(node.opt(i), out, depth + 1)
                }
            }
            is String -> {
                val value = node.trim()
                if (value.startsWith("http") && value.contains(".mp4")) {
                    out.add(value)
                }
            }
        }
    }

    /** 找到包含目标地址的那个对象，用来读取时长和尺寸。 */
    private fun findHolder(node: Any?, target: String, depth: Int): JSONObject? {
        if (depth > 12 || node == null) return null
        when (node) {
            is JSONObject -> {
                val keys = node.keys()
                while (keys.hasNext()) {
                    val value = node.opt(keys.next())
                    if (value is String && value.trim() == target) return node
                }
                val keys2 = node.keys()
                while (keys2.hasNext()) {
                    findHolder(node.opt(keys2.next()), target, depth + 1)?.let { return it }
                }
            }
            is JSONArray -> {
                for (i in 0 until node.length()) {
                    findHolder(node.opt(i), target, depth + 1)?.let { return it }
                }
            }
        }
        return null
    }

    private fun pickBest(urls: List<String>): String? =
        urls.distinct()
            .filter { it.substringBefore('?').lowercase().endsWith(".mp4") }
            .minByOrNull { score(it) }

    private fun score(url: String): Long {
        val path = url.substringBefore('?').lowercase()
        val rank = when {
            path.contains("tweet_video") && path.endsWith(".mp4") -> 0L
            path.contains("amplify_video") && path.endsWith(".mp4") -> 1L
            path.endsWith(".mp4") -> 2L
            else -> 3L
        }
        val pixels = PIXELS.find(path)?.let {
            it.groupValues[1].toLong() * it.groupValues[2].toLong()
        } ?: 0L
        // rank 主导，同档里分辨率高的优先；用 Long 避免极端路径溢出翻转排序
        return rank * 1_000_000_000L - pixels
    }
}
