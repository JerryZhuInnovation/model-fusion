import { promises as fs } from "node:fs";
import path from "node:path";
import type { ProfileConfig } from "./types.js";

const SAFE_PROFILE = /^[a-z0-9-]+$/;

export function fusionHome(): string {
  return (
    process.env.MODEL_FUSION_HOME ||
    path.join(process.env.HOME || "~", ".config", "model-fusion")
  );
}

export function assertProfileSlug(profile: string): void {
  if (!SAFE_PROFILE.test(profile)) {
    throw new Error(`invalid profile name: ${JSON.stringify(profile)}`);
  }
}

export function profilePath(profile: string): string {
  assertProfileSlug(profile);
  return path.join(fusionHome(), `${profile}.json`);
}

export async function loadProfile(profile: string): Promise<ProfileConfig> {
  const p = profilePath(profile);
  let raw: string;
  try {
    raw = await fs.readFile(p, "utf8");
  } catch {
    throw new Error(`profile not found: ${p}`);
  }
  return JSON.parse(raw) as ProfileConfig;
}

export async function saveProfile(
  profile: string,
  config: ProfileConfig,
): Promise<string> {
  const p = profilePath(profile);
  await fs.mkdir(path.dirname(p), { recursive: true });
  const now = new Date().toISOString();
  const next: ProfileConfig = {
    ...config,
    created_at: config.created_at || now,
    updated_at: now,
  };
  await fs.writeFile(p, JSON.stringify(next, null, 2), "utf8");
  return p;
}

export async function listProfiles(): Promise<string[]> {
  const dir = fusionHome();
  try {
    const files = await fs.readdir(dir);
    return files
      .filter((f) => f.endsWith(".json") && SAFE_PROFILE.test(f.replace(/\.json$/, "")))
      .map((f) => f.replace(/\.json$/, ""))
      .sort();
  } catch {
    return [];
  }
}
