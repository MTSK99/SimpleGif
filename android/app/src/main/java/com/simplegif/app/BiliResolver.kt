package com.simplegif.app

import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * B 站视频解析，走公开接口，不需要登录、不需要 cookie、不需要签名算法。
 *
 * 三步：
 *   1. x/web-interface/view 拿 cid、标题、分 P、原始分辨率
 *   2. x/player/playurl 用 platform=html5 拿一份标准 CDN 的 mp4 直链
 *   3. 交给 VideoDownloader 下载（必须带 Referer，否则 CDN 拒绝）
 *
 * 实测出来的两个坑：
 *   - 请求头不全时 view 会返回 412（HTML 错误页），补上 Accept-Language / Origin 就正常。
 *     这不是 cookie 问题，所以不需要先访问主页拿 buvid3。
 *   - CDN 对移动端 UA 会 403，必须用桌面 UA。
 *
 * 只做单 P（多 P 视频取第一段）。不用 DASH，因为它音视频分离，转 GIF 用不上。
 */
object BiliResolver {

    private const val DESKTOP_UA =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

    private const val REFERER = "https://www.bilibili.com/"

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private val BV_RE = Regex("""BV[0-9A-Za-z]{10}""")
    private val SHORT_LINK_RE = Regex("""https?://b23\.tv/[A-Za-z0-9]+""", RegexOption.IGNORE_CASE)

    fun matches(text: String): Boolean =
        text.contains("bilibili.com", ignoreCase = true) || text.contains("b23.tv", ignoreCase = true)

    fun resolve(raw: String): Media {
        val expanded = SHORT_LINK_RE.find(raw)?.let { expandShortLink(it.value) } ?: raw
        val bvid = BV_RE.find(expanded)?.value
            ?: throw ResolveException(
                "没认出 B 站的视频号（BV 号）。\n" +
                    "请用视频页面的完整地址，形如 https://www.bilibili.com/video/BV1xx411c7mD"
            )

        val view = getJson("https://api.bilibili.com/x/web-interface/view?bvid=$bvid")
        if (view.optInt("code", -1) != 0) {
            throw ResolveException(
                "B 站接口报错：${view.optString("message").ifBlank { "未知原因" }}\n" +
                    "（番剧、付费课程这类需要登录的内容转不了）"
            )
        }
        val data = view.optJSONObject("data") ?: throw ResolveException("B 站返回的数据不完整。")

        val pages = data.optJSONArray("pages")
        val cid = if (pages != null && pages.length() > 0) {
            pages.getJSONObject(0).optLong("cid")
        } else {
            data.optLong("cid")
        }
        if (cid <= 0L) throw ResolveException("拿不到这个视频的 cid，可能是番剧或需要登录的内容。")

        val title = data.optString("title")
        val owner = data.optJSONObject("owner")?.optString("name").orEmpty()
        val duration = data.optDouble("duration", 0.0)

        // dimension 是原始分辨率，rotate=1 表示竖屏拍摄，宽高要互换
        val dimension = data.optJSONObject("dimension")
        var width = dimension?.optInt("width", 0) ?: 0
        var height = dimension?.optInt("height", 0) ?: 0
        if (dimension?.optInt("rotate", 0) == 1) {
            val tmp = width
            width = height
            height = tmp
        }

        val play = getJson(
            "https://api.bilibili.com/x/player/playurl" +
                "?bvid=$bvid&cid=$cid&qn=64&fnval=1&platform=html5&high_quality=1"
        )
        if (play.optInt("code", -1) != 0) {
            throw ResolveException(
                "B 站不给播放地址：${play.optString("message").ifBlank { "可能需要登录" }}"
            )
        }

        val durl = play.optJSONObject("data")?.optJSONArray("durl")
        val first = (if (durl != null && durl.length() > 0) durl.optJSONObject(0) else null)
            ?: throw ResolveException("这条视频没有可直接下载的 mp4。")

        val videoUrl = first.optString("url")
        HostGuard.check(videoUrl)

        return Media(
            url = videoUrl,
            author = owner,
            text = title,
            width = width,
            height = height,
            duration = duration,
            referer = REFERER,
            sourceName = "B 站"
        )
    }

    /** b23.tv 短链是 302 跳转，跟随一次就能拿到带 BV 号的真实地址 */
    private fun expandShortLink(shortUrl: String): String {
        return try {
            val request = Request.Builder()
                .url(shortUrl)
                .header("User-Agent", DESKTOP_UA)
                .build()
            client.newCall(request).execute().use { response ->
                response.request.url.toString()
            }
        } catch (e: IOException) {
            throw ResolveException("短链展开失败：${e.message ?: "网络异常"}")
        }
    }

    private fun getJson(url: String): JSONObject {
        val request = Request.Builder()
            .url(url)
            .header("User-Agent", DESKTOP_UA)
            .header("Referer", REFERER)
            .header("Accept", "application/json, text/plain, */*")
            .header("Accept-Language", "zh-CN,zh;q=0.9")
            .header("Origin", REFERER.removeSuffix("/"))
            .build()

        val body = try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    throw ResolveException(
                        if (response.code == 412) {
                            "B 站把这次请求当成爬虫拦了（412）。\n稍等一会儿再试，或者换一条视频。"
                        } else {
                            "B 站接口返回 HTTP ${response.code}。"
                        }
                    )
                }
                response.body?.string().orEmpty()
            }
        } catch (e: IOException) {
            throw ResolveException(
                "连不上 B 站，请检查手机网络。\n" +
                    "（国内站点一般不用代理；如果开着全局代理，把 B 站加进直连规则更稳）"
            )
        }

        if (!body.trimStart().startsWith("{")) {
            throw ResolveException("B 站返回的不是 JSON，可能被风控拦了，稍后重试。")
        }
        return try {
            JSONObject(body)
        } catch (e: Exception) {
            throw ResolveException("B 站返回的 JSON 解析失败。")
        }
    }
}
