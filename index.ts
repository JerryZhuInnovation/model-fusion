#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { completeOnce } from "./complete.js";
import { listProfiles, loadProfile, saveProfile } from "./config.js";
import { fuseOnce } from "./fuse.js";
import { probeAvailableModels } from "./probe.js";
import type { ProfileConfig } from "./types.js";

const server = new McpServer({
  name: "model-fusion",
  version: "0.1.0",
});

const profileSchema = z.object({
  profile: z.string().describe("Profile slug, e.g. default"),
  config: z
    .object({
      version: z.literal(1),
      strategy: z.enum(["A", "B"]),
      judge: z.object({
        model_id: z.string(),
        api_key_env: z.string().optional(),
        base_url: z.string().optional(),
        model: z.string().optional(),
        api_format: z.enum(["openai", "anthropic"]).optional(),
      }),
      workers: z
        .array(
          z.object({
            model_id: z.string(),
            api_key_env: z.string(),
            base_url: z.string(),
            model: z.string(),
            samples: z.number().int().min(1).max(3),
            role: z.enum(["primary", "supplement"]),
            api_format: z.enum(["openai", "anthropic"]).optional(),
          }),
        )
        .min(1),
      temperature: z.object({
        mode: z.enum(["default", "fixed", "custom"]),
        fixed: z.number().optional(),
        sequence: z.array(z.number()).optional(),
      }),
      strategy_options: z.object({
        on_partial_failure: z.enum(["continue_with_available", "abort"]),
        min_success: z.number().int().min(1),
        timeout_s: z.number().min(1),
        hard_only: z.boolean(),
      }),
    })
    .describe("Full profile config per schema"),
});

server.registerTool(
  "probe_available_models",
  {
    title: "Probe available models",
    description:
      "Scan env keys for known OpenAI-compatible APIs and optionally ping them. Never returns raw keys.",
    inputSchema: {
      ping: z.boolean().default(true),
      ping_timeout_ms: z.number().int().default(4000),
    },
  },
  async (args) => {
    const result = await probeAvailableModels(args as { ping?: boolean; ping_timeout_ms?: number });
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  },
);

server.registerTool(
  "complete",
  {
    title: "Single model complete",
    description: "One chat completion against a specific endpoint (debug / host model use).",
    inputSchema: {
      model_id: z.string(),
      base_url: z.string(),
      api_key_env: z.string(),
      model: z.string(),
      messages: z.array(
        z.object({
          role: z.enum(["system", "user", "assistant"]),
          content: z.string(),
        }),
      ),
      temperature: z.number().optional(),
      max_tokens: z.number().optional(),
      timeout_ms: z.number().optional(),
      api_format: z.enum(["openai", "anthropic"]).optional(),
    },
  },
  async (args) => {
    const apiKey = process.env[args.api_key_env];
    if (!apiKey) {
      return {
        content: [{ type: "text", text: JSON.stringify({ ok: false, error: `missing key env: ${args.api_key_env}`, latency_ms: 0 }) }],
      };
    }
    const res = await completeOnce({
      baseUrl: args.base_url,
      apiKey,
      model: args.model,
      messages: args.messages,
      temperature: args.temperature,
      maxTokens: args.max_tokens,
      timeoutMs: args.timeout_ms,
      apiFormat: args.api_format,
    });
    return {
      content: [{ type: "text", text: JSON.stringify({ model_id: args.model_id, ...res }, null, 2) }],
    };
  },
);

server.registerTool(
  "fuse_once",
  {
    title: "Fuse once",
    description:
      "Run fan-out + single judge for a saved profile. Strategy comes only from the profile (skill writes it).",
    inputSchema: {
      profile: z.string().default("default"),
      task: z.string(),
      system: z.string().optional(),
      run_id: z.string().optional(),
    },
  },
  async (args) => {
    const result = await fuseOnce({
      profile: args.profile,
      task: args.task,
      system: args.system,
    });
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  },
);

server.registerTool(
  "save_profile",
  {
    title: "Save profile",
    description: "Persist a fusion profile under MODEL_FUSION_HOME.",
    inputSchema: {
      profile: z.string(),
      config: profileSchema.shape.config,
    },
  },
  async (args) => {
    try {
      const p = await saveProfile(args.profile, args.config as ProfileConfig);
      return {
        content: [{ type: "text", text: JSON.stringify({ ok: true, path: p }, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ ok: false, error: err instanceof Error ? err.message : String(err) }),
          },
        ],
      };
    }
  },
);

server.registerTool(
  "load_profile",
  {
    title: "Load profile",
    description: "Load a fusion profile by name.",
    inputSchema: { profile: z.string() },
  },
  async (args) => {
    try {
      const cfg = await loadProfile(args.profile);
      return {
        content: [{ type: "text", text: JSON.stringify({ ok: true, profile: args.profile, config: cfg }, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ ok: false, error: err instanceof Error ? err.message : String(err) }),
          },
        ],
      };
    }
  },
);

server.registerTool(
  "list_profiles",
  {
    title: "List profiles",
    description: "List profile slugs in MODEL_FUSION_HOME.",
    inputSchema: {},
  },
  async () => {
    const profiles = await listProfiles();
    return {
      content: [{ type: "text", text: JSON.stringify({ profiles }, null, 2) }],
    };
  },
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // log to stderr only
  process.stderr.write("model-fusion-mcp listening on stdio\n");
}

main().catch((err) => {
  process.stderr.write(String(err) + "\n");
  process.exit(1);
});
