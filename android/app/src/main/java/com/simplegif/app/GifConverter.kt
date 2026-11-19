package com.simplegif.app

import android.graphics.BitmapFactory
import com.arthenica.ffmpegkit.FFmpegKit
import com.arthenica.ffmpegkit.FFmpegSession
import com.arthenica.ffmpegkit.ReturnCode
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * mp4 -> GIF。设备无关，只依赖打进 APK 的 ffmpeg。
 *
 * 两遍调色板法：palettegen 统计全片用色，paletteuse 套用，比单遍量化干净得多。
 * 体积超预算按梯度降参重转，最多 4 轮。
 */
object GifConverter {

    data class Options(
        val width: Int,
        val fps: Int,
        val maxBytes: Long
    )

    data class Result(
        val file: File,
        val size: Long,
        val width: Int,
        val height: Int,
        val rounds: Int,
        val oversized: Boolean,
        val usedWidth: Int,
        val usedFps: Int
    )

    class ConvertException(message: String) : Exception(message)

    private const val MAX_SECONDS = 30
    private const val MAX_ROUNDS = 4

    /** 当前正在跑的 ffmpeg 会话，用于页面销毁时及时掐断，别白烧 CPU */
    @Volatile
    private var currentSessionId: Long = -1L

    fun cancelCurrent() {
        val id = currentSessionId
        if (id >= 0L) {
            FFmpegKit.cancel(id)
            currentSessionId = -1L
        }
    }

    /**
     * @param onUpdate (阶段文案, 0~100 百分比, 详情)
     */
    fun convert(
        src: File,
        outFile: File,
        cacheDir: File,
        options: Options,
        sourceDurationMs: Long,
        onUpdate: (String, Int, String) -> Unit
    ): Result {
        var width = options.width
        var fps = options.fps
        var last: Result? = null

        for (round in 1..MAX_ROUNDS) {
            // 第一轮给足 0~70%，因为绝大多数转换一轮就过；后续轮次各占一小段
            val spanStart = if (round == 1) 0 else minOf(70 + (round - 2) * 10, 96)
            val spanEnd = if (round == 1) 70 else minOf(spanStart + 10, 99)

            val label = if (round == 1) "转码中" else "第 $round 轮压缩"
            val result = transcodeOnce(
                src = src,
                outFile = outFile,
                cacheDir = cacheDir,
                width = width,
                fps = fps,
                sourceDurationMs = sourceDurationMs,
                spanStart = spanStart,
                spanEnd = spanEnd,
                label = label,
                onUpdate = onUpdate
            )

            last = result.copy(rounds = round, oversized = result.size > options.maxBytes)
            if (result.size <= options.maxBytes) return last

            if (round == MAX_ROUNDS) break
            fps = maxOf(6, (fps * 0.8f).toInt())
            width = maxOf(160, (width * 0.85f).toInt())
        }

        return last ?: throw ConvertException("转码没有产出结果。")
    }

    private fun transcodeOnce(
        src: File,
        outFile: File,
        cacheDir: File,
        width: Int,
        fps: Int,
        sourceDurationMs: Long,
        spanStart: Int,
        spanEnd: Int,
        label: String,
        onUpdate: (String, Int, String) -> Unit
    ): Result {
        val palette = File(cacheDir, "palette.png")
        palette.delete()
        outFile.delete()

        val totalMs = if (sourceDurationMs > 0) {
            minOf(sourceDurationMs, MAX_SECONDS * 1000L)
        } else {
            0L
        }

        val span = (spanEnd - spanStart).coerceAtLeast(1)
        val mid = spanStart + (span * 25 / 100)

        val scale = "fps=$fps,scale=$width:-1:flags=lanczos"

        // 关键：-t 必须作为「输入选项」放在 -i src 之前。
        // 放到两个 -i 之间会被 ffmpeg 当成第二个输入（palette.png）的选项，对输出时长无效，
        // 结果是整段视频照转——这条已用本机 ffmpeg 实测复现过。
        val limit = "-t $MAX_SECONDS"

        onUpdate(label, spanStart, "调色板（1/2）")
        val pass1 = "-y -hide_banner -loglevel error $limit -i \"${src.absolutePath}\" " +
            "-vf \"$scale,palettegen=max_colors=256:stats_mode=diff\" -frames:v 1 " +
            "\"${palette.absolutePath}\""
        runFfmpeg(pass1, 0L) { percent ->
            onUpdate(label, spanStart + span * percent / 100 / 4, "调色板（1/2）")
        }

        if (!palette.exists() || palette.length() == 0L) {
            throw ConvertException("调色板生成失败，源视频可能损坏。")
        }

        onUpdate(label, mid, "渲染 GIF（2/2）")
        val pass2 = "-y -hide_banner -loglevel error $limit -i \"${src.absolutePath}\" " +
            "-i \"${palette.absolutePath}\" " +
            "-lavfi \"$scale[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle\" " +
            "-loop 0 \"${outFile.absolutePath}\""
        runFfmpeg(pass2, totalMs) { percent ->
            val mapped = mid + (spanEnd - mid) * percent / 100
            onUpdate(label, mapped, "渲染 GIF（2/2）")
        }

        palette.delete()

        if (!outFile.exists() || outFile.length() == 0L) {
            throw ConvertException("没有生成 GIF 文件。")
        }

        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(outFile.absolutePath, bounds)

        return Result(
            file = outFile,
            size = outFile.length(),
            width = bounds.outWidth.coerceAtLeast(0),
            height = bounds.outHeight.coerceAtLeast(0),
            rounds = 1,
            oversized = false,
            usedWidth = width,
            usedFps = fps
        )
    }

    /**
     * 同步执行一条 ffmpeg 命令。
     * totalMs > 0 时用 ffmpeg 的统计回调换算真实进度；为 0 时不报进度。
     */
    private fun runFfmpeg(command: String, totalMs: Long, onProgress: (Int) -> Unit) {
        val latch = CountDownLatch(1)
        val failure = arrayOfNulls<String>(1)

        val session: FFmpegSession = FFmpegKit.executeAsync(
            command,
            { s ->
                if (!ReturnCode.isSuccess(s.returnCode)) {
                    failure[0] = (s.allLogsAsString ?: "")
                        .takeLast(500)
                        .ifBlank { "ffmpeg 返回码 ${s.returnCode}" }
                }
                currentSessionId = -1L
                latch.countDown()
            },
            null,
            { statistics ->
                if (totalMs > 0) {
                    val percent = (statistics.time * 100 / totalMs).toInt().coerceIn(0, 100)
                    onProgress(percent)
                }
            }
        )

        currentSessionId = session.sessionId

        if (!latch.await(8, TimeUnit.MINUTES)) {
            FFmpegKit.cancel(session.sessionId)
            currentSessionId = -1L
            throw ConvertException("转码超时。推文动图可能太长，换一条短的试试。")
        }

        failure[0]?.let { throw ConvertException("ffmpeg 执行失败：\n$it") }
    }
}
