# Project explanation

## Two-minute spoken version

> AndroidNetGuard is a small research harness for testing how an Android phone behaves on the
> network, built so that every result can be checked against evidence.
>
> It has three parts. An Android app runs bounded tests: a USB connectivity check, a direct
> Wi-Fi check, a message sequence, an app-restart check and a user-assisted Wi-Fi off/on
> observation. Every run, step and request gets a random ID, and every event is written to an
> append-only log with the phone's monotonic time. On the Mac, a separate collector receives
> the test messages and stores exactly what arrived. A third program, a localhost-only
> dashboard, pulls the app's log over USB and matches the two sides by request ID.
>
> The main design rule is that the phone's own verdict is never enough. A test passes only if
> the app got an acknowledgement carrying its own ID *and* the collector holds exactly one
> matching record, under the test's preconditions. For a Wi-Fi test, that means Android reported
> the transport as exactly "wifi" and the record didn't arrive via loopback. When evidence is
> missing or the preconditions aren't met, the result is "inconclusive", not "failed".
> Harness problems are reported separately as "execution error". I also never subtract phone
> and Mac timestamps, because their clocks aren't synchronised.
>
> I validated it on a real Android 13 phone: USB and direct Wi-Fi delivery, sequences of up to
> 50 messages, recovery after an app restart, and rotation in the middle of a run. Writing the
> tests found and fixed real bugs, such as an unbounded thread pool in the collector and a
> start-up race that could have closed a fresh run. The phone itself also exposed a defect: the
> app never noticed losing Wi-Fi because it called an Android API from inside a callback, which
> the documentation warns against. I fixed it and re-verified the disconnect/reconnect check.
> Cancelling from the UI is verified too: the run stopped 4.5 ms after the tap and started nothing
> afterwards. That check exposed a small evidence gap (the interrupted step isn't closed), which
> the analysis now handles and which I've documented.
>
> I've also added an opt-in wireless observation mode. It records the Wi-Fi telemetry Android
> exposes (RSSI, frequency, link speed) and runs a simple rule-based detector for signal
> degradation and connectivity loss. It's explicitly *not* CSI. Moving the phone away from the
> router showed up as a drop to about −75 dBm, and turning Wi-Fi off showed up as a 51-second
> outage.
>
> What it does *not* do yet matters just as much. There are no security-policy tests and no AI
> model. The next step is to define real policies with an independent oracle, then compare
> fixed, random and model-guided test selection on this evidence layer.

## Likely questions and accurate answers

**What is the research contribution?**
At this stage it's an engineering contribution: a validated evidence and execution layer with
explicit rules for when a result counts. It isn't yet a research result. The research question,
selection strategies under an evidence requirement, is designed but not evaluated.

**Does it detect security vulnerabilities?**
No. All five tests are connectivity or reliability checks. No security policy or oracle is
implemented, so the dashboard lists security-policy tests as "Not implemented".

**Is the RSSI work channel state information (CSI)?**
No. Android doesn't give apps CSI. The app records `ANDROID_WIFI_TELEMETRY`: coarse,
platform-reported RSSI, frequency and link speed, often repeated from a cache. There's an optional
parser for external CSI hardware (RuView's ADR-018 frame format, pinned and reviewed), but no
hardware is connected and no compatibility, presence or vital-sign sensing is claimed.

**What did the wireless sessions show?**
In one stationary, one moved and one Wi-Fi off/on session, the detector flagged a sustained
≈ 25 dB drop and a link-speed drop while the phone was moved, and an outage with 11 failed probes
while Wi-Fi was off. All LAN probes still succeeded at −75 dBm. These are observations from one
phone. They don't explain causes.

**Where is the AI?**
There's none yet. Model-guided selection is planned as one arm of a comparison against fixed
and random selection, using the same evidence the dashboard sees.

**How do you know traffic really went over Wi-Fi?**
Two pieces of evidence must agree. Android reports the default network as exactly `wifi`
before and after the request, and the collector sees the phone's Wi-Fi LAN address as the peer,
not loopback. Neither is a perfect independent proof. Android's transport report is trusted,
and that's listed as a limitation.

**Why not just trust HTTP 200 on the phone?**
A 200 says only that something answered. The app also requires the ack to echo its request ID,
and the dashboard requires the collector's stored record. This catches ID mismatches, duplicates
and records that were acknowledged but not saved, and there are tests for each case.

**What does "inconclusive" mean, and isn't it a cop-out?**
It means the evidence can't support pass or fail: a precondition wasn't met, the run was
cancelled, the user didn't act in time, or the server evidence is missing. Separating it stops
missing data from being reported as failure or as "blocked". The rules are documented and
unit-tested.

**How are timestamps handled?**
Durations come only from one device's monotonic clock (`elapsedRealtimeNanos` on the phone,
`monotonic_ns` on the Mac). Wall-clock times are shown for reference and never subtracted
across devices.

**What did validation find?**
Real defects, all fixed with tests:
* the collector accepted non-canonical IDs and had no thread or storage bounds;
* the analysis flagged reused IDs in only one run;
* a unit test really waited five minutes;
* a start-up race could close a fresh run;
* public exports didn't scrub serials or paths.

The full list is in HANDOFF.md and VALIDATION.md.

**How general are the results?**
Not very. There's one device, one network and an operator in the loop. They show the harness
works as designed, not anything about Android in general.

**How did you build it?**
I designed and directed the project, ran every device test, and reviewed the results. An AI
coding assistant (Claude Code) helped substantially with implementation, tests and
documentation under my direction. All claims are checked against the saved evidence.

**What would you do next?**
Define one or two concrete network policies with an oracle on the collector side, add them as
security-policy tests with independent verification, then run the fixed/random/model-guided
comparison with equal budgets and a pre-registered analysis.
