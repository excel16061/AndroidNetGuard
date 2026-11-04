# Release review update

Reviewed from AndroidNetGuard-0.4.1-20261104T031444Z.zip.

This update corrects portable setup, the architecture version label, test documentation,
telemetry wording, and Python resource cleanup. Raw recorded sessions and earlier static
snapshots are preserved as historical outputs; their labels reflect their generating version.

First/changed and repeated values describe tuple equality only. Actual radio measurement
freshness is unknown. Detector calculations and recorded run outcomes are unchanged.

Independent verification: 21 collector and 95 dashboard tests passed before these edits.
Android build and physical-device experiments were not independently repeated here.

Post-update verification: 21 collector and 95 dashboard tests passed with resource-warning checking enabled.
