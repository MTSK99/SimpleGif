package com.simplegif.app

import android.content.ClipboardManager
import android.content.Intent
import android.graphics.ImageDecoder
import android.graphics.drawable.AnimatedImageDrawable
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.ChipGroup
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var etLink: TextInputEditText
    private lateinit var btnPaste: MaterialButton
    private lateinit var btnConvert: MaterialButton
    private lateinit var chipGroup: ChipGroup
    private lateinit var progressBox: View
    private lateinit var tvStage: TextView
    private lateinit var tvPercent: TextView
    private lateinit var tvDetail: TextView
    private lateinit var progressBar: ProgressBar
    private lateinit var tvError: TextView
    private lateinit var resultBox: View
    private lateinit var tvSize: TextView
    private lateinit var ivPreview: ImageView
    private lateinit var tvInfo: TextView
    private lateinit var tvWarn: TextView
    private lateinit var btnSave: MaterialButton
    private lateinit var btnShare: MaterialButton
    private lateinit var tvIdleTip: TextView

    private var currentFile: File? = null
    private var currentName: String = "simplegif.gif"
    private var busy = false

    @Volatile
    private var lastProgressAt = 0L

    companion object {
        private const val MB = 1024L * 1024L
        private const val MAX_INPUT_LENGTH = 2000
        private val EMOJI = GifConverter.Options(width = 320, fps = 12, maxBytes = (4.8 * MB).toLong())
        private val CHAT = GifConverter.Options(width = 480, fps = 15, maxBytes = 8L * MB)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        bindViews()
        applyWindowInsets()
        wireEvents()
        handleIncomingIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIncomingIntent(intent)
    }

    override fun onDestroy() {
        super.onDestroy()
        // 页面真的销毁了就把 ffmpeg 掐掉，别让它在后台空转满 8 分钟
        GifConverter.cancelCurrent()
    }

    private fun bindViews() {
        etLink = findViewById(R.id.etLink)
        btnPaste = findViewById(R.id.btnPaste)
        btnConvert = findViewById(R.id.btnConvert)
        chipGroup = findViewById(R.id.chipGroup)
        progressBox = findViewById(R.id.progressBox)
        tvStage = findViewById(R.id.tvStage)
        tvPercent = findViewById(R.id.tvPercent)
        tvDetail = findViewById(R.id.tvDetail)
        progressBar = findViewById(R.id.progressBar)
        tvError = findViewById(R.id.tvError)
        resultBox = findViewById(R.id.resultBox)
        tvSize = findViewById(R.id.tvSize)
        ivPreview = findViewById(R.id.ivPreview)
        tvInfo = findViewById(R.id.tvInfo)
        tvWarn = findViewById(R.id.tvWarn)
        btnSave = findViewById(R.id.btnSave)
        btnShare = findViewById(R.id.btnShare)
        tvIdleTip = findViewById(R.id.tvIdleTip)
    }

    /** targetSdk 35 在 Android 15 上强制边到边，不处理 insets 会被状态栏和手势条压住 */
    private fun applyWindowInsets() {
        val scroll = findViewById<View>(R.id.rootScroll)
        val content = findViewById<View>(R.id.rootContent)
        val baseTop = content.paddingTop
        val baseBottom = content.paddingBottom

        ViewCompat.setOnApplyWindowInsetsListener(scroll) { _, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            content.updatePadding(top = baseTop + bars.top, bottom = baseBottom + bars.bottom)
            insets
        }
    }

    private fun wireEvents() {
        btnPaste.setOnClickListener { pasteFromClipboard() }
        btnConvert.setOnClickListener { startConvert() }

        btnSave.setOnClickListener {
            val file = currentFile ?: return@setOnClickListener
            btnSave.isEnabled = false
            lifecycleScope.launch {
                try {
                    withContext(Dispatchers.IO) {
                        GifStore.saveToGallery(this@MainActivity, file, currentName)
                    }
                    toast("已保存到 相册/Pictures/简单gif")
                } catch (e: Exception) {
                    toast("保存失败：${e.message ?: "未知错误"}")
                } finally {
                    btnSave.isEnabled = true
                }
            }
        }

        btnShare.setOnClickListener {
            val file = currentFile ?: return@setOnClickListener
            try {
                GifStore.shareToWechat(this, file)
            } catch (e: Exception) {
                toast("分享失败：${e.message ?: "没有能接收图片的应用"}")
            }
        }
    }

    private fun handleIncomingIntent(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            val text = intent.getStringExtra(Intent.EXTRA_TEXT)
            if (!text.isNullOrBlank()) {
                etLink.setText(extractLink(text))
                etLink.setSelection(etLink.text?.length ?: 0)
            }
        }
    }

    private fun pasteFromClipboard() {
        val manager = getSystemService(ClipboardManager::class.java)
        val clip = manager?.primaryClip
        if (clip == null || clip.itemCount == 0) {
            toast("剪贴板是空的")
            return
        }
        val text = clip.getItemAt(0).coerceToText(this).toString()
        if (text.isBlank()) {
            toast("剪贴板里没有文字")
            return
        }
        etLink.setText(extractLink(text))
        etLink.setSelection(etLink.text?.length ?: 0)
    }

    /** 从一长段分享文本里挑出真正的链接，别把整段文案塞进输入框 */
    private fun extractLink(text: String): String {
        val status = Regex(
            """https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/[^\s"'<>]*?/status(?:es)?/\d+""",
            RegexOption.IGNORE_CASE
        ).find(text)
        if (status != null) return status.value

        val media = Regex("""https?://video\.twimg\.com/[^\s"'<>]+""", RegexOption.IGNORE_CASE).find(text)
        if (media != null) return media.value

        val any = Regex("""https?://[^\s"'<>]+""").find(text)
        return any?.value ?: text.trim()
    }

    private fun currentOptions(): GifConverter.Options =
        if (chipGroup.checkedChipId == R.id.chipChat) CHAT else EMOJI

    private fun startConvert() {
        if (busy) return
        val input = etLink.text?.toString()?.trim().orEmpty()
        if (input.isEmpty()) {
            toast("先把推文链接粘进来")
            etLink.requestFocus()
            return
        }
        if (input.length > MAX_INPUT_LENGTH) {
            toast("输入太长了，只粘贴一条链接就行")
            return
        }

        setBusy(true)
        hideError()
        resultBox.visibility = View.GONE
        tvIdleTip.visibility = View.GONE
        showProgress("解析链接", 2, "")

        val options = currentOptions()

        lifecycleScope.launch {
            val gifDir = File(cacheDir, "gif").apply { mkdirs() }
            val sourceFile = File(gifDir, "source.mp4")
            val outFile = File(gifDir, "out.gif")

            try {
                val media = withContext(Dispatchers.IO) { Resolver.resolve(input) }
                val caption = if (media.author.isNotBlank()) "来源 ${media.author}" else "来源 ${media.sourceName}"
                updateProgress("下载原片", 12, caption)

                withContext(Dispatchers.IO) {
                    VideoDownloader.download(media, sourceFile) { done, total ->
                        val percent = if (total > 0) {
                            (12 + (done.toDouble() / total * 23).toInt()).coerceIn(12, 35)
                        } else {
                            20
                        }
                        val detail = if (total > 0) {
                            "${formatSize(done)} / ${formatSize(total)}"
                        } else {
                            formatSize(done)
                        }
                        updateProgress("下载原片", percent, detail)
                    }
                }

                updateProgress("转码中", 36, "首次运行会慢一些")

                val result = withContext(Dispatchers.IO) {
                    GifConverter.convert(
                        src = sourceFile,
                        outFile = outFile,
                        cacheDir = gifDir,
                        options = options,
                        sourceDurationMs = (media.duration * 1000).toLong()
                    ) { stage, percent, detail ->
                        // 转码占 36~99，真实进度来自 ffmpeg 的统计回调
                        updateProgress(stage, 36 + percent * 63 / 100, detail)
                    }
                }

                currentFile = result.file
                currentName = "simplegif_${System.currentTimeMillis()}.gif"
                showResult(result, media, options)
            } catch (e: Exception) {
                showError(e.message ?: "未知错误")
            } finally {
                // 源片无论成功失败都清掉，别把缓存撑大
                sourceFile.delete()
                setBusy(false)
            }
        }
    }

    private fun showResult(
        result: GifConverter.Result,
        media: Media,
        options: GifConverter.Options
    ) {
        showProgress("完成", 100, "")

        tvSize.text = formatSize(result.size)

        var info = "尺寸 ${result.width}×${result.height} · ${formatSize(result.size)}\n"
        info += "参数 ${result.usedWidth}px / ${result.usedFps}fps · 转了 ${result.rounds} 轮"
        if (media.duration > 0) {
            info += "\n原片 ${if (media.width > 0) "${media.width}×${media.height}" else "未知尺寸"} · " +
                String.format(Locale.US, "%.1f 秒", media.duration)
        }
        if (media.text.isNotBlank()) info += "\n「${media.text.take(28)}」"
        if (media.author.isNotBlank()) {
            info += "\n来源 ${media.sourceName} · ${media.author}"
        } else if (media.sourceName.isNotBlank()) {
            info += "\n来源 ${media.sourceName}"
        }
        tvInfo.text = info

        if (result.oversized) {
            tvWarn.visibility = View.VISIBLE
            val limitMb = String.format(Locale.US, "%.1f", options.maxBytes / 1024.0 / 1024.0)
            tvWarn.text = if (options.maxBytes <= 5L * MB) {
                "已经压到参数下限，成品 ${formatSize(result.size)} 仍超过设定的 ${limitMb}MB。" +
                    "微信表情库对单个动图有约 5MB 的上限，这张加进表情大概率会失败，只能当普通图片发。"
            } else {
                "已经压到参数下限，成品 ${formatSize(result.size)} 仍超过设定的 ${limitMb}MB。" +
                    "当普通图片发没问题，但微信可能会压缩它；想更小可以换成「微信表情」预设。"
            }
        } else {
            tvWarn.visibility = View.GONE
        }

        showPreview(result.file)
        resultBox.visibility = View.VISIBLE
        progressBox.visibility = View.GONE
    }

    private fun showPreview(file: File) {
        try {
            val source = ImageDecoder.createSource(file)
            val drawable = ImageDecoder.decodeDrawable(source)
            ivPreview.setImageDrawable(drawable)
            ivPreview.visibility = View.VISIBLE
            if (drawable is AnimatedImageDrawable) {
                drawable.repeatCount = AnimatedImageDrawable.REPEAT_INFINITE
                drawable.start()
            }
        } catch (t: Throwable) {
            // 超大 GIF 解码失败不算致命，保存按钮仍然可用
            ivPreview.visibility = View.GONE
        }
    }

    private fun showProgress(stage: String, percent: Int, detail: String) {
        progressBox.visibility = View.VISIBLE
        tvStage.text = stage
        tvPercent.text = "$percent%"
        progressBar.progress = percent
        tvDetail.text = detail
    }

    /** 进度回调来自下载线程和 ffmpeg 线程，统一切主线程并做节流 */
    private fun updateProgress(stage: String, percent: Int, detail: String) {
        val now = System.currentTimeMillis()
        if (percent < 100 && now - lastProgressAt < 120) return
        lastProgressAt = now
        runOnUiThread {
            tvStage.text = stage
            tvPercent.text = "$percent%"
            progressBar.progress = percent
            if (detail.isNotBlank()) tvDetail.text = detail
        }
    }

    private fun showError(message: String) {
        progressBox.visibility = View.GONE
        resultBox.visibility = View.GONE
        tvError.visibility = View.VISIBLE
        tvError.text = message
        tvIdleTip.visibility = View.VISIBLE
    }

    private fun hideError() {
        tvError.visibility = View.GONE
    }

    private fun setBusy(value: Boolean) {
        busy = value
        btnConvert.isEnabled = !value
        btnConvert.text = getString(if (value) R.string.action_convert_running else R.string.action_convert)
        btnPaste.isEnabled = !value
        // 转换中参数已经定死，锁住输入避免让人以为改了会生效
        etLink.isEnabled = !value
        chipGroup.isEnabled = !value
        for (i in 0 until chipGroup.childCount) {
            chipGroup.getChildAt(i).isEnabled = !value
        }
    }

    private fun formatSize(bytes: Long): String = when {
        bytes >= 1024 * 1024 -> String.format(Locale.US, "%.2fMB", bytes / 1024.0 / 1024.0)
        else -> "${bytes / 1024}KB"
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}
