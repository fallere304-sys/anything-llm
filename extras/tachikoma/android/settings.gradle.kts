// タチコマ Android 版。extras/tachikoma/android で `gradle assembleDebug` (llama.cpp を ./llama.cpp に置くこと。README 参照)
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "Tachikoma"
include(":app")
