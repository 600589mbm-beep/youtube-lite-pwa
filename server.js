import express from 'express';
import dotenv from 'dotenv';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const app = express();

const PORT = Number(process.env.PORT || 3456);
const APP_TITLE = process.env.OPENROUTER_TITLE || 'YouTube Automation Agent';
const OPENROUTER_MODEL = process.env.OPENROUTER_MODEL || 'openai/gpt-4o-mini';
const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY || '';
const HTTP_REFERER = process.env.HTTP_REFERER || process.env.APP_URL || 'http://localhost:3456';

app.use(express.json({ limit: '1mb' }));
app.use(express.static(__dirname, { extensions: ['html'] }));

app.get('/', function (_req, res) {
  res.sendFile(path.join(__dirname, 'app.html'));
});

app.get('/landing', function (_req, res) {
  res.sendFile(path.join(__dirname, 'index.html'));
});

app.get('/api/health', function (_req, res) {
  res.json({
    ok: true,
    service: APP_TITLE,
    model: OPENROUTER_MODEL,
    openrouterConfigured: Boolean(OPENROUTER_API_KEY),
    uptimeSeconds: Math.round(process.uptime()),
  });
});

app.post('/api/generate', async function (req, res) {
  try {
    if (!OPENROUTER_API_KEY) {
      return res.status(500).json({
        ok: false,
        error: 'OPENROUTER_API_KEY is not configured on the server.',
      });
    }

    const body = req.body || {};
    const topic = typeof body.topic === 'string' ? body.topic.trim() : '';
    const audience = typeof body.audience === 'string' && body.audience.trim() ? body.audience.trim() : 'YouTube viewers';
    const style = typeof body.style === 'string' && body.style.trim() ? body.style.trim() : 'tutorial';
    const tone = typeof body.tone === 'string' && body.tone.trim() ? body.tone.trim() : 'clear, engaging, and practical';
    const length = typeof body.length === 'string' && body.length.trim() ? body.length.trim() : 'about 8 minutes';
    const model = typeof body.model === 'string' && body.model.trim() ? body.model.trim() : OPENROUTER_MODEL;

    if (!topic) {
      return res.status(400).json({
        ok: false,
        error: 'Please provide a topic before generating a content pack.',
      });
    }

    const systemPrompt = [
      'You are a YouTube channel production assistant.',
      'Create original, non-plagiarized content that is suitable for a creator who publishes frequently.',
      'Return strict JSON only with these keys:',
      'title, hook, outline, script, thumbnailText, seoTitle, seoDescription, tags, nextActions',
      'The outline must be an array of 6 to 8 concise strings.',
      'The tags must be an array of 10 to 15 strings.',
      'The nextActions must be an array of short, practical production steps.',
      'Do not wrap the response in markdown fences or commentary.',
    ].join(' ');

    const userPrompt = [
      `Topic: ${topic}`,
      `Audience: ${audience}`,
      `Style: ${style}`,
      `Tone: ${tone}`,
      `Target length: ${length}`,
      '',
      'Generate a package for the next video. Keep the script concise but useful, write for retention, and make the thumbnail text punchy.',
    ].join('\n');

    const response = await fetch('https://openrouter.ai/api/v1/chat/completions', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${OPENROUTER_API_KEY}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': HTTP_REFERER,
        'X-OpenRouter-Title': APP_TITLE,
      },
      body: JSON.stringify({
        model,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        temperature: 0.7,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      return res.status(response.status).json({
        ok: false,
        error: `OpenRouter request failed: ${errorText}`,
      });
    }

    const payload = await response.json();
    const content = payload?.choices?.[0]?.message?.content?.trim() || '';
    const parsed = extractJsonPayload(content);

    return res.json({
      ok: true,
      model,
      usage: payload.usage || null,
      result: parsed || { text: content },
      rawText: parsed ? content : null,
    });
  } catch (error) {
    return res.status(500).json({
      ok: false,
      error: error instanceof Error ? error.message : 'Unexpected server error.',
    });
  }
});

function extractJsonPayload(text) {
  if (!text) {
    return null;
  }

  const candidates = [];
  const trimmed = text.trim();
  candidates.push(trimmed);

  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced?.[1]) {
    candidates.push(fenced[1].trim());
  }

  const firstBrace = trimmed.indexOf('{');
  const lastBrace = trimmed.lastIndexOf('}');
  if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
    candidates.push(trimmed.slice(firstBrace, lastBrace + 1));
  }

  for (const candidate of candidates) {
    try {
      return JSON.parse(candidate);
    } catch {
      // Keep trying the next candidate.
    }
  }

  return null;
}

app.listen(PORT, '0.0.0.0', function () {
  console.log(`${APP_TITLE} listening on port ${PORT}`);
});
