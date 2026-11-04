# Third-party components

| Component | Where | License | Bundled in this package? |
|---|---|---|---|
| Gradle Wrapper (`gradlew`, `gradlew.bat`, `gradle/wrapper/gradle-wrapper.jar`) | build | Apache License 2.0 (header in `gradlew`; `META-INF/LICENSE` inside the jar) | yes, unmodified |
| Gradle 9.6.1 distribution | build | Apache License 2.0 | no; downloaded by the wrapper (SHA-256 pinned in `gradle-wrapper.properties`) |
| Android Gradle Plugin 9.5.0 | build | Apache License 2.0 | no; resolved from Google Maven |
| JUnit 4.13.2 | tests only | Eclipse Public License 1.0 | no; resolved from Maven Central |
| Hamcrest Core 1.3 (via JUnit) | tests only | BSD 3-Clause | no |
| Android SDK platform 36 / build-tools 36.0.0 | build | Android SDK License | no; installed separately |
| Python 3 standard library | collector, dashboard | PSF License | no |
| RuView ADR-018 CSI frame format (ruvnet/RuView @ 5728b90949e60e3f908d996997d453147c1794e0) | `dashboard/csi.py` | MIT License, Copyright (c) 2024 rUv | no code copied; the parser is an independent implementation of the documented byte layout, attributed here and in the module docstring |

The app has no third-party runtime dependencies (no AndroidX).

**Project license:** the project files carry no license. All rights are reserved by the author
until a license is chosen. This package does not grant any license.
