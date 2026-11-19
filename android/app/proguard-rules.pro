# ffmpeg-kit 通过 JNI 回调 Java 层，类名和方法名不能被混淆
-keep class com.arthenica.ffmpegkit.** { *; }
-dontwarn com.arthenica.ffmpegkit.**

# OkHttp 在 Android 上有可选依赖，缺了不影响运行
-dontwarn okhttp3.**
-dontwarn okio.**
