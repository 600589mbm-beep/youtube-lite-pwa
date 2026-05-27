# Fix-It Jimmy channel setup (separate auth profile)

Target channel: **Fix-It Jimmy** — `UCKP48Yc9xDuPHudt9qGWRMA`
(https://studio.youtube.com/channel/UCKP48Yc9xDuPHudt9qGWRMA)

This is a **new brand/channel**, distinct from both the crypto Shorts channel and
the earlier kids channel (`UCGXmL8spGRle5y0k2bvNnUw`). It gets its own OAuth token
under the **`fixit`** profile WITHOUT touching the crypto or kids tokens.

Google Cloud credentials project: `gen-lang-client-0563502571`.

## How tokens are stored

| Profile | Token file | Notes |
|---|---|---|
| `crypto` (default) | `data/youtube-auth.crypto.json` (falls back to legacy `data/youtube-auth.json`) | the existing Shorts channel — unchanged |
| `kids` | `data/youtube-auth.kids.json` | the kids/longform channel `UCGXmL8spGRle5y0k2bvNnUw` |
| `fixit` | `data/youtube-auth.fixit.json` | created when you connect Fix-It Jimmy |

- Default profile = `crypto` (override with `YOUTUBE_CHANNEL_PROFILE` / `YOUTUBE_AUTH_PROFILE`).
- Profile names match `^[a-z0-9_-]{1,32}$`; `fixit` is handled generically — no code change needed.
- Token files are git-ignored (`data/*`) and are never committed.
- Each publish job carries a `profile`; the queue worker loads that profile's
  token. **A fixit job queued before the channel is connected stays `queued`
  (it does NOT fail) and uploads automatically once connected.**

## Connect Fix-It Jimmy in Chrome (you do this)

1. In Chrome, sign in to the Google account that owns channel
   `UCKP48Yc9xDuPHudt9qGWRMA` (the one where Studio opens to Fix-It Jimmy).
2. Open the fixit connect URL on the app host (`APP_URL=https://191-101-2-203.sslip.io`):

   ```
   https://191-101-2-203.sslip.io/auth/youtube?profile=fixit&returnTo=/youtube
   ```

   This base URL must match `YOUTUBE_REDIRECT_URI`
   (`https://191-101-2-203.sslip.io/auth/youtube/callback`).
3. On Google's consent screen, **pick the Fix-It Jimmy channel / brand account**
   (`UCKP48Yc9xDuPHudt9qGWRMA`) if asked which channel to grant access to. This
   is the critical step — granting the wrong channel writes the wrong token.
4. Approve the `youtube.upload` scope. You'll be redirected back with `connected=1`.
5. Verify it landed in the fixit profile (no secrets printed):

   ```
   curl -s 'http://localhost:3456/api/youtube/status?profile=fixit' \
     | python3 -c 'import sys,json;d=json.load(sys.stdin);print("fixit connected:",d["connected"],"| profiles:",d["profiles"])'
   ```

   Expect `fixit connected: True` and `fixit` in the profiles list. The crypto
   channel is untouched: `?profile=crypto` should still report connected.

> Connecting `fixit` writes only `data/youtube-auth.fixit.json`. It never reads or
> overwrites the crypto or kids tokens.

## If Google blocks OAuth ("app not verified" / "access blocked")

If the OAuth consent screen for project `gen-lang-client-0563502571` is in
**Testing** mode, only added **test users** can authorize. Symptom: "Access blocked:
<app> has not completed the Google verification process" or error 403 `access_denied`.

Fix: add the Google account that owns Fix-It Jimmy as a test user.

- **Tester email needed:** the Google account you sign in with at step 1 — i.e. the
  account that owns `UCKP48Yc9xDuPHudt9qGWRMA`. If that channel is a Brand Account,
  add the **owner Google account's** email (the personal Google login that manages
  the brand), not the channel name.
- **Cloud Console page:** Google Cloud Console → select project
  `gen-lang-client-0563502571` → **APIs & Services → OAuth consent screen →
  Audience / Test users → + Add users** → enter the email → Save.
  Direct: https://console.cloud.google.com/apis/credentials/consent?project=gen-lang-client-0563502571

## Uploading to Fix-It Jimmy (later, not now)

- No pipeline lane currently targets `fixit`. To publish here, a job's payload must
  set `profile = "fixit"` (the crypto Shorts lane sends no profile → stays `crypto`;
  the longform lane targets `YOUTUBE_KIDS_PROFILE`). Wire a lane to `fixit` only when
  Fix-It Jimmy content exists.
- This is **not** a made-for-kids channel by default; do not set `madeForKids` unless
  the specific content requires it.

## Scopes

- Upload uses `https://www.googleapis.com/auth/youtube.upload` (same as crypto/kids).
- For future analytics, re-auth this profile additively with `youtube.readonly` +
  `yt-analytics.readonly` later. **Not required now.**
