"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { request, type Actor, type Wardrobe } from "./api";

const SEASONS = [
  ["spring", "春"], ["summer", "夏"], ["autumn", "秋"], ["winter", "冬"], ["special", "特殊"],
] as const;
type Season = typeof SEASONS[number][0];
type Choices = Record<string, Partial<Record<Season, string>>>;
type Load<T> = { data?: T; error?: string };
const STORAGE_KEY = "kaibun.outfit-review.v1";
const PAGE_SIZE = 24;
const SELECT = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm";

function savedChoices(): Choices {
  try {
    const draft = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    return Object.fromEntries(Object.entries(draft).flatMap(([actor, row]) => {
      if (!row || typeof row !== "object") return [];
      const valid = SEASONS.flatMap(([key]) => {
        const outfit = (row as Record<string, unknown>)[key];
        return typeof outfit === "string" && /^(stand|stand_costume)\/\d{10}$/.test(outfit) ? [[key, outfit]] : [];
      });
      return valid.length ? [[actor, Object.fromEntries(valid)]] : [];
    }));
  } catch {
    return {};
  }
}

function OutfitImage({ outfit }: { outfit: Wardrobe }) {
  const [failed, setFailed] = useState(false);
  return <div className="flex aspect-[4/5] w-full items-center justify-center bg-muted/50 p-2 text-xs text-muted-foreground">
    {outfit.thumbnail && !failed
      ? <img src={outfit.thumbnail} alt="" loading="lazy" onError={() => setFailed(true)} className="size-full object-contain" />
      : <span>暂无预览</span>}
  </div>;
}

export function OutfitReview() {
  const [actors, setActors] = useState<Load<Actor[]>>({});
  const [catalogs, setCatalogs] = useState<Record<string, Load<Wardrobe[]>>>({});
  const [actorId, setActorId] = useState("");
  const [season, setSeason] = useState<Season>("spring");
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [choices, setChoices] = useState<Choices>({});
  const [attempt, setAttempt] = useState(0);
  const [saveStatus, setSaveStatus] = useState("");

  useEffect(() => { setChoices(savedChoices()); }, []);
  useEffect(() => {
    if (actors.data) return;
    const controller = new AbortController();
    request<Actor[]>("characters", undefined, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setActors({ data });
      setActorId(data[0]?.id ?? "");
    }).catch(error => {
      if (!controller.signal.aborted) setActors({ error: error.message });
    });
    return () => controller.abort();
  }, [actors.data, attempt]);

  const catalog = catalogs[actorId];
  useEffect(() => {
    if (!actorId || catalog?.data) return;
    const controller = new AbortController();
    request<Wardrobe[]>(`characters/${encodeURIComponent(actorId)}/outfits`, undefined, controller.signal).then(data => {
      if (!controller.signal.aborted) setCatalogs(previous => ({ ...previous, [actorId]: { data } }));
    }).catch(error => {
      if (!controller.signal.aborted) setCatalogs(previous => ({ ...previous, [actorId]: { error: error.message } }));
    });
    return () => controller.abort();
  }, [actorId, catalog?.data, attempt]);

  const current = choices[actorId] ?? {};
  const count = Object.values(choices).reduce((total, row) => total + Object.keys(row).length, 0);
  const seasonLabel = SEASONS.find(([key]) => season === key)?.[1];
  const term = search.trim().toLowerCase();
  const filtered = (catalog?.data ?? []).filter(outfit =>
    (category === "all" || outfit.id.startsWith(`${category}/`)) && `${outfit.label} ${outfit.id}`.toLowerCase().includes(term));
  const pages = Math.ceil(filtered.length / PAGE_SIZE);
  const selected = current[season];
  const error = actors.error ?? catalog?.error;

  function updateChoice(outfit?: string) {
    if (!actorId) return;
    const row = { ...current };
    if (outfit) row[season] = outfit;
    else delete row[season];
    const next = { ...choices };
    if (Object.keys(row).length) next[actorId] = row;
    else delete next[actorId];
    setChoices(next);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      setSaveStatus("选择已保存到本浏览器");
    } catch {
      setSaveStatus("无法保存草稿，请在离开前导出。");
    }
  }

  function exportChoices() {
    const blob = new Blob([JSON.stringify({ version: 1, default_outfits: choices }, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "kaibun-default-outfits.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <div className="min-h-dvh bg-background text-foreground">
    <header className="border-b">
      <div className="mx-auto flex min-h-16 max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 md:px-8">
        <a href="/" className="text-sm text-muted-foreground hover:text-foreground">← 返回怪文书</a>
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">已选 {count} 项</span>
          <Button disabled={!count} onClick={exportChoices}>导出 JSON</Button>
        </div>
      </div>
    </header>
    <main className="mx-auto max-w-7xl space-y-6 px-4 py-7 md:px-8 md:py-10">
      <div className="space-y-2">
        <p className="text-xs font-semibold tracking-widest text-muted-foreground">KAIBUN / 衣装图鉴</p>
        <h1 className="text-3xl font-semibold tracking-tight">默认衣装选择</h1>
        <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">先选角色和分类，再点击衣装卡片。每个分类可选一套，也可以留空。页面只保存草稿，不会改动作品或当前默认衣装。</p>
      </div>
      <section aria-label="衣装选择" className="space-y-4 rounded-xl border bg-card p-4 md:p-5">
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="space-y-1 text-sm font-medium">角色
            <select className={SELECT} value={actorId} disabled={!actors.data?.length} onChange={event => { setActorId(event.target.value); setPage(0); setSearch(""); }}>
              {!actors.data?.length && <option value="">{error ? "角色未加载" : "暂无可选角色"}</option>}
              {actors.data?.map(actor => <option key={actor.id} value={actor.id}>{actor.name}</option>)}
            </select>
          </label>
          <label className="space-y-1 text-sm font-medium">衣装类型
            <select className={SELECT} value={category} onChange={event => { setCategory(event.target.value); setPage(0); }}>
              <option value="all">全部衣装</option><option value="stand">日常衣装</option><option value="stand_costume">舞台衣装</option>
            </select>
          </label>
          <label className="space-y-1 text-sm font-medium">搜索衣装
            <Input value={search} placeholder="名称或衣装编号" onChange={event => { setSearch(event.target.value); setPage(0); }} />
          </label>
        </div>
        <div role="group" aria-label="默认衣装分类" className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {SEASONS.map(([key, label]) => {
            const outfitId = current[key];
            const title = catalog?.data?.find(outfit => outfit.id === outfitId)?.label ?? outfitId;
            return <button type="button" key={key} aria-pressed={key === season} onClick={() => setSeason(key)} title={title}
              className={`min-w-0 cursor-pointer rounded-lg border px-3 py-2 text-left focus-visible:outline-2 focus-visible:outline-ring ${key === season ? "border-primary bg-primary/10" : "hover:bg-muted"}`}>
              <span className="block text-sm font-semibold">{label}</span>
              <span className="block truncate text-xs text-muted-foreground">{title ?? "未选择"}</span>
            </button>;
          })}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span role="status">{saveStatus || "未选择的分类不会进入导出文件。"}</span>
          <Button variant="outline" size="sm" disabled={!selected} onClick={() => updateChoice()}>清空「{seasonLabel}」</Button>
        </div>
      </section>
      {error ? <div role="alert" className="flex items-center gap-3 text-sm">
        读取衣装清单失败：{error}<Button variant="outline" onClick={() => {
          if (actors.error) setActors({});
          else setCatalogs(previous => ({ ...previous, [actorId]: {} }));
          setAttempt(value => value + 1);
        }}>重试</Button>
      </div> : !actors.data || (actorId && !catalog?.data) ? <p role="status" className="text-sm text-muted-foreground">正在读取本地衣装……</p> : <section aria-label="本地衣装列表" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-medium">{filtered.length} 套衣装 · 正在选择「{seasonLabel}」</h2>
          {pages > 1 && <nav aria-label="衣装分页" className="flex items-center gap-2 text-sm">
            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</Button>
            <span>{page + 1} / {pages}</span>
            <Button variant="outline" size="sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>下一页</Button>
          </nav>}
        </div>
        {!filtered.length && <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">{catalog?.data?.length ? "没有符合筛选条件的衣装。" : "这个角色没有本地衣装。"}</p>}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
          {filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(outfit => <button key={outfit.id} type="button"
            aria-pressed={selected === outfit.id} onClick={() => updateChoice(outfit.id)}
            className={`group grid cursor-pointer content-start overflow-hidden rounded-xl border bg-card text-left focus-visible:outline-2 focus-visible:outline-ring ${selected === outfit.id ? "border-primary ring-1 ring-primary" : "hover:border-primary/60"}`}>
            <OutfitImage outfit={outfit} />
            <div className="space-y-1 p-3">
              <p className="text-xs font-medium leading-snug">{outfit.label}</p>
              <p className="break-all font-mono text-[11px] text-muted-foreground">{outfit.id}</p>
              <p className="text-xs font-medium">{selected === outfit.id ? `✓ 已选为「${seasonLabel}」` : `选为「${seasonLabel}」`}</p>
            </div>
          </button>)}
        </div>
      </section>}
    </main>
  </div>;
}
