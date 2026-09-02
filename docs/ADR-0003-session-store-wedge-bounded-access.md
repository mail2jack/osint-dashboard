# ADR-0003 — Session-store wedge: bounded access + ownership enforcement

- Status: **Proposed (draft for approval — PR 2 / P2)**
- Date: 2026-08-29
- Deciders: codebase verification (OpenCode P2 investigation), issue #88
- Related docs: `docs/deploy-plan-invoice-numbering-integrity.md` (P1,
  unrelated), issue #88 "P2: session-store / flask_session test-flakiness"

> **Scope of this PR:** production evidence, root-cause ADR, a small bounded
> filesystem-cache backend, regression tests and a doctor/observability check.
> No production change is deployed from this PR; rollout is a separate,
> explicitly approved step. Strictly unrelated to invoice numbering (P1).

## Context

### Observed symptom (production, 2026-08-29)

During the P1 deploy window the `doctor` preflight gate failed: `Flask health`
FAIL (≈10s). The app (gunicorn **sync**, `--workers 1`, systemd unit
`/etc/systemd/system/osint-dashboard.service`, `User=osint`) recovered for
1–2 min after a service restart, then wedged again within the same minute.
Journal: repeated

```
WARNI [root] Exception raised while handling cache file
  '<repo>/flask_session/79bc207e…'
Traceback (most recent call last): … OSError
```

cadence ≈ every 16s. The wedge returned post-deploy too (48 OSErrors in 10
min after deploy; a stale client connection held open via the nginx proxy).
`access.log` stayed empty (observability gap, separate follow-up).

### Evidence

1. The single offender object is one file,
   `flask_session/79bc207ee04a581ab2348ca1f3494e64cd0ec36128735b8c6bc85ddd8fe1938c`,
   `root:root` mode `0600`, size 19, mtime during the maintenance window.
   It is the **only** non-`osint` file in the session store (full scan).
   The gunicorn worker runs as `osint`; `open(..., 'rb')` on it yields EACCES
   (`PermissionError`) and even `dd` as `osint` is denied.
2. How it became root-owned: server-side writes are always done by the worker
   (osint) via `tempfile.mkstemp` + `os.replace`, so the app itself cannot
   create a root file. The entry must have been created by a **root-euid
   process running maintenance tooling** that wrote a session-store entry
   directly (or via an HTTP request whose process euid was root). Hypothesis
   labelled as such; the precise writer was not pinned down.
3. `cachelib==0.17.0` `FileSystemCache._run_safely` retries `PermissionError`
   on `open` / `os.replace` / `os.chmod` with exponential backoff for up to
   `max_sleep_time = 10.0` s, then `_safe_stream_open` raises `OSError`
   (caught by `get`, which returns `None`). Net effect: **one request touching
   that entry blocks ~10 s**. With `--workers 1` every request (including
   `/health`) queues behind it → the observed 10 s health timeout, the OSError
   storm, and the "restart fixes it, then it returns" pattern (the file
   survives restarts).
4. Session config (production): `flask-session==0.8.0`, `SESSION_TYPE=cachelib`
   (no `REDIS_URL`), `SESSION_CACHELIB = FileSystemCache(cache_dir=<repo
   root>/flask_session, threshold=5000, default_timeout=28800)`
   (`app.py:210-219`), serialization `json`, `SESSION_PERMANENT=True`. Session
   store at repo root; ~2034 entries.

### Decision drivers

- **D1 — Bound the stall.** No filesystem session entry may block the app for
  10 s. 10 s of exponential backoff is meant for transient SMB/NTFS
  `PermissionError` races between concurrent writers (see cachelib docstring);
  a *persistent* EACCES (ownership skew) must be a fast miss, not a hang.
- **D2 — Enforce store hygiene.** Ownership/permission of session files must
  be validated continuously (doctor), so the skew never silently re-occurs.
- **D3 — No scope creep.** Redis backend, worker-count tuning, access-log
  repair and DB changes are explicitly out of scope; the wedge must be fixed
  without touching invoice numbering or any other subsystem.

## Decision

1. **Use `cms.session_cache.BoundedFileSystemCache`** (new, ~40 lines) as the
   `SESSION_CACHELIB` backend instead of stock `FileSystemCache`. It is a
   subclass whose `_run_safely` keeps the same semantics but caps
   `max_sleep_time` at `0.05` s (module constant `SESSION_CACHE_MAX_WAIT`).
   Keying, serialization, pruning, threshold and file modes are untouched;
   `BoundedFileSystemCache.max_wait_time < 1.0` is asserted in tests.
   - Read path: an unreadable/foreign entry → fast `None` (new session).
   - Write path: `os.replace`/`os.chmod` failures stay bounded (fast `False`).
2. **Doctor hygiene check** `check_flask_session_contents` (new): scan every
   file in `flask_session/`; any entry with `uid != service uid` or
   `mode != 0600` is listed; non-dry mode **removes** foreign/unreadable
   entries (a session store re-creates entries on demand; re-owning foreign
   session data would leak another user's stored session to the app user and
   is rejected). Registered between `flask_session/ writable` and the rest.
   `check_flask_session` (directory-level) remains as-is.
3. **App-level hardening not taken:** the app cannot `chown` root files (it
   runs as `osint`), so prevention lives in ops (doctor + one-time remediation)
   and resilience (D1). This trade-off is intentional and documented.

### Why not

- Increasing `--workers` or switching to `gthread`: does not remove the 10 s
  block; it only shortens the queue behind it. Out of scope (D3).
- Redis session backend: new infrastructure dependency, larger blast radius.
  Can be revisited separately.
- Deleting ALL `flask_session/` on boot: needless logout storms; the fix
  should degrade gracefully, not nuke user sessions.

## Regression tests

`tests/test_session_cache.py` (5 tests, serial `-n 0` as CI):

- `test_bounded_retry_is_fast_on_permission_error` — monkeypatches
  `builtins.open` to raise `PermissionError` for the target file; asserts the
  read is a miss and completes in `< 0.75 s` (privilege-independent).
- `test_roundtrip_still_works` — `set`/`get`/`delete` unaffected.
- `test_end_to_end_broken_session_file_keeps_app_healthy` — Flask+
  `flask_session` app; after a session file is `chmod 000`, replaying the same
  session still returns `200` in `< 1.5 s` (skipped when running as root, where
  `chmod 000` does not EACCES a root process).
- `test_doctor_check_flags_foreign_session_entries` — non-0600 file is
  reported and removed; 0600 file is kept.
- `test_bounded_cache_is_subclass_of_stock` — subclass + bound assertions.

## Rollout plan (separately approved, no DB changes)

1. `sudo -u osint` preflight: `doctor --dry-run` must pass, including the new
   ownership check (it will list the offender entry).
2. Remediation of the known offender (once): remove/`chown osint:osint` the
   single root-owned entry so the current stuck cookie resolves to a fresh
   session. Pure ops step; idempotent.
3. Deploy code (PR merge) via `production_rollout.sh --confirm DEPLOY-MASTER`.
4. Post-checks: `doctor` (now 22 checks incl. ownership) green; `journalctl`
   0× `OSError` over 10 min (previously ~48); `/health` < 1 s sustained.
5. Monitoring window (short working day) counts `OSError`/slow-request
   occurrences to close the P2 loop; observability follow-up (empty
   `access.log`) tracked separately.

> **Window-scope note:** the P2 session-store/health loop closes on a
> **short working day** window, *not* 24 hours. This is distinct from the
> **Gunicorn 2-worker canary**, which has its own explicit **24 h
> uninterrupted** observation window (see
> `docs/PLAN-GUNICORN-CONCURRENCY-TUNING.md`, phase 1). Do not conflate the
> two: the P2 loop may be formally green on the short-working-day window
> before the canary's 24 h window has completed.

## Follow-ups (out of scope here)

- Full `/health` (without `?quick=1`) runs sequential external HTTP checks
  (rdw/kadaster/hibp, `timeout=5` each) → ~8s latency. By design; doctor
  correctly uses `/health?quick=1` (0.1s). Worth a future annotation on the
  health endpoint so probes don't accidentally hit the slow path.
- `access.log` has been 0 bytes since May 27 despite
  `--access-logfile …/access.log`; investigate + logrotate.
- Issue #88 test-flakiness under xdist parallel (nondeterministic failures in
  the shared session-scoped `app` fixture) — separate from the prod wedge;
  CI is already serial (`-n 0`).
- Optional later: Redis session backend for zero-cleanup support.