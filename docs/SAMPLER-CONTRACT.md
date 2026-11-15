# M4 Firewalla sampler delivery record

The November 14, 2026 user dispatch authorizes one isolated implementation PR and conditionally authorizes installation, five-minute scheduling and restart testing of the new M4 LaunchAgent `com.nibrwr.firewalla-security-sampler`. Activation requires exact paired identity, an existing authorized local health-only path, bounded resources, persistence and recovery, and stable existing Firewalla and Observer operation. The dispatch protects all existing services and permissions, credentials and pairing, Firewalla settings, the M6 installation, draft PR #1 and historical disablements. The rollback scope is the new job, with bounded local evidence preserved.

## Baseline and ownership

Source is `nibrwr/firewalla-mcp` at `c6125caa9c3dae5f33e4306ed4e2e9eca6d6db8f`. GitHub reported `main` at that exact revision. The independent [draft PR #1](https://github.com/nibrwr/firewalla-mcp/pull/1) at `db12f46c8451aa1a0b930929c9e9dfda86c72ee5` remains in its M6 workflow. The new change adds a separate Python sampler and LaunchAgent template. The existing TypeScript package and allowlists remain at their baseline.

## Preflight and runtime result

- **PASS** M4, macOS 27.2 build 26B123, arm64. The current Homebrew Python has the required SQLite heap limit. Apple's system Python on the same Mac returned a zero limit from the same check, so the manifest pins the Homebrew interpreter.
- **PASS** A bounded read returned live health through the existing Firewalla connector. The existing M4 Observer view returned four qualified and responding targets. Its cached projection yielded fresh counts through the new read-only status command.
- **BLOCKED** The installed M4 wrapper directly runs the MCP server under the separate credential owner. Its private transport socket is unavailable to the LaunchAgent's login account. The launch account has no established Firewalla health endpoint at the fixed socket path. The deployed backend chooses the first group, and the live health response supplies no identity. No credential or box values are needed in the record.
- **BLOCKED** Activation and the schedule require an owner-attested box and a verified health socket reachable without modifying the protected existing installation. The source includes the client contract and template. There is no sampler plist or job installed in the login domain. Continuous live collection, production resource measurements and launchd recovery were **NOT RUN**.
- **PASS** The previous collector, host observer and Firewalla bridge remain disabled on M4. The new sampler has no dependency on those jobs.

## Source qualification

The M4 source gate passed 28 tests with Homebrew Python 3.15.2 and SQLite 3.54.1. It exercised the installed-style Python entry point with a temporary Unix peer, SQLite reopening and interrupted writes. The four additional detectors passed four development and ten held-out fictional cases. Test-first checks preceded each behavior slice. The clock, storage error and schema checks were re-run after diagnosis.

The 31-day capped-history simulation made 8,928 fictional reads, with 288 on its last day. It retained 2,016 readings and 3,000 incidents and reported 188 overflow incidents on the last day. SQLite integrity passed. The sampler process peaked at 28,164,096 bytes RSS and committed SQLite state peaked at 225,280 bytes. Its most expensive four adjacent collection calls took 20.08 ms of CPU in total, about 0.0022% of one core over 15 simulated minutes. This includes maximum-length fictional JSON frames and SQLite work and excludes process launch, the rollback journal peak and any live provider work. The separate timed socket test used the actual client path; production resource consumption remains unmeasured.

Coverage includes one request per slot and 288 per trailing day, writer exclusion, pending reservations across process crash and reopening, stale and invalid data, disk-write failure and recovery, Unix peer ownership and exact request, response cap and timeout, plus the four fixture-only detectors with separate development and held-out cases. Run these commands on the M4 from the reviewed checkout.

```sh
/opt/homebrew/bin/python3 -E -s -B -W error::ResourceWarning -m unittest discover -s tests -v
/opt/homebrew/bin/python3 -E -s -B tests/measure_sampler.py
plutil -lint -- deploy/com.nibrwr.firewalla-security-sampler.plist.in
git diff --check c6125caa9c3dae5f33e4306ed4e2e9eca6d6db8f...HEAD
```

The temporary Unix socket tests require permission to bind a local socket. They use fictional data with no Firewalla access. No dependency installation is needed for the sampler. The Node source and lockfile retain the baseline byte for byte. The unchanged TypeScript build was not rerun. Hosted validation is **NOT APPLICABLE**.

The [Operator Guide](SAMPLER-OPERATOR-GUIDE.md) and [Operations Quick Reference](SAMPLER-QUICK-REFERENCE.md) describe the endpoint handshake, resource guards, evidence format and rollback.

## November 15 provider continuation

The continuation authorizes a new credential-owner M4 LaunchDaemon
`com.nibrwr.firewalla-health-provider` and supervised use of its fixed local
socket. The existing sampler retains its conditional schedule authority. Both
service labels and their install paths were vacant in the system and intended
GUI domains. Existing M4 Firewalla and Observer responded before work, and
legacy collector, observer and Firewalla bridge disablements were read back.
The managed worktree resumed the existing PR #2 commit `3edafd3`.

The provider uses the installed pinned Firewalla package for authentication,
crypto, and its fixed ping message. A local admission check requires an exact
Darwin peer UID and request frame. The independent sampler checks the provider
UID and a strict response schema. The provider pins the owner-confirmed target,
binds cloud group selection to the approved group and exactly one paired key,
and uses persisted reservations for both reads and authentication. Production
enrollment enters the protected values only in the credential owner's local
terminal.

**PASS, fixture scope.** Native M4 build and manifest lint. Provider tests used
real Darwin peer credentials and a temporary ACL, bounded bodies, a wrong UID,
an invalid request, concurrency, timeout, SDK crypto with synthetic keys, and
read and replay rules across reopening. The Python suite includes the sampler's
state and recovery checks and hidden enrollment behavior. A real local socket
fixture passed two cross-process sampler reads with both stores reopened and
recorded the expected control-process restart. The examples use synthetic
targets and credentials.

**BLOCKED, production.** Administrator authorization was unavailable for
unattended credential-owner execution. The attended identity inputs and owner
verification therefore remain required. The source was staged as a private
unprivileged bundle. Exact appliance identity, cross-account live socket
access, installation, schedule, restart, rollback, and combined production
resource measurement require live qualification. The sampler operates inside the logged-in
GUI session. Host reboot recovery was not exercised.

Use the [Operator Guide](SAMPLER-OPERATOR-GUIDE.md) for the attended steps and the
[Operations Quick Reference](SAMPLER-QUICK-REFERENCE.md) for recovery and rollback.
