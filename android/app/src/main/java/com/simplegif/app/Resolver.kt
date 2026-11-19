package com.simplegif.app

/**
 * 解析入口：按链接所属站点分发到具体解析器。
 *
 * 加新站点只需要在这里多一个分支 + 一个 Resolver 对象，
 * 下载和转码完全不用动。
 *
 * 为什么只做少数站点而不是集成 yt-dlp：
 *     yt-dlp 需要打包 Python 运行时，APK 会从 19MB 涨到 55MB 以上，
 *     而且解析规则固化在安装包里，站点改版后必须发新版本。
 *     对「先能用」这个目标，只做接口稳定的站点更划算。
 */
object Resolver {

    fun resolve(input: String): Media {
        val raw = input.trim()
        if (raw.isEmpty()) throw ResolveException("先把链接粘进来。")

        return when {
            BiliResolver.matches(raw) -> BiliResolver.resolve(raw)
            else -> TwitterResolver.resolve(raw)
        }
    }
}
