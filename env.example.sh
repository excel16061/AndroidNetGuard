# Copy to env.sh and adjust paths for YOUR machine, then: source ./env.sh
# (env.sh is git-ignored; it is not included in this package.)
export JAVA_HOME="/path/to/jdk-17"                 # e.g. macOS Homebrew: /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export ANDROID_HOME="/path/to/android-sdk"         # needs platform-tools, platforms;android-36, build-tools;36.0.0
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$JAVA_HOME/bin:$PATH"
