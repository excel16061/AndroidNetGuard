# External CSI hardware plan

**Status: no external CSI hardware is connected, and none has been tested.** The dashboard shows
"External CSI hardware not connected" until real frames arrive. Nothing in this project claims
compatibility with any device. This document says what would have to be verified first.

Android phones don't expose Wi-Fi CSI to apps. The phone's `ANDROID_WIFI_TELEMETRY` (RSSI and
link speed) is a different kind of data and is never substituted for CSI.

## What the adapter implements today

`dashboard/csi.py` parses the RuView **ADR-018** raw CSI frame and records it under schema
`ang.csi/1`, with the source kind `REAL_CSI` or `SYNTHETIC_FIXTURE` supplied explicitly by the
caller. The format was reviewed at a pinned revision, and no code was copied:

* Repository: `https://github.com/ruvnet/RuView`, commit
  `5728b90949e60e3f908d996997d453147c1794e0` (2026-10-29), MIT License, "Copyright (c) 2024 rUv".
* Files reviewed:
  * `firmware/esp32-csi-node/main/csi_collector.h` (magic `0xC5110001`, 20-byte header, flag bit 5)
  * `firmware/esp32-csi-node/main/csi_collector.c` (`csi_serialize_frame` byte layout; frequency
    derived from the channel, 0 when unmapped)
  * `firmware/esp32-csi-node/main/stream_sender.c` (UDP `SOCK_DGRAM` sender)
  * `v2/crates/wifi-densepose-hardware/src/esp32_parser.rs` (validation: 1–4 antennas,
    ≤ 1024 I/Q pairs, sibling magics `0xC5110002`–`…07` are not raw CSI)
  * `docs/adr/ADR-018-esp32-dev-implementation.md` (format table)
  * `firmware/esp32-csi-node/README.md`, `sdkconfig.defaults`, `release_bins/*/version.txt`
* Discrepancy noted: the ADR text says "n_subcarriers (≤512)", but the parser code enforces
  `MAX_IQ_PAIRS = 1024 / n_antennas`. The adapter follows the code.
* The ADR-018 header has **no device timestamp**. Records use the host's receive time (monotonic
  and wall clock) and mark the device timestamp `not_provided_by_format`.
* No RuView plugin, installer, script or setup step was installed or run. The repository was read
  from a read-only shallow clone.

## Candidate hardware (to be verified, not claimed)

| Option | Basis for listing it | What must be verified before use |
|---|---|---|
| **ESP32-S3** running RuView `esp32-csi-node` firmware | RuView README labels the S3 its "production" target; `sdkconfig.defaults` sets `CONFIG_IDF_TARGET="esp32s3"`; README badge says ESP-IDF v5.4 | exact board model and flash size; firmware version (`release_bins/version.txt` at the reviewed commit says `0.6.7`, git-sha `8703ade9b`, built 2026-06-02, while the README mentions 0.8.x releases, so check the release you actually flash); frames match ADR-018 byte for byte |
| **ESP32-C6** running RuView firmware | README calls it a "research target"; `sdkconfig.defaults.esp32c6`; `release_bins/c6-onboarding/version.txt` says `0.8.12` | as above; also whether PPDU and flag bytes 18–19 are populated (HE tagging) |
| **ESP32-C5** (experimental) | README section "ESP32-C5 candidate"; mentions ESP-IDF 6.0.2 (minimum 5.5.2) | treat as experimental; README says checks "do not establish sensing accuracy" |
| Other CSI tools (e.g. Nexmon CSI on Broadcom chips, Linux 802.11n CSI Tool on Intel 5300, ESP32-CSI-Tool) | well-known research tools | **not supported**: different frame formats; each would need its own reviewed, pinned adapter |

## Prerequisites (before connecting anything)

1. Choose one board and record its model, chip revision, flash size and MAC (the MAC stays private).
2. Flash firmware **yourself** from a specific, recorded release. Record the tag, commit, ESP-IDF
   version and image SHA-256. Don't run repository setup scripts you haven't read.
3. Put the board on an isolated test network you control. No router configuration changes
   should be needed for a plain UDP stream to the Mac.
4. Decide the Mac's receiving address (a specific IP, never a wildcard) and the UDP port, and
   configure the firmware's destination to match.

## Acceptance criteria for claiming "compatible"

All of these must hold on recorded evidence (`evidence/csi/<session>/frames.jsonl` plus `session.json`):

1. **Receipt:** `python3 -I dashboard/csi.py receive --bind <Mac IP> --port <port> --duration 60`
   records ≥ 1 frame with `source_kind = REAL_CSI` and `record = frame`.
2. **Format:** ≥ 99 % of datagrams parse without a `FrameError`. Every rejection is listed by code,
   and none is `invalid_magic` (that would mean a different protocol).
3. **Plausibility:** `n_antennas` and `n_subcarriers` match the board's documented configuration.
   `frequency_mhz` matches the configured channel. RSSI and noise floor fall within −127..0 dBm.
4. **Continuity:** `seq_gap`/`seq_repeat` warnings are counted. Any loss rate is reported, not hidden.
5. **Rate:** the observed frame rate is reported as measured (RuView documents a 20 packet/s
   acceptance floor; verify, don't assume).
6. **Separation:** synthetic fixtures never appear in `evidence/csi/`. The dashboard's real-frame
   count matches the `REAL_CSI` records.
7. **Reproducibility:** the firmware version, board and capture command are recorded next to the
   evidence.

Even when all of these pass, "compatible" means only that the frames are **received and parsed
correctly**. It says nothing about sensing capability. No presence, pose, breathing, heart-rate
or through-wall claims would follow without separate, validated experiments.

## Not planned

* Deriving CSI-like or vital-sign information from Android RSSI.
* Silent fallback from real input to synthetic data.
* Installing RuView's own services or plugins into this project.
