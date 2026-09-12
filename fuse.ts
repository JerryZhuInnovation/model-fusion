import { promises as fs } from "node:fs";
import path from "node:path";
import { completeOnce, type ChatMessage } from "./complete.js";
import { fusionHome, loadProfile } from "./config.js";
import {
  JUDGE_SYSTEM,
  type CandidateResult,
  type FuseOnceResult,
  type ProfileConfig,
  type WorkerConfig,
} from "./types.js";

const DEFAULT_TEMPS = [0.4, 0.7, 1.0];

function tempSequence(mode: ProfileConfig["temperature"], n: number): number[] {
  if (mode.mode === "fixed") {
    const t = mode.fixed ?? 0.7;
    return Array.from({ length: n }, () => t);
  }
  if (mode.mode === "custom") {
    const seq = mode.sequence?.length ? mode.sequence : DEFAULT_TEMPS;
    return Array.from({ length: n }, (_, i) => seq[i % seq.length]!);
  }
  return Array.from({ length: n }, (_, i) => DEFAULT_TEMPS[i % DEFAULT_TEMPS.length]!);
}

function buildJudgeTask(
  strategy: "A" | "B",
  task: string,
  candidates: CandidateResult[],
  workers: WorkerConfig[],
): string {
  const ok = candidates.filter((c) => c.ok && c.content);
  const roleMap = new Map(workers.map((w) => [w.model_id, w.role]));
  const primary: CandidateResult[] = [];
  const supplement: CandidateResult[] = [];
  for (const c of ok) {
    const role = roleMap.get(c.model_id) ?? "primary";
    if (role === "supplement") supplement.push(c);
    else primary.push(c);
  }

  const block = (items: CandidateResult[]) =>
    items
      .map((c, i) => `### Sample #${i + 1}\n${(c.content || "").trim()}\n`)
      .join("\n");

  if (strategy === "B") {
    return [
      `## 用户任务\n${task}`,
      `## 主方案候选\n${block(primary) || "(无)"}`,
      `## 补充视角（旁证/反例，不决定主线结构）\n${block(supplement) || "(无)"}`,
      "请按系统说明完成取舍、判伪与组织，输出唯一最终答复。",
    ].join("\n\n");
  }

  const flat = [...primary, ...supplement];
  return [
    `## 用户任务\n${task}`,
    `## 全部候选\n${block(flat)}`,
    "请按系统说明完成取舍、判伪与组织，输出唯一最终答复。",
  ].join("\n\n");
}

async function writeRun(profileName: string, payload: FuseOnceResult): Promise<string> {
  const runs = path.join(fusionHome(), "runs");
  await fs.mkdir(runs, { recursive: true });
  const ts = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "");
  const base = path.join(runs, `${ts}-${profileName}`);
  const jsonPath = `${base}.json`;
  await fs.writeFile(jsonPath, JSON.stringify(payload, null, 2), "utf8");
  const md = [
    `# fusion run ${ts}`,
    `profile: ${profileName}`,
    `strategy: ${payload.strategy}`,
    `judge: ${payload.judge_model}`,
    "",
    "## workers",
    ...payload.workers_used.map((w) => `- ${w.model_id}×${w.n} ok=${w.ok}`),
    "",
    "## final",
    payload.final || "(empty)",
  ].join("\n");
  await fs.writeFile(`${base}.md`, md, "utf8");
  return jsonPath;
}

export async function fuseOnce(input: {
  profile: string;
  task: string;
  system?: string;
}): Promise<FuseOnceResult> {
  let cfg: ProfileConfig;
  try {
    cfg = await loadProfile(input.profile);
  } catch (err) {
    return {
      ok: false,
      profile: input.profile,
      strategy: "A",
      judge_model: "",
      workers_used: [],
      candidates: [],
      error: err instanceof Error ? err.message : String(err),
    };
  }

  const strategy = cfg.strategy;
  const workers = cfg.workers || [];
  const judge = cfg.judge || { model_id: "" };
  const opts = cfg.strategy_options || {
    on_partial_failure: "continue_with_available",
    min_success: 2,
    timeout_s: 90,
    hard_only: true,
  };
  const timeoutMs = Math.max(1, opts.timeout_s) * 1000;
  const minSuccess = opts.min_success || 2;

  if (!workers.length) {
    return {
      ok: false,
      profile: input.profile,
      strategy,
      judge_model: judge.model_id || "",
      workers_used: [],
      candidates: [],
      error: "profile has no workers",
    };
  }

  type Job = { w: WorkerConfig; i: number; temp: number };
  const jobs: Job[] = [];
  for (const w of workers) {
    const n = Math.min(3, Math.max(1, w.samples || 1));
    const temps = tempSequence(cfg.temperature || { mode: "default" }, n);
    for (let i = 0; i < n; i++) {
      jobs.push({ w, i, temp: temps[i]! });
    }
  }

  // SPEC: 并发上限 4
  const CONCURRENCY = 4;
  const candidates: CandidateResult[] = new Array(jobs.length);
  let next = 0;
  async function runJob(job: Job): Promise<CandidateResult> {
    const { w, i, temp } = job;
    const entry: CandidateResult = {
      id: `${w.model_id}#${i}`,
      model_id: w.model_id,
      sample_index: i,
      temperature: temp,
      ok: false,
    };
    const key = w.api_key_env ? process.env[w.api_key_env] : undefined;
    if (!key) {
      entry.error = `missing key env: ${w.api_key_env}`;
      return entry;
    }
    if (!w.base_url || !w.model) {
      entry.error = `incomplete worker endpoint: ${w.model_id}`;
      return entry;
    }
    const messages: ChatMessage[] = [];
    if (input.system) messages.push({ role: "system", content: input.system });
    messages.push({ role: "user", content: input.task });
    const res = await completeOnce({
      baseUrl: w.base_url,
      apiKey: key,
      model: w.model,
      messages,
      temperature: temp,
      timeoutMs,
      apiFormat: w.api_format ?? "openai",
    });
    entry.ok = res.ok;
    entry.content = res.content;
    entry.error = res.error ?? undefined;
    entry.api_format = w.api_format ?? "openai";
    return entry;
  }

  async function workerLoop(): Promise<void> {
    for (;;) {
      const idx = next++;
      if (idx >= jobs.length) return;
      candidates[idx] = await runJob(jobs[idx]!);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(CONCURRENCY, jobs.length) }, () => workerLoop()),
  );

  const workers_used = workers.map((w) => {
    const subset = candidates.filter((c) => c.model_id === w.model_id);
    return {
      model_id: w.model_id,
      n: subset.length,
      ok: subset.filter((c) => c.ok).length,
    };
  });

  const success = candidates.filter((c) => c.ok);
  const base: FuseOnceResult = {
    ok: false,
    profile: input.profile,
    strategy,
    judge_model: judge.model_id || judge.model || "",
    workers_used,
    candidates,
    error: undefined,
  };

  if (!success.length) {
    base.error = "no successful worker candidates";
    base.run_path = await writeRun(input.profile, base);
    return base;
  }

  const degraded = success.length < minSuccess;
  if (degraded && opts.on_partial_failure === "abort") {
    base.error = `only ${success.length} ok < min_success ${minSuccess} (abort)`;
    base.degraded = true;
    base.run_path = await writeRun(input.profile, base);
    return base;
  }

  // SPEC step 6: 单路成功 → 直接用该候选作 final，不调 judge
  if (success.length === 1) {
    base.ok = true;
    base.degraded = true;
    base.final = success[0]!.content;
    base.note = "single_success_skipped_judge";
    base.run_path = await writeRun(input.profile, base);
    return base;
  }

  const jKeyEnv = judge.api_key_env;
  const jBase = judge.base_url;
  const jModel = judge.model;
  if (!jKeyEnv || !jBase || !jModel) {
    base.error = `judge missing api_key_env/base_url/model in profile (model_id=${judge.model_id})`;
    base.degraded = degraded;
    base.run_path = await writeRun(input.profile, base);
    return base;
  }
  const jKey = process.env[jKeyEnv];
  if (!jKey) {
    base.error = `missing judge key env: ${jKeyEnv}`;
    base.degraded = degraded;
    base.run_path = await writeRun(input.profile, base);
    return base;
  }

  const judgeTask = buildJudgeTask(strategy, input.task, candidates, workers);
  const jres = await completeOnce({
    baseUrl: jBase,
    apiKey: jKey,
    model: jModel,
    messages: [
      { role: "system", content: JUDGE_SYSTEM },
      { role: "user", content: judgeTask },
    ],
    temperature: 0.2,
    timeoutMs,
    apiFormat: judge.api_format ?? "openai",
  });

  base.degraded = degraded;
  if (!jres.ok) {
    base.error = `judge failed: ${jres.error}`;
    base.run_path = await writeRun(input.profile, base);
    return base;
  }

  base.ok = true;
  base.final = jres.content;
  base.run_path = await writeRun(input.profile, base);
  return base;
}

/** Build a complete() tool result without profile */
export { completeOnce };
