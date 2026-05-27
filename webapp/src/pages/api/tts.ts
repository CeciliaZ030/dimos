import type { NextApiRequest, NextApiResponse } from "next";
import OpenAI from "openai";

/**
 * Server-side proxy to OpenAI text-to-speech (gpt-4o-mini-tts).
 *
 * The browser POSTs `{ text }` and gets back an MP3, which the phone plays via
 * an <audio> element (see src/lib/speech.ts). This runs server-side so the
 * OPENAI_API_KEY never reaches the client — keep it as OPENAI_API_KEY (NOT
 * NEXT_PUBLIC_*) in .env.local.
 *
 * We use cloud TTS instead of the browser SpeechSynthesis API because iOS
 * silently drops speech that isn't started inside a user gesture; agent replies
 * arrive asynchronously over SSE, so they never spoke. An <audio> element,
 * once unlocked by one tap, plays async audio reliably on iOS.
 */

// MP3 for a sentence is tiny, but don't let Next cap the response.
export const config = { api: { responseLimit: false } };

const MODEL = process.env.OPENAI_TTS_MODEL ?? "gpt-4o-mini-tts";
const VOICE = process.env.OPENAI_TTS_VOICE ?? "onyx"; // matches the robot's voice

let client: OpenAI | null = null;
function getClient(apiKey: string): OpenAI {
  if (!client) client = new OpenAI({ apiKey });
  return client;
}

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "method not allowed" });
    return;
  }

  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    res.status(500).json({ error: "OPENAI_API_KEY is not set" });
    return;
  }

  const text = String(req.body?.text ?? "").trim();
  if (!text) {
    res.status(400).json({ error: "missing text" });
    return;
  }

  try {
    const speech = await getClient(apiKey).audio.speech.create({
      model: MODEL,
      voice: VOICE,
      input: text,
      response_format: "mp3",
    });

    const audio = Buffer.from(await speech.arrayBuffer());
    res.setHeader("Content-Type", "audio/mpeg");
    res.setHeader("Cache-Control", "no-store");
    res.status(200).send(audio);
  } catch (err) {
    const status =
      err && typeof err === "object" && "status" in err
        ? Number((err as { status: unknown }).status) || 502
        : 502;
    res.status(status).json({ error: "tts failed", detail: String(err) });
  }
}
