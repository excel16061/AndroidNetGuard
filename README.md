# AndroidNetGuard

AndroidNetGuard is a local Android network-observability prototype with a Python collector and browser dashboard.

## Working features

- USB and direct Wi-Fi connectivity checks with correlated request IDs.
- Bounded message sequences, app restart checks, and user-assisted Wi-Fi reconnect checks.
- Wireless telemetry sessions with RSSI, frequency, link information, and probe outcomes.
- Cancellation logging, run history, charts, and sanitized offline report generation.

Android telemetry is not CSI. There are no implemented security-policy tests, VPN isolation checks, or model-guided test selection. External CSI parsing is experimental and has not been validated with hardware.

## Setup

Requires JDK 17, Android SDK platform 36/build-tools 36.0.0, platform-tools, and Python 3.

```sh
cp env.example.sh env.sh
# Edit env.sh to set your local JDK and Android SDK paths.
source ./env.sh
# Copy local.properties.example to local.properties and set ang.collectorHost
# to the collector's actual LAN IPv4 address.
./gradlew testDebugUnitTest assembleDebug
python3 -I -m unittest discover -s collector -v
python3 -I -m unittest discover -s dashboard -v
```

The app is version 0.4.1, with minSdk 26 and compile/target SDK 36.
Cleartext HTTP exceptions are generated for the debug build only. The local
collector is intended for harmless test traffic on a trusted network; it does not provide TLS.

## Running

Start the collector on specific addresses using its CLI, and the dashboard on localhost:

```sh
python3 -I collector/collector.py --help
python3 -I dashboard/dashboard.py --port 8770 --serial DEVICE_SERIAL
```

Open http://127.0.0.1:8770/ for the local dashboard.
Use ADB to install the debug app in place and import evidence from our app only.
Keep env.sh, local.properties, signing material, and private evidence out of Git.

## Verification and interpretation

Independent Python checks on this reviewed source: **21 collector tests and
95 dashboard/analysis tests passed**. Android builds and physical-device
experiments were not independently repeated in this review.

Connectivity/reliability outcomes require the relevant test preconditions and
correlated evidence. Observation-session completion is a data-collection outcome:
failed probes can be valid observations. Cancellation validation is separate
from the interrupted session's outcome.

Legacy fields containing "fresh" describe first/changed reported value tuples,
versus repeated tuples. They do not establish radio measurement freshness,
caching, or independence. The 50% sample-coverage criterion is a retrospective
engineering rule, not a validated research threshold.

## Review update

- Corrected portable environment setup.
- Clarified telemetry labels without changing recorded evidence or detector calculations.
- Closed CSI file and collector-test socket resources.
- Updated the relevant UI-label regression assertion.

The presentation documents, screenshots, and saved experiment reports are
pending publication review and are not included in this source-only commit.
The original uploaded release remains the private historical reference.

Third-party notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
