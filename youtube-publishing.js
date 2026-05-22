import express from 'express';
import { google } from 'googleapis';
import crypto from 'node:crypto';
import { createReadStream } from 'node:fs';
import { access, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

const YOUTUBE_SCOPE = 'https://www.googleapis.com/auth/youtube.upload';
const STATE_TTL_MS = 10 * 60 * 1000;
const QUEUE_INTERVAL_MS = 30 * 1000;

export function createYouTubeRouter({ baseDir, appUrl }) {
  const router = express.Router();
  const dataDir = path.join(baseDir, 'data');
  const uploadsDir = path.join(baseDir, 'uploads');
  const tokenPath = path.join(dataDir, 'youtube-auth.json');
  const queuePath = path.join(dataDir, 'youtube-publish-queue.json');
  const defaultRedirectUri = `${stripTrailingSlash(appUrl || 'http://localhost:3456')}/auth/youtube/callback`;

  const config = {
    clientId: process.env.YOUTUBE_CLIENT_ID?.trim() || '',
    clientSecret: process.env.YOUTUBE_CLIENT_SECRET?.trim() || '',
    redirectUri: process.env.YOUTUBE_REDIRECT_URI?.trim() || defaultRedirectUri,
    defaultPrivacyStatus: normalizePrivacyStatus(process.env.YOUTUBE_DEFAULT_PRIVACY_STATUS || 'private'),
    defaultCategoryId: process.env.YOUTUBE_DEFAULT_CATEGORY_ID?.trim() || '22',
  };

  const stateCache = new Map();
  let queueBusy = false;
  let queueAgain = false;
  let queueTimer = null;

  void ensureStorage();
  startQueueLoop();

  router.get('/auth/youtube', function (req, res) {
    try {
      if (!config.clientId || !config.clientSecret) {
        return res.status(500).send(buildHtmlError('YouTube OAuth is not configured. Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env first.'));
      }

      const returnTo = normalizeReturnTo(req.query.returnTo);
      const state = crypto.randomUUID();
      stateCache.set(state, {
        createdAt: Date.now(),
        returnTo,
      });
      cleanupStateCache();

      const oauth2Client = createOauthClient();
      const authorizationUrl = oauth2Client.generateAuthUrl({
        access_type: 'offline',
        scope: [YOUTUBE_SCOPE],
        include_granted_scopes: true,
        prompt: 'consent',
        state,
      });

      return res.redirect(authorizationUrl);
    } catch (error) {
      return res.status(500).send(buildHtmlError(error instanceof Error ? error.message : 'Unable to start YouTube OAuth.'));
    }
  });

  router.get('/auth/youtube/callback', async function (req, res) {
    try {
      const error = typeof req.query.error === 'string' ? req.query.error : '';
      if (error) {
        return res.redirect(`/youtube?auth=error&message=${encodeURIComponent(error)}`);
      }

      const code = typeof req.query.code === 'string' ? req.query.code : '';
      const state = typeof req.query.state === 'string' ? req.query.state : '';
      const stateEntry = consumeState(state);

      if (!code) {
        return res.redirect('/youtube?auth=missing_code');
      }

      if (!stateEntry) {
        return res.redirect('/youtube?auth=invalid_state');
      }

      const oauth2Client = createOauthClient();
      const tokenResponse = await oauth2Client.getToken(code);
      const receivedTokens = sanitizeTokens(tokenResponse.tokens || {});
      const existingBundle = await loadTokenBundle();
      const mergedTokens = sanitizeTokens({
        ...(existingBundle?.tokens || {}),
        ...receivedTokens,
      });

      if (!mergedTokens.refresh_token && existingBundle?.tokens?.refresh_token) {
        mergedTokens.refresh_token = existingBundle.tokens.refresh_token;
      }

      if (!mergedTokens.refresh_token) {
        return res.redirect('/youtube?auth=missing_refresh_token');
      }

      await saveTokenBundle(mergedTokens);
      kickQueueProcessor();

      const separator = stateEntry.returnTo.includes('?') ? '&' : '?';
      return res.redirect(`${stateEntry.returnTo}${separator}connected=1`);
    } catch (error) {
      return res.redirect(`/youtube?auth=failed&message=${encodeURIComponent(error instanceof Error ? error.message : 'OAuth failed.')}`);
    }
  });

  router.get('/api/youtube/status', async function (_req, res) {
    try {
      const [tokenBundle, queue] = await Promise.all([loadTokenBundle(), loadQueue()]);
      const queued = queue.filter(function (job) {
        return job.status === 'queued';
      }).length;
      const publishing = queue.filter(function (job) {
        return job.status === 'publishing';
      }).length;
      const published = queue.filter(function (job) {
        return job.status === 'published';
      }).length;

      return res.json({
        ok: true,
        configured: Boolean(config.clientId && config.clientSecret),
        connected: Boolean(tokenBundle?.tokens?.refresh_token),
        tokenSource: tokenBundle?.source || null,
        redirectUri: config.redirectUri,
        authScope: YOUTUBE_SCOPE,
        defaultPrivacyStatus: config.defaultPrivacyStatus,
        defaultCategoryId: config.defaultCategoryId,
        uploadDirectory: path.relative(baseDir, uploadsDir).replace(/\\/g, '/'),
        connectUrl: '/auth/youtube?returnTo=/youtube',
        publishUrl: '/api/youtube/publish',
        queueCount: queue.length,
        queuedCount: queued,
        publishingCount: publishing,
        publishedCount: published,
        lastJob: summarizeJob(queue[queue.length - 1] || null, baseDir),
      });
    } catch (error) {
      return res.status(500).json({
        ok: false,
        error: error instanceof Error ? error.message : 'Unable to load YouTube status.',
      });
    }
  });

  router.get('/api/youtube/queue', async function (_req, res) {
    try {
      const queue = await loadQueue();
      return res.json({
        ok: true,
        jobs: queue.map(function (job) {
          return summarizeJob(job, baseDir);
        }),
      });
    } catch (error) {
      return res.status(500).json({
        ok: false,
        error: error instanceof Error ? error.message : 'Unable to load publish queue.',
      });
    }
  });

  router.post('/api/youtube/publish', async function (req, res) {
    try {
      const payload = normalizePublishPayload(req.body || {}, config, baseDir, uploadsDir);
      const job = {
        id: crypto.randomUUID(),
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        attempts: 0,
        status: 'queued',
        lastError: null,
        payload,
      };

      const queue = await loadQueue();
      queue.push(job);
      await saveQueue(queue);
      kickQueueProcessor();

      return res.status(202).json({
        ok: true,
        queued: true,
        job: summarizeJob(job, baseDir),
        message: payload.publishAt ? 'Queued for upload and scheduled on YouTube.' : 'Queued for upload.',
      });
    } catch (error) {
      return res.status(400).json({
        ok: false,
        error: error instanceof Error ? error.message : 'Unable to queue publish request.',
      });
    }
  });

  router.post('/api/youtube/disconnect', async function (_req, res) {
    try {
      const tokenBundle = await loadTokenBundle();
      await clearTokenBundle();
      return res.json({
        ok: true,
        disconnected: true,
        tokenSource: tokenBundle?.source || null,
        warning: tokenBundle?.source === 'env' ? 'YOUTUBE_REFRESH_TOKEN is still configured in the environment.' : null,
      });
    } catch (error) {
      return res.status(500).json({
        ok: false,
        error: error instanceof Error ? error.message : 'Unable to disconnect YouTube.',
      });
    }
  });

  return router;

  function createOauthClient() {
    if (!config.clientId || !config.clientSecret) {
      throw new Error('YouTube OAuth is not configured.');
    }

    return new google.auth.OAuth2(config.clientId, config.clientSecret, config.redirectUri);
  }

  async function ensureStorage() {
    await mkdir(dataDir, { recursive: true });
    await mkdir(uploadsDir, { recursive: true });
  }

  function startQueueLoop() {
    if (queueTimer) {
      return;
    }

    queueTimer = setInterval(function () {
      void processQueue();
    }, QUEUE_INTERVAL_MS);

    if (typeof queueTimer.unref === 'function') {
      queueTimer.unref();
    }
  }

  function kickQueueProcessor() {
    if (queueBusy) {
      queueAgain = true;
      return;
    }

    void processQueue();
  }

  async function processQueue() {
    if (queueBusy) {
      queueAgain = true;
      return;
    }

    queueBusy = true;

    try {
      await ensureStorage();
      const tokenBundle = await loadTokenBundle();

      if (!tokenBundle?.tokens?.refresh_token) {
        return;
      }

      const queue = await loadQueue();
      let changed = false;

      for (const job of queue) {
        if (job.status !== 'queued') {
          continue;
        }

        job.status = 'publishing';
        job.attempts = Number(job.attempts || 0) + 1;
        job.updatedAt = new Date().toISOString();
        job.lastAttemptAt = job.updatedAt;
        changed = true;
        await saveQueue(queue);

        try {
          const result = await uploadJob(job, tokenBundle);
          job.status = 'published';
          job.result = result;
          job.lastError = null;
          job.completedAt = new Date().toISOString();
          job.updatedAt = job.completedAt;
          changed = true;
          await saveQueue(queue);
        } catch (error) {
          job.status = 'failed';
          job.lastError = error instanceof Error ? error.message : 'Upload failed.';
          job.completedAt = new Date().toISOString();
          job.updatedAt = job.completedAt;
          changed = true;
          await saveQueue(queue);
        }
      }

      if (!changed) {
        return;
      }
    } finally {
      queueBusy = false;

      if (queueAgain) {
        queueAgain = false;
        void processQueue();
      }
    }
  }

  async function uploadJob(job, tokenBundle) {
    await ensureStorage();

    const oauth2Client = createOauthClient();
    oauth2Client.setCredentials(tokenBundle.tokens);

    const youtube = google.youtube({
      version: 'v3',
      auth: oauth2Client,
    });

    const videoPath = job.payload.videoPath;
    await access(videoPath);

    const response = await youtube.videos.insert({
      part: 'snippet,status',
      requestBody: {
        snippet: {
          title: job.payload.title,
          description: job.payload.description,
          tags: job.payload.tags,
          categoryId: job.payload.categoryId,
        },
        status: {
          privacyStatus: job.payload.privacyStatus,
          ...(job.payload.publishAt ? { publishAt: job.payload.publishAt } : {}),
        },
      },
      media: {
        mimeType: detectVideoMimeType(videoPath),
        body: createReadStream(videoPath),
      },
    });

    const videoId = response.data?.id || '';
    if (!videoId) {
      throw new Error('YouTube did not return a video ID.');
    }

    let thumbnailUploaded = false;
    if (job.payload.thumbnailPath) {
      const thumbnailPath = job.payload.thumbnailPath;
      await access(thumbnailPath);
      await youtube.thumbnails.set({
        videoId,
        media: {
          mimeType: detectThumbnailMimeType(thumbnailPath),
          body: createReadStream(thumbnailPath),
        },
      });
      thumbnailUploaded = true;
    }

    return {
      videoId,
      youtubeUrl: `https://www.youtube.com/watch?v=${videoId}`,
      thumbnailUploaded,
      publishAt: job.payload.publishAt || null,
    };
  }

  async function loadTokenBundle() {
    await ensureStorage();

    try {
      const raw = await readFile(tokenPath, 'utf8');
      const parsed = JSON.parse(raw);

      if (parsed?.tokens) {
        return {
          ...parsed,
          tokens: sanitizeTokens(parsed.tokens),
          source: parsed.source || 'file',
        };
      }

      if (parsed?.refresh_token) {
        return {
          connectedAt: parsed.connectedAt || null,
          updatedAt: parsed.updatedAt || null,
          source: 'file',
          tokens: sanitizeTokens(parsed),
        };
      }
    } catch {
      // Fall through to environment tokens.
    }

    const refreshToken = process.env.YOUTUBE_REFRESH_TOKEN?.trim();
    if (refreshToken) {
      return {
        connectedAt: null,
        updatedAt: null,
        source: 'env',
        tokens: {
          refresh_token: refreshToken,
        },
      };
    }

    return null;
  }

  async function saveTokenBundle(tokens) {
    await ensureStorage();
    const payload = {
      connectedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      source: 'file',
      tokens: sanitizeTokens(tokens),
    };

    await writeFile(tokenPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  }

  async function clearTokenBundle() {
    await ensureStorage();

    try {
      await rm(tokenPath);
    } catch {
      // Ignore if the file is already gone.
    }
  }

  async function loadQueue() {
    await ensureStorage();

    try {
      const raw = await readFile(queuePath, 'utf8');
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return parsed;
      }
      if (Array.isArray(parsed?.jobs)) {
        return parsed.jobs;
      }
    } catch {
      // No queue file yet.
    }

    return [];
  }

  async function saveQueue(queue) {
    await ensureStorage();
    await writeFile(queuePath, `${JSON.stringify(queue, null, 2)}\n`, 'utf8');
  }

  function normalizePublishPayload(body, activeConfig, activeBaseDir, activeUploadsDir) {
    const title = typeof body.title === 'string' ? body.title.trim() : '';
    if (!title) {
      throw new Error('A video title is required.');
    }

    const description = typeof body.description === 'string' ? body.description.trim() : '';
    const videoPath = resolveMediaPath(body.videoPath || body.filePath || body.path, activeBaseDir, activeUploadsDir);
    const thumbnailValue = typeof body.thumbnailPath === 'string' ? body.thumbnailPath.trim() : '';
    const thumbnailPath = thumbnailValue ? resolveMediaPath(thumbnailValue, activeBaseDir, activeUploadsDir) : '';
    const tags = parseTags(body.tags);
    const publishAt = parsePublishAt(body.publishAt);
    const privacyStatus = publishAt ? 'private' : normalizePrivacyStatus(body.privacyStatus || activeConfig.defaultPrivacyStatus);
    const categoryId = typeof body.categoryId === 'string' && body.categoryId.trim() ? body.categoryId.trim() : activeConfig.defaultCategoryId;

    return {
      title,
      description,
      videoPath,
      thumbnailPath,
      tags,
      publishAt,
      privacyStatus,
      categoryId,
    };
  }

  function parseTags(value) {
    if (Array.isArray(value)) {
      return value
        .map(function (item) {
          return String(item).trim();
        })
        .filter(Boolean)
        .slice(0, 15);
    }

    if (typeof value === 'string') {
      return value
        .split(/[,\n]/)
        .map(function (item) {
          return item.trim();
        })
        .filter(Boolean)
        .slice(0, 15);
    }

    return [];
  }

  function parsePublishAt(value) {
    if (value === undefined || value === null || value === '') {
      return null;
    }

    const parsed = new Date(String(value));
    if (Number.isNaN(parsed.getTime())) {
      throw new Error('publishAt must be a valid ISO 8601 date/time.');
    }

    return parsed.toISOString();
  }

  function normalizePrivacyStatus(value) {
    const candidate = String(value || '').toLowerCase();
    if (candidate === 'private' || candidate === 'public' || candidate === 'unlisted') {
      return candidate;
    }

    return 'private';
  }

  function sanitizeTokens(tokens) {
    const allowedFields = ['access_token', 'refresh_token', 'scope', 'token_type', 'expiry_date', 'id_token'];
    const sanitized = {};

    for (const field of allowedFields) {
      if (tokens && tokens[field]) {
        sanitized[field] = tokens[field];
      }
    }

    return sanitized;
  }

  function resolveMediaPath(value, activeBaseDir, activeUploadsDir) {
    const raw = typeof value === 'string' ? value.trim() : '';
    if (!raw) {
      throw new Error('Video and thumbnail files must live inside the uploads/ directory.');
    }

    let candidate;
    if (path.isAbsolute(raw)) {
      candidate = path.resolve(raw);
    } else if (raw.startsWith('uploads/') || raw.startsWith('uploads\\') || raw.startsWith('./') || raw.startsWith('.\\')) {
      candidate = path.resolve(activeBaseDir, raw);
    } else {
      candidate = path.resolve(activeUploadsDir, raw);
    }

    const allowedRoot = path.resolve(activeUploadsDir);
    if (candidate !== allowedRoot && !candidate.startsWith(`${allowedRoot}${path.sep}`)) {
      throw new Error('Video and thumbnail files must live inside the uploads/ directory.');
    }

    return candidate;
  }

  function detectVideoMimeType(filePath) {
    switch (path.extname(filePath).toLowerCase()) {
      case '.mp4':
        return 'video/mp4';
      case '.mov':
        return 'video/quicktime';
      case '.m4v':
        return 'video/x-m4v';
      case '.webm':
        return 'video/webm';
      case '.mkv':
        return 'video/x-matroska';
      default:
        return 'application/octet-stream';
    }
  }

  function detectThumbnailMimeType(filePath) {
    switch (path.extname(filePath).toLowerCase()) {
      case '.jpg':
      case '.jpeg':
        return 'image/jpeg';
      case '.png':
        return 'image/png';
      default:
        throw new Error('Thumbnail files must be JPG or PNG images.');
    }
  }

  function summarizeJob(job, activeBaseDir) {
    if (!job) {
      return null;
    }

    const payload = job.payload || {};
    const result = job.result || {};
    const videoPath = typeof payload.videoPath === 'string' ? toDisplayPath(payload.videoPath, activeBaseDir) : '';
    const thumbnailPath = typeof payload.thumbnailPath === 'string' ? toDisplayPath(payload.thumbnailPath, activeBaseDir) : '';

    return {
      id: job.id,
      status: job.status,
      attempts: job.attempts || 0,
      createdAt: job.createdAt || null,
      updatedAt: job.updatedAt || null,
      lastAttemptAt: job.lastAttemptAt || null,
      completedAt: job.completedAt || null,
      title: payload.title || '',
      description: payload.description || '',
      videoPath,
      thumbnailPath,
      publishAt: payload.publishAt || null,
      privacyStatus: payload.privacyStatus || null,
      categoryId: payload.categoryId || null,
      tags: Array.isArray(payload.tags) ? payload.tags : [],
      lastError: job.lastError || null,
      videoId: result.videoId || null,
      youtubeUrl: result.youtubeUrl || null,
      thumbnailUploaded: Boolean(result.thumbnailUploaded),
    };
  }

  function toDisplayPath(filePath, activeBaseDir) {
    const relative = path.relative(activeBaseDir, filePath).replace(/\\/g, '/');
    return relative.startsWith('..') ? filePath.replace(/\\/g, '/') : relative;
  }

  function normalizeReturnTo(value) {
    const raw = typeof value === 'string' ? value.trim() : '';
    if (raw.startsWith('/')) {
      return raw;
    }

    return '/youtube';
  }

  function consumeState(state) {
    cleanupStateCache();
    const entry = stateCache.get(state);
    if (!entry) {
      return null;
    }

    stateCache.delete(state);
    return entry;
  }

  function cleanupStateCache() {
    const now = Date.now();
    for (const [key, entry] of stateCache.entries()) {
      if (!entry || now - entry.createdAt > STATE_TTL_MS) {
        stateCache.delete(key);
      }
    }
  }

  function stripTrailingSlash(value) {
    return String(value || '').replace(/\/+$/, '');
  }

  function buildHtmlError(message) {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>YouTube OAuth</title>
  <style>
    body { margin: 0; font-family: system-ui, sans-serif; background: #07111f; color: #f7fbff; display: grid; min-height: 100vh; place-items: center; padding: 24px; }
    .card { max-width: 640px; width: 100%; border: 1px solid rgba(255,255,255,0.08); border-radius: 20px; background: rgba(10,18,32,0.95); padding: 28px; box-shadow: 0 28px 70px rgba(0,0,0,0.35); }
    a { color: #86efac; text-decoration: none; font-weight: 700; }
    p { line-height: 1.6; color: #d4dceb; }
    code { background: rgba(255,255,255,0.06); padding: 0.15rem 0.35rem; border-radius: 8px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>YouTube OAuth is not ready yet</h1>
    <p>${escapeHtml(message)}</p>
    <p>Go back to <a href="/youtube">/youtube</a> after setting the Google OAuth credentials on the VPS.</p>
  </div>
</body>
</html>`;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
}
