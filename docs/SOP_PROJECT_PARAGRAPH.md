# Statement-of-purpose paragraph

> In AndroidNetGuard, I built a small research harness for testing Android network behaviour
> on which every verdict has to be supported by evidence. It has an Android app that runs
> bounded connectivity and reliability tests, a separate collector on a laptop that records what
> it actually receives, and a localhost dashboard that correlates the two by request ID. Its
> rules separate "passed" and "failed" from "inconclusive" and "execution error" instead of
> trusting the phone's own result. I validated it on a physical Android 13 device, covering USB
> and direct Wi-Fi delivery, multi-message sequences, recovery after an app restart, screen
> rotation during a run, and Wi-Fi loss and recovery. I also added an opt-in mode that records
> the phone's Wi-Fi signal telemetry with a transparent baseline for degradation and outage
> alerts, and fixed the defects that testing on the device exposed. The project doesn't yet
> include security-policy tests or machine-learning components. My next step is to define
> policies with an independent oracle and compare fixed, random and model-guided test selection
> on this evidence layer, a question I'd like to pursue in doctoral research on trustworthy
> evaluation of mobile systems.

_Notes for adapting it: keep the device-validation clause in line with
`docs/VALIDATION_SUMMARY.md`. The reconnect and UI-cancellation checks are both verified on build 0.4.1. Mention AI-assisted development wherever the
application asks about tools or collaboration._
