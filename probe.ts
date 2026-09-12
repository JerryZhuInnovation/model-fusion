import type { ApiFormat, ProbeCandidate, ProbeResult } from "./types.js";
import { completeOnce } from "./complete.js";

interface Known {
  id: string;
  display: string;
  base_url: string;
  model: string;
  api_key_env: string;
  alt_envs?: string[];
  api_format: ApiFormat;
}

const KNOWN: Known[] = [
  {
    id: "openai",
    display: "OpenAI",
    base_url: "https://api.openai.com/v1",
    model: "gpt-4o-mini",
    api_key_env: "OPENAI_API_KEY",
    api_format: "openai",
  },
  {
    id: "anthropic",
    display: "Anthropic",
    base_url: "https://api.anthropic.com/v1",
    model: "claude-sonnet-4-5",
    api_key_env: "ANTHROPIC_API_KEY",
    api_format: "anthropic",
  },
  {
    id: "openrouter",
    display: "OpenRouter",
    base_url: "https://openrouter.ai/api/v1",
    model: "openai/gpt-4o-mini",
    api_key_env: "OPENROUTER_API_KEY",
    api_format: "openai",
  },
  {
    id: "deepseek",
    display: "DeepSeek",
    base_url: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
    api_key_env: "DEEPSEEK_API_KEY",
    api_format: "openai",
  },
  {
    id: "moonshot",
    display: "Moonshot",
    base_url: "https://api.moonshot.cn/v1",
    model: "moonshot-v1-8k",
    api_key_env: "MOONSHOT_API_KEY",
    api_format: "openai",
  },
  {
    id: "gemini",
    display: "Google Gemini",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    model: "gemini-2.0-flash",
    api_key_env: "GEMINI_API_KEY",
    alt_envs: ["GOOGLE_API_KEY"],
    api_format: "openai",
  },
];

function getKey(k: Known): string | null {
  for (const name of [k.api_key_env, ...(k.alt_envs || [])]) {
    const v = process.env[name];
    if (v) return v;
  }
  return null;
}

async function pingOne(k: Known, ping: boolean, timeoutMs: number): Promise<ProbeCandidate> {
  const key = getKey(k);
  const base: ProbeCandidate = {
    id: k.id,
    display: k.display,
    base_url: k.base_url,
    model: k.model,
    api_key_env: k.api_key_env,
    api_format: k.api_format,
    ping_ok: false,
    ping_ms: null,
    roles: [],
    note: null,
  };

  if (!key) {
    base.note = `missing key env: ${k.api_key_env}`;
    return base;
  }
  if (!ping) {
    base.ping_ok = true;
    base.roles = ["worker", "judge"];
    return base;
  }

  const started = Date.now();
  const result = await completeOnce({
    baseUrl: k.base_url,
    apiKey: key,
    model: k.model,
    messages: [{ role: "user", content: "ping" }],
    maxTokens: 1,
    timeoutMs,
    apiFormat: k.api_format,
  });
  base.ping_ms = Date.now() - started;
  if (result.ok) {
    base.ping_ok = true;
    base.roles = ["worker", "judge"];
  } else {
    if (result.error && /HTTP (401|403)/.test(result.error)) {
      base.note = result.error;
    } else {
      base.ping_ok = true;
      base.roles = ["worker", "judge"];
      base.note = result.error || null;
    }
  }
  return base;
}

export async function probeAvailableModels(opts: {
  ping?: boolean;
  ping_timeout_ms?: number;
} = {}): Promise<ProbeResult> {
  const ping = opts.ping !== false;
  const timeoutMs = opts.ping_timeout_ms ?? 4000;
  const candidates = await Promise.all(KNOWN.map((k) => pingOne(k, ping, timeoutMs)));
  const host =
    process.env.MIMO_MODEL ||
    process.env.MODEL_NAME ||
    process.env.ANTHROPIC_MODEL ||
    process.env.OPENAI_MODEL ||
    null;
  return { candidates, host_model_hint: host };
}
