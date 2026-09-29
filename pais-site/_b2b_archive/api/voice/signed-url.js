// Public Copper Lantern voice demo — signed-URL rotation.
//
// The browser can't hold our ElevenLabs keys, so this function does. It walks
// the key chain (3 free accounts ≈ 45 Conversational-AI min/month combined),
// asks ElevenLabs for a short-lived signed wss:// URL, and returns the first one
// that works. When an account is out of free minutes its request fails and we
// fall through to the next — keys + agent_ids never reach the client.
//
// Abuse guardrails: each agent is capped at 5 min/call (set on the agent itself),
// and this function adds a best-effort per-IP cooldown.
//
// Env (set in Vercel — paired key+agent per account):
//   ELEVENLABS_API_KEY            + ELEVENLABS_AGENT_ID
//   ELEVENLABS_API_KEY_FALLBACK   + ELEVENLABS_AGENT_ID_FALLBACK
//   ELEVENLABS_API_KEY_FALLBACK_2 + ELEVENLABS_AGENT_ID_FALLBACK_2

const PAIRS = [
  ["ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID"],
  ["ELEVENLABS_API_KEY_FALLBACK", "ELEVENLABS_AGENT_ID_FALLBACK"],
  ["ELEVENLABS_API_KEY_FALLBACK_2", "ELEVENLABS_AGENT_ID_FALLBACK_2"],
];

const COOLDOWN_MS = 15_000; // best-effort, per warm instance
const lastHit = new Map();

function chain() {
  return PAIRS
    .map(([k, a]) => [process.env[k], process.env[a]])
    .filter(([key, agentId]) => key && agentId);
}

export default async function handler(req, res) {
  const ip = (req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "anon";
  const now = Date.now();
  const prev = lastHit.get(ip) || 0;
  if (now - prev < COOLDOWN_MS) {
    res.status(429).json({ error: "Easy there — give the line a few seconds." });
    return;
  }
  lastHit.set(ip, now);

  const keys = chain();
  if (!keys.length) {
    res.status(500).json({ error: "Voice demo not configured." });
    return;
  }

  const errors = [];
  for (const [key, agentId] of keys) {
    try {
      const url = `https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=${encodeURIComponent(agentId)}`;
      const r = await fetch(url, { headers: { "xi-api-key": key } });
      if (!r.ok) {
        errors.push(`${agentId.slice(-4)}: ${r.status}`);
        continue;
      }
      const data = await r.json();
      if (data.signed_url) {
        res.status(200).json({ signed_url: data.signed_url });
        return;
      }
      errors.push(`${agentId.slice(-4)}: no url`);
    } catch (e) {
      errors.push(`${agentId.slice(-4)}: ${e.message}`);
    }
  }
  // All accounts exhausted/failing — tell the client to fall back to "book a call".
  res.status(503).json({ error: "All demo minutes are used up for now.", detail: errors });
}
