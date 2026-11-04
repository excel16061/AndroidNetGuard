import java.util.Properties

plugins {
    id("com.android.application")
}

// ---------------------------------------------------------------------------
// Debug-only cleartext allow-list.
// The collector's LAN address is machine-specific, so it is NOT committed.
// Set it in the git-ignored local.properties:   ang.collectorHost=192.168.x.y
// (or pass -Pang.collectorHost=...). Loopback is always allowed for the USB path.
// The generated file is added ONLY to the debug variant; release has no
// network-security config and keeps Android's default (cleartext blocked).
// ---------------------------------------------------------------------------
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
val collectorHost: String = (findProperty("ang.collectorHost") as String?)
    ?: localProps.getProperty("ang.collectorHost", "")

abstract class GenerateDebugNetworkSecurityConfig : DefaultTask() {
    @get:Input abstract val hosts: ListProperty<String>
    @get:OutputDirectory abstract val outputDir: DirectoryProperty

    @TaskAction
    fun generate() {
        val ipv4 = Regex("^(25[0-5]|2[0-4]\\d|1?\\d?\\d)(\\.(25[0-5]|2[0-4]\\d|1?\\d?\\d)){3}$")
        val hostname = Regex("^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
        val domains = hosts.get().map { it.trim() }.filter { it.isNotEmpty() }.onEach {
            require(ipv4.matches(it) || hostname.matches(it)) { "ang.collectorHost must be one IPv4 address or hostname, got '$it'" }
        }
        val entries = domains.joinToString("\n") { "        <domain includeSubdomains=\"false\">$it</domain>" }
        val f = outputDir.get().file("xml/network_security_config_debug.xml").asFile
        f.parentFile.mkdirs()
        f.writeText("""<?xml version="1.0" encoding="utf-8"?>
<!-- GENERATED for the DEBUG build only by :app:generateDebugNetworkSecurityConfig. Do not edit. -->
<network-security-config>
    <base-config cleartextTrafficPermitted="false" />
    <domain-config cleartextTrafficPermitted="true">
$entries
    </domain-config>
</network-security-config>
""")
    }
}

val generateDebugNsc = tasks.register<GenerateDebugNetworkSecurityConfig>("generateDebugNetworkSecurityConfig") {
    hosts.set(listOf("127.0.0.1", "localhost", collectorHost))
    outputDir.set(layout.buildDirectory.dir("generated/ang/debugNsc"))
}

android {
    namespace = "com.androidnetguard.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.androidnetguard.app"
        minSdk = 26          // test device: Android 13 / API 33
        targetSdk = 36
        versionCode = 5
        versionName = "0.4.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

androidComponents {
    onVariants(selector().withBuildType("debug")) { variant ->
        variant.sources.res?.addGeneratedSourceDirectory(generateDebugNsc, GenerateDebugNetworkSecurityConfig::outputDir)
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}
