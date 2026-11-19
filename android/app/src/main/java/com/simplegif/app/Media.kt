package com.simplegif.app

import android.net.Uri
import java.io.IOException

/**
 * 解析出来的媒体，附带下载它所需要的请求头。
 *
 * referer / cookie 不是可选项：B 站 CDN 会校验这两个，缺了直接 403；
 * 推特的 pbs 图片也同样要 Referer。所以跟着地址一起传下去。
 */
data class Media(
    val url: String,
    val author: String = "",
    val text: String = "",
    val width: Int = 0,
    val height: Int = 0,
    val duration: Double = 0.0,
    val referer: String = "",
    /** 预留：将来接需要登录的来源（微博等）时，下载要带 cookie */
    val cookie: String = "",
    val sourceName: String = ""
)

class ResolveException(message: String) : Exception(message)

/** HTTP 层面的失败，和「连不上」要区分开，否则提示会误导用户去查网络 */
internal class HttpStatusException(val code: Int) : IOException("HTTP $code")

/**
 * 下载地址的域名白名单。
 *
 * 用后缀匹配而不是精确匹配：这些站点的 CDN 域名不固定
 * （B 站的 mcdn 节点每次都不一样），精确匹配会误伤正常地址。
 */
object HostGuard {

    private val ALLOWED_SUFFIXES = listOf(
        "twimg.com",
        "bilivideo.com",
        "mountaintoys.cn",   // B 站 P2P CDN 节点
        "bilibili.com",
        "hdslb.com",         // B 站图片/静态资源
        "b23.tv"
    )

    fun check(url: String) {
        val host = Uri.parse(url).host?.lowercase().orEmpty()
        if (host.isEmpty()) {
            throw ResolveException("媒体地址无法解析，已中止。")
        }
        val allowed = ALLOWED_SUFFIXES.any { suffix ->
            host == suffix || host.endsWith(".$suffix")
        }
        if (!allowed) {
            throw ResolveException("媒体地址指向了不受信任的域名（$host），已中止。")
        }
    }
}
