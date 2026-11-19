plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.simplegif.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.simplegif.app"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"

        // 只打 arm64，APK 少一半体积；2020 年之后的手机基本都是这个架构
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            // 自用 APK，用 debug 签名，装到手机上不需要额外配置
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    // ffmpeg-kit 的 .so 必须用传统方式打包，否则解压不出来
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // ffmpeg-kit 官方 2025 年退役，这是社区维护版，包名与 API 与原版一致
    implementation("dev.ffmpegkit-maintained:ffmpeg-kit-full-gpl:8.1.9")
}
