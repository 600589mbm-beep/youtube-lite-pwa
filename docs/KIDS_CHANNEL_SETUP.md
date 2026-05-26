# Kids channel setup (separate auth profile)

Target kids/longform channel: **`UCGXmL8spGRle5y0k2bvNnUw`**
(https://studio.youtube.com/channel/UCGXmL8spGRle5y0k2bvNnUw)

The app now supports **per-channel auth profiles** so the kids channel gets its
own OAuth token WITHOUT touching the working crypto channel.

## How tokens are stored

| Profile | Token file | Notes |
|---|---|---|
| `crypto` (default) | `data/youtube-auth.crypto.json` (falls back to legacy `data/youtube-auth.json`) | the existing Shorts channel — unchanged |
| `kids` | `data/youtube-auth.kids.json` | created when you connect the kids channel |

- Default profile = `crypto` (override with `YOUTUBE_CHANNEL_PROFILE` / `YOUTUBE_AUTH_PROFILE`).
- Token files are git-ignored (`data/*`) and are never committed.
- Each publish job carries a `profile`; the queue worker loads that profile's
  token. **A kids job queued before the kids channel is connected stays
  `queued` (it does NOT fail) and uploads automatically once connected.**

## Connect the kids channel in Chrome (you do this)

1. In Chrome, sign in to the Google account that owns channel
   `UCGXmL8spGRle5y0k2bvNnUw` (the one where Studio already opens for you).
2. Open the kids connect URL on the app host:

   ```
   http://localhost:3456/auth/youtube?profile=kids&returnTo=/youtube
   ```

   Use whatever base URL you normally reach the app at (the same host/tunnel you
   used to connect the crypto channel — it must match `YOUTUBE_REDIRECT_URI`).
3. On Google's consent screen, **pick the kids channel / brand account**
   (`UCGXmL8spGRle5y0k2bvNnUw`) if asked which channel to grant access to. This
   is the critical step — granting the wrong channel writes the wrong token.
4. Approve the `youtube.upload` scope. You'll be redirected back with
   `connected=1`.
5. Verify it landed in the kids profile (no secrets printed):

   ```
   curl -s 'http://localhost:3456/api/youtube/status?profile=kids' \
     | python3 -c 'import sys,json;d=json.load(sys.stdin);print("kids connected:",d["connected"],"| profiles:",d["profiles"])'
   ```

   Expect `kids connected: True` and `kids` in the profiles list. The crypto
   channel is untouched: `?profile=crypto` should still report connected.

> Connecting `kids` writes only `data/youtube-auth.kids.json`. It never reads or
> overwrites the crypto token.

## Uploading to the kids channel (later, not now)

- The longform/ViMax lane targets the kids profile automatically:
  `pipeline/longform_pipeline.py` sets `profile = YOUTUBE_KIDS_PROFILE` (default
  `kids`) on the `RenderedVideo`, and it is build-only unless `--publish` AND
  `LONGFORM_PUBLISH=1`. ViMax itself is still inert (see `docs/VIMAX_LONGFORM.md`).
- **Made for Kids (COPPA):** `RenderedVideo.made_for_kids` flows to the publish
  payload as `madeForKids`, and the Node uploader forwards it as
  `status.selfDeclaredMadeForKids` on `videos.insert`. kids_show episodes are
  marked made-for-kids by default.
- The crypto Shorts pipeline is unchanged: it sends no `profile`, so it uses the
  default `crypto` profile.

## Scopes

- Upload uses `https://www.googleapis.com/auth/youtube.upload` (same as crypto).
- For future kids-channel analytics, you'll later re-auth that profile additively
  with `youtube.readonly` + `yt-analytics.readonly` (see `docs/ANALYTICS.md`).
  **Not required now** — do not reauth for analytics yet.
