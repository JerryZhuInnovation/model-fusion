export type Role = "worker" | "judge";
export type ApiFormat = "openai" | "anthropic";

export interface ProbeCandidate {
  id: string;
  display: string;
  base_url: string;
  model: string;
  api_key_env: string;
  api_format: ApiFormat;
  ping_ok: boolean;
  ping_ms?: number | null;
  roles: Role[];
  note?: string | null;
}

export interface ProbeResult {
  candidates: ProbeCandidate[];
  host_model_hint?: string | null;
}

export interface WorkerConfig {
  model_id: string;
  api_key_env: string;
  base_url: string;
  model: string;
  samples: number;
  role: "primary" | "supplement";
  api_format?: ApiFormat;
}

export interface ProfileConfig {
  version: number;
  strategy: "A" | "B";
  judge: {
    model_id: string;
    api_key_env?: string;
    base_url?: string;
    model?: string;
    api_format?: ApiFormat;
  };
  workers: WorkerConfig[];
  temperature: {
    mode: "default" | "fixed" | "custom";
    fixed?: number;
    sequence?: number[];
  };
  strategy_options: {
    on_partial_failure: "continue_with_available" | "abort";
    min_success: number;
    timeout_s: number;
    hard_only: boolean;
    max_tokens?: number;
  };
  created_at?: string;
  updated_at?: string;
}

export interface CandidateResult {
  id: string;
  model_id: string;
  sample_index: number;
  temperature: number;
  content?: string;
  error?: string;
  ok?: boolean;
  api_format?: ApiFormat;
}

export interface FuseOnceResult {
  ok: boolean;
  profile: string;
  strategy: "A" | "B";
  judge_model: string;
  workers_used: Array<{ model_id: string; n: number; ok: number }>;
  candidates: CandidateResult[];
  final?: string;
  run_path?: string;
  error?: string;
  degraded?: boolean;
  note?: string;
}

export const JUDGE_SYSTEM = [
  "你是融合仲裁者。任务：取舍、判伪、组织。",
  "- 从候选中吸收正确、完整、与用户任务匹配的内容。",
  "- 发现明显事实错误或互相冲突时，做出取舍并用一两句说明理由。",
  "- 输出一份可直接使用的最终答复，结构清晰。",
  "- 不要复述全部候选，不要给模型打分排名，不要引入候选之外的新事实声称。",
  "- 不要区分或猜测候选来自哪个模型。",
].join("\n");
