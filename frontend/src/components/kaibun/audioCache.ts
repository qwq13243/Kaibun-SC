import type { Line } from "./api";

export type AudioTarget = { key: string; url: string };

export function voiceKey(line: Line, id = line.id): string {
  return JSON.stringify([id, line.speaker, line.speakers, line.ja, line.emotion, line.kind,
    line.voice_style, line.nichika_voice_style, line.audio_revision]);
}

/** Bounded object URLs, shared in-flight reads, and cancellation on seeking. */
export class StoryAudioCache {
  private cached = new Map<string, string>();
  private pending = new Map<string, { controller: AbortController; promise: Promise<string> }>();
  private wanted = new Set<string>();
  private generation = 0;
  private closed = false;

  // Native Window.fetch requires its browser receiver. Do not store it bare
  // and then invoke it as this.fetcher(), which binds it to the cache instance.
  constructor(private readonly fetcher: typeof fetch = (...args) => globalThis.fetch(...args),
    private readonly objectURL = (blob: Blob) => URL.createObjectURL(blob),
    private readonly revoke = (url: string) => URL.revokeObjectURL(url),
    private readonly limit = 64) {}

  keep(keys: string[]) {
    this.wanted = new Set(keys);
    this.generation++;
    for (const [key, request] of this.pending) {
      if (!this.wanted.has(key)) {
        this.pending.delete(key);
        request.controller.abort();
      }
    }
  }

  get(target: AudioTarget): Promise<string> {
    if (this.closed) return Promise.reject(new DOMException("Closed", "AbortError"));
    const cached = this.cached.get(target.key);
    if (cached) {
      this.cached.delete(target.key);
      this.cached.set(target.key, cached);
      return Promise.resolve(cached);
    }
    const pending = this.pending.get(target.key);
    if (pending) return pending.promise;
    const controller = new AbortController();
    const request = { controller, promise: null as unknown as Promise<string> };
    request.promise = this.fetcher(target.url, { cache: "no-store", signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(response.status === 404 ? "这句配音尚未完成" : "语音读取失败");
      const blob = await response.blob();
      if (controller.signal.aborted || this.closed) throw new DOMException("Aborted", "AbortError");
      const url = this.objectURL(blob);
      this.cached.set(target.key, url);
      while (this.cached.size > this.limit) {
        // Keep the active window's audio alive until it is no longer needed.
        const oldest = [...this.cached.keys()].find(key => !this.wanted.has(key));
        if (oldest === undefined) break;
        this.revoke(this.cached.get(oldest)!);
        this.cached.delete(oldest);
      }
      return url;
    }).finally(() => {
      if (this.pending.get(target.key) === request) this.pending.delete(target.key);
    });
    this.pending.set(target.key, request);
    return request.promise;
  }

  async prefetch(targets: AudioTarget[]) {
    const generation = this.generation;
    let position = 0;
    const worker = async () => {
      while (!this.closed && generation === this.generation && position < targets.length) {
        const target = targets[position++];
        if (!this.wanted.has(target.key)) continue;
        try { await this.get(target); } catch { /* Missing audio is retried on use. */ }
      }
    };
    await Promise.all([worker(), worker()]);
  }

  close() {
    this.closed = true;
    this.generation++;
    for (const request of this.pending.values()) request.controller.abort();
    this.pending.clear();
    for (const url of this.cached.values()) this.revoke(url);
    this.cached.clear();
  }
}
