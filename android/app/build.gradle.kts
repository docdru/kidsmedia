import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ktlint)
}

android {
    namespace = "app.kidsmedia"
    // AGP 9 compiles Kotlin itself (built-in Kotlin, no kotlin-android plugin); the Kotlin
    // version comes from the compose/serialization plugins in libs.versions.toml.
    // compileSdk 37: required by Compose 1.12, okhttp 5 and coil 3.6.
    compileSdk = 37

    defaultConfig {
        applicationId = "app.kidsmedia"
        minSdk = 26
        // Stays at 35 on purpose: a higher target changes runtime behaviour (edge-to-edge,
        // background limits) on the tablets and needs its own test round.
        targetSdk = 35
        // Release builds get both from the tag (release.yml): -PversionName=1.2.3 -PversionCode=10203
        versionCode = (project.findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = (project.findProperty("versionName") as String?) ?: "0.1.0"
    }

    // Release signing from CI secrets (SPEC §10.2). Without them, assembleRelease stays unsigned.
    val keystore = System.getenv("KM_KEYSTORE_FILE")
    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storePassword = System.getenv("KM_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KM_KEY_ALIAS")
                keyPassword = System.getenv("KM_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = false
        // SPEC §4.3: English and German from day one; a missing or orphaned
        // translation fails CI.
        error += setOf("MissingTranslation", "ExtraTranslation", "MissingQuantity")
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.session)
    implementation(libs.media3.ui)
    implementation(libs.media3.datasource.okhttp)
    // In-app camera for photos of drawings and stories (SPEC §15.3).
    implementation(libs.camera.core)
    implementation(libs.camera.camera2)
    implementation(libs.camera.lifecycle)
    implementation(libs.camera.view)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.coil.compose)
    implementation(libs.coil.network.okhttp)
    debugImplementation(libs.androidx.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)
}

// Pin the formatter so CI and a local `ktlint` CLI agree.
ktlint {
    version.set("1.3.1")
}
