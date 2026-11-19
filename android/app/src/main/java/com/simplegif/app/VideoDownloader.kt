package com.simplegif.app

import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * 把 Media.url 下载到本地。
 *
 * 两个站点都需要 Referer（B 站还需要 cookie），所以请求头从 Media 里带过来，
 * 而不是在这里写死——否则换个来源就得改这个类。
 */
object VideoDownloader {

    private const val UA =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

    private const val MAX_BYTES = 60L * 1024 * 1024

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        // 跟随重定向后也要校验域名，防止被跳到任意地址
        .addInterceptor { chain ->
            val response = chain.proceed(chain.request())
            HostGuard.check(response.request.url.toString())
            response
        }
        .build()

    /** onProgress(已下载字节, 总字节)，总字节未知时为 -1 */
    fun download(media: Media, dest: File, onProgress: (Long, Long) -> Unit) {
        HostGuard.check(media.url)

        val builder = Request.Builder()
            .url(media.url)
            .header("User-Agent", UA)
            .header("Accept", "*/*")
        if (media.referer.isNotBlank()) builder.header("Referer", media.referer)
        if (media.cookie.isNotBlank()) builder.header("Cookie", media.cookie)

        try {
            client.newCall(builder.build()).execute().use { response ->
                if (!response.isSuccessful) {
                    throw ResolveException(
                        "下载原片失败，服务器返回 ${response.code}。链接可能已失效。"
                    )
                }
                val body = response.body
                    ?: throw ResolveException("下载原片失败：响应是空的。")

                val total = body.contentLength()
                if (total > MAX_BYTES) {
                    throw ResolveException(
                        "源文件 ${total / 1024 / 1024}MB 超过 ${MAX_BYTES / 1024 / 1024}MB 上限。"
                    )
                }

                var done = 0L
                body.byteStream().use { input ->
                    dest.outputStream().use { output ->
                        val buffer = ByteArray(256 * 1024)
                        while (true) {
                            val read = input.read(buffer)
                            if (read < 0) break
                            done += read
                            if (done > MAX_BYTES) {
                                throw ResolveException(
                                    "源文件超过 ${MAX_BYTES / 1024 / 1024}MB 上限，已中止下载。"
                                )
                            }
                            output.write(buffer, 0, read)
                            onProgress(done, total)
                        }
                    }
                }
            }
        } catch (e: IOException) {
            dest.delete()
            throw ResolveException("下载中断：${e.message ?: "网络异常"}")
        } catch (e: ResolveException) {
            dest.delete()
            throw e
        }

        if (dest.length() < 1024) {
            dest.delete()
            throw ResolveException("下载到的文件太小，可能是错误页面而不是视频。")
        }
    }
}
