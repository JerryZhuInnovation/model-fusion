export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export type ApiFormat = "openai" | "anthropic";

export interface CompleteArgs {
  baseUrl: string;
  apiKey: string;
  model: string;
  messages: ChatMessage[];
  temperature?: number;
  maxTokens?: number;
  timeoutMs?: number;
  apiFormat?: ApiFormat;
}

export interface CompleteResult {
  ok: boolean;
  content?: string;
  error?: string;
  latency_ms: number;
}

function splitSystem(messages: ChatMessage[]): {
  system?: string;
  rest: Array<{ role: "user" | "assistant"; content: string }>;
} {
  const systemParts: string[] = [];
  const rest: Array<{ role: "user" | "assistant"; content: string }> = [];
  for (const m of messages) {
    if (m.role === "system") systemParts.push(m.content || "");
    else if (m.role === "user" || m.role === "assistant") {
      rest.push({ role: m.role, content: m.content || "" });
    }
  }
  const system = systemParts.filter(Boolean).join("\n\n") || undefined;
  return { system, rest };
}

function extractContent(
  raw: unknown,
  fmt: ApiFormat,
): string {
  if (!raw || typeof raw !== "object") return "";
  const obj = raw as Record<string, unknown>;
  if (fmt === "anthropic") {
    const blocks = Array.isArray(obj.content) ? obj.content : [];
    const parts: string[] = [];
    for (const b of blocks) {
      const block = b as { type?: string; text?: string };
      if (block?.type === "text" && block.text) parts.push(block.text);
    }
    return parts.join("");
  }
  const choices = Array.isArray(obj.choices) ? obj.choices : [];
  if (!choices.length) return "";
  const first = choices[0] as {
    message?: { content?: string };
    text?: string;
  };
  return first.message?.content || first.text || "";
}

export async function completeOnce(args: CompleteArgs): Promise<CompleteResult> {
  const started = Date.now();
  const timeoutMs = args.timeoutMs ?? 90_000;
  const fmt: ApiFormat = args.apiFormat ?? "openai";
  const base = args.baseUrl.replace(/\/$/, "");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let url: string;
  let headers: Record<string, string>;
  let body: Record<string, unknown>;

  if (fmt === "anthropic") {
    url = `${base}/messages`;
    const { system, rest } = splitSystem(args.messages);
    const msgs = rest.length ? rest : [{ role: "user" as const, content: "" }];
    body = {
      model: args.model,
      max_tokens: args.maxTokens ?? 2048,
      temperature: args.temperature ?? 0.7,
      messages: msgs,
    };
    if (system) body.system = system;
    headers = {
      "Content-Type": "application/json",
      "x-api-key": args.apiKey,
      "anthropic-version": "2023-06-01",
    };
  } else {
    url = `${base}/chat/completions`;
    body = {
      model: args.model,
      messages: args.messages,
      temperature: args.temperature ?? 0.7,
      max_tokens: args.maxTokens ?? 2048,
    };
    headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${args.apiKey}`,
    };
  }

  try {
    const res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const latency = Date.now() - started;
    if (!res.ok) {
      const detail = (await res.text()).slice(0, 400);
      return { ok: false, error: `HTTP ${res.status}: ${detail}`, latency_ms: latency };
    }

    const raw = await res.json();
    const content = extractContent(raw, fmt);
    if (!content) {
      return { ok: false, error: "empty content", latency_ms: latency };
    }
    return { ok: true, content, latency_ms: latency };
  } catch (err) {
    const latency = Date.now() - started;
    const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
    return { ok: false, error: msg, latency_ms: latency };
  } finally {
    clearTimeout(timer);
  }
}
