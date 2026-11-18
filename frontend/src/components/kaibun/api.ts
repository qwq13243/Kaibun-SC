import type { Emotion } from "@/lib/standAnimations";
import type { Outfit } from "@/lib/api";
import type { StagePose } from "./stage";

export type StageShot = { cast: string[]; poses: Record<string, StagePose>; thought: boolean; outfits: Record<string,string>; outfitSources: Record<string,string>; backgroundSource: string | null };

export type Actor = { id: string; name: string; short_name: string | null; avatar_url: string | null; background_url: string | null; stand: Outfit | null; stand_bounds?: { width: number; height: number } | null; retired_default_outfits?: string[] };

export function migrateOutfitDefaults(outfits: Record<string, string>, actors: readonly Actor[]): Record<string, string> {
  return Object.fromEntries(Object.entries(outfits).filter(([cid, oid]) => !actors.find(actor => actor.id === cid)?.retired_default_outfits?.includes(oid)));
}
export type Wardrobe = Outfit & { label: string; thumbnail?: string | null; bounds?: { width: number; height: number } | null };
export type Background = { id: string; url: string; thumbnail: string; label: string; description: string; tags: string[]; indexed: boolean; time_of_day?: string };
export type ActorState = { mouth_action?:string|null; rest_mouth_action?:string|null; emotion?: Emotion; expression?: string | null; body_action?: string | null; body_mood?: Emotion | null };
export type PerformanceSegment = { mouth_action?:string|null; rest_mouth_action?:string|null; ja: string; zh: string; start?: number; emotion: Emotion; expression?: string | null; body_action?: string | null; body_mood?: Emotion | null; actor_states?: Record<string, ActorState> };
export type Line = { voice_targets?: Record<string,string>; shot?: StageShot; last_fine_edit_token?:string;mouth_action?:string|null;rest_mouth_action?:string|null;performance_actions?:Record<string,{preset_id:string;body:string;face:string;mouth:string;rest:string;kind?:string;entry_idle?:boolean}>; expression?: string | null; body_action?: string | null; body_actions?: Record<string,string>; nichika_voice_style?: "natural" | "idol"; listener_state_persist?: boolean; audio_revision?: string; performance_invalidated?: boolean; audio_status?: string; imported_untranslated?: boolean; performance_segments?: PerformanceSegment[]; id: string; speaker: string; speakers?: string[]; ja: string; zh: string; emotion: Emotion; body_mood?: Emotion | null; screen_cast?: string[]; actor_order?: string[]; actor_states?: Record<string, ActorState>; voice_style?: "natural" | "idol"; kind?: "dialogue" | "thought"; onstage?: string[]; focus?: string[] };
export type Scene = { transition_length?: "short" | "medium" | "long"; line_transition_lengths?: Record<string, "short" | "medium" | "long">; line_transitions?: Record<string, "cut" | "pause" | "fade">; transition?: "cut" | "pause" | "fade"; index: number; title: string; place: string; lines: Line[]; memory: string; outfits?: Record<string, string>; wardrobe?: Record<string, Wardrobe>; wardrobe_changes?: Record<string, Record<string, Wardrobe>>; background_asset?: Background | null; background_change_assets?: Record<string, Background | null> };
export type StoryLength = "short" | "standard" | "half_hour" | "hour_plus";
export const seasons = { spring: "春装", summer: "夏装", autumn: "秋装", winter: "冬装" } as const;
export type Season = keyof typeof seasons;
export type OutfitMode = "story" | "default" | Season;
export function composerOutfitMode(mode?: string): OutfitMode {
  return mode === "story" || mode === "spring" || mode === "summer" || mode === "autumn" || mode === "winter" ? mode : "default";
}
export type OutfitPreferences = { outfit_mode?: OutfitMode | "fixed"; requested_outfits?: Record<string, string> };
export type Outline = { title: string; direction: string; opening?: string; cast: string[]; guests: { id: string; name: string }[]; scenes: { title: string; place: string; direction: string; transition?: "cut" | "pause" | "fade"; transition_length?: "short" | "medium" | "long" }[] };
export type ProductionProgress = { staging_progress?:{scene_number:number;status:string;started_at?:string}|null; voice_failed_ids?:string[]; voices_active?:boolean; staging_active?:boolean; staging_retrying?:boolean; staging_retry_attempt?:number; voice_retrying?:boolean; voices_done: number; voices_total: number; voices_failed?: number; staging_done: boolean; staging_scenes_done?: number; staging_scenes_total?: number; voice_error?: string; staging_error?: string; active?: boolean };
export type StoryBrief = { id: string; premise: string; title: string; pinned?: boolean; length: StoryLength; status: "queued" | "generating" | "preparing" | "ready" | "failed" | "interrupted" | "tuning"; phase: string; error: string | null; created_at: string; production?: ProductionProgress | null };
export type StoryWork = { writing: "complete" | "writing" | "paused" | "failed"; voices: string; staging: string; actions: { pause_writing: boolean; resume: boolean; edit: boolean; continue_story: boolean; export: boolean } };
export type Story = StoryBrief & { work?: StoryWork; failure_stage?:string; writing_batch?: {start:number; end:number; context_scenes:number; context_mode:"full"}; writing_draft?: {scene_number:number; lines:Line[]}; writing_notice?:string; voice_revision?: string; base_fingerprint?: string; revision_history_count?: number; imported_script?: {filename: string; unparsed_lines: number}; revision_proposal?: {id: string; completed: number[]; outline?: Outline; scenes: Record<string,Scene>}; revision_undo?: unknown; revision_changes?: {scene:number; changes:{id:string; before:string; after:string; before_ja?:string; after_ja?:string; fields?:string[]; voice_changed?:boolean; performance_reset?:boolean}[]}[]; revision_progress?: {status: string; message: string; completed?: number; total?: number; scene?: number}; outline: Outline | null; scenes: Scene[]; model: string | null; tuning?: "outline" | "script" | "production" };

export class RequestError extends Error {
  constructor(message: string, public status: number, public savedFingerprint?: string) { super(message); }
}

export async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch("/api/kaibun/" + path, body === undefined ? { cache: "no-store", signal } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new RequestError(typeof data.detail === "string" ? data.detail : "请求失败，请稍后重试", response.status, typeof data.saved_fingerprint === 'string' ? data.saved_fingerprint : undefined);
  }
  return response.json();
}
