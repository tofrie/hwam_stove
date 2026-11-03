# A07.3 — private file download (1.0.0rc12)

Base: rc11 `c0c37e97dad7fc47f0bc535f9753f9eeaa1c7f53`.
No collection, Store/checkpoint, entity, prediction, benchmark, statistics,
controller request or dependency change. H01B remains OPEN.

## Security decision

HA 2026.10.4 `HomeAssistantView` defaults to authentication; its official
`async_sign_path` supports short-lived authenticated downloads. Verified against
the installed HA source and actual authentication middleware, not a mock auth flag.
[HA authentication documentation](https://contributors.home-assistant.io/core/internals/auth-api/).

Do not place private observation files in `/config/www`, which is public through
`/local`. The service instead writes under HA's config directory:
`.storage/hwam_observation_exports/` (normally `/config/.storage/...` on HAOS).
This is a separate export directory, not a change to any HA Store file.

An explicit action with an active administrator user context is required. The
signature is bound to an existing refresh token belonging to that user; no content
user, new credentials or long-lived API token is created. The download view checks
HA authentication and administrator status. Unsigned/invalid/expired requests
are refused. An ordinary non-admin bearer token is insufficient.

The returned `download_path` includes a five-minute **bearer capability**. Anyone
holding that entire link can use it until expiry, so do not share it or put it
in logs/notifications. Token revocation/user deletion/HA restart invalidates it.
Use the normal local HA origin; no automatic cloud transmission occurs. HA remote
access, if independently configured, may also serve an authorized download.
No credentials are included in the JSON file itself.

## Use

In Developer Tools → Actions, as an administrator:

```yaml
action: hwam_stove.export_observations
data: {}
```

With multiple HWAM entries, use the existing config-entry selector. The small
response has exactly `filename`, `records`, `bytes`, `download_path`. Copy the
complete download path (including `?authSig=...`) after your normal HA base URL
and open it within five minutes. Save the browser's JSON download locally.
A new action creates a new snapshot/link; failed/expired downloads do not poll the
controller. A direct API client can alternatively use an admin Authorization header.

Names include UTC, e.g. `hwam_observations_20261103T120000Z_<random>.json`.
The random suffix prevents same-second collisions and contains no device identity.
`records` includes observations and explicitly marked gap rows. JSON preserves all
currently retained rows, including those still buffered in memory; existing
checkpoint metadata identifies their durability. No deduplication or truncation.
The original 7-day/20,000-record collection retention remains unchanged.

## File lifecycle and failures

- Directory 0700, files 0600; owned by the HA process user. Existing unsafe modes,
  symlinks and hard links are rejected, never silently chmodded or followed.
- Strict filename pattern and directory descriptors prevent traversal. Downloads
  read an already-opened regular file, bounded to 8 MB, not a later path reopen.
- JSON serialization, atomic file replacement, fsync, retention and download reads
  run in HA's existing executor. No new threads, timers or background polling.
- One export lock serializes file operations; repeated cancellation waits for owned
  disk work before releasing it. Partial temporary files are removed on errors;
  recognized orphan temporaries after a crash are removed on the next explicit call.
- At most five completed files, at most 8 MB each. Oldest files are evicted for a
  new export. Files aged >=24 hours are removed on the next export/download. There
  is no autonomous cleanup timer, so an idle installation can retain old files
  longer on disk; their signed links still expire after five minutes.
- Responses use attachment, JSON, no-store, nosniff and no-referrer headers. HTTP
  errors reveal no file contents or filesystem paths. Action errors are sanitized.
- Export failure does not change recorder health, data, checkpoint or model state.
  A download already in progress may finish after signature expiry (normal HA).
- HA backups can contain the private export directory; protect those backups too.

## Manual installation / rollback

1. Make a current HA backup and save the rc11 integration directory outside
   `custom_components`. Prevent HACS overwriting the private fork.
2. Verify the delivered ZIP SHA256. Stop Core, replace only
   `custom_components/hwam_stove/`, then start Core manually.
3. Confirm rc12, dependency `saynwerk-pystove==0.3.0rc3`, the same 49 entities,
   healthy collection and unchanged A02/A04 counters. Do not recreate the entry.
4. Run the export action, open the returned path within five minutes, confirm the
   downloaded JSON and row count. No stove action is needed.
5. On new errors stop testing, preserve evidence, never reset a Store.

Rollback: stop Core, restore saved rc11 integration code, start Core. Do not change
Stores or dependency. The old large JSON response limitation returns. Private
export files may remain; rc11 neither serves nor maintains them. No schema
migration is needed. Never restore `.storage` while HA is running.
