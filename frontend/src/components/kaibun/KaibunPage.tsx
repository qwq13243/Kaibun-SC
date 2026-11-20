"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { StoryFineTune } from "./StoryFineTune";
import { performancePose, segmentAt } from "./performance";
import { StoryStage } from "./StoryStage";
import { stageCast, stagePoses, speechPassage, orderedStageCast } from "./stage";
import { StoryLibrary } from "./StoryLibrary";
import { StoryOutfits } from "./StoryOutfits";
import { SceneOutfits } from "./SceneOutfits";
import { StoryBackgrounds } from "./StoryBackgrounds";
import { StoryVoiceControls } from "./StoryVoiceControls";
import { ProductionStatus } from "./ProductionStatus";
import { StoryAudioCache, voiceKey as audioKey } from "./audioCache";
import { StoryImport } from "./StoryImport";
import { StoryTune } from "./StoryTune";
import { scenePresentation } from "./presentation";
import { reviewOutfitActions } from "./outfitActionReview";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { copy } from "@/i18n";
import { cn } from "@/lib/utils";
import { BackIcon, StoryIcon, StoryNewIcon, StoryPlayIcon, StoryPauseIcon, StoryPreviousIcon, StoryNextIcon, LetterSoundIcon, LetterMutedIcon } from "@/theme/icons";
import { Ja } from "@/theme/Ja";
import { request, migrateOutfitDefaults, composerOutfitMode, seasons, type OutfitMode, type OutfitPreferences, type Actor, type Story, type StoryBrief, type StoryLength, type Line } from "./api";

const t = copy.kaibun;

function pruneDeletedStoryDrafts(stories: StoryBrief[]) {
  const ids = new Set(stories.map(story => story.id));
  try {
    for (const key of Object.keys(localStorage)) {
      const match = /^(?:kaibun-position-|kaibun-tune-draft-)([a-f0-9]{32})$/.exec(key);
      if (match && !ids.has(match[1])) localStorage.removeItem(key);
    }
    const composer = JSON.parse(localStorage.getItem("kaibun-composer-draft") || "null");
    if (composer?.continuing && !ids.has(composer.continuing)) localStorage.removeItem("kaibun-composer-draft");
  } catch {}
}

export function KaibunPage() {
  const [actors, setActors] = useState<Actor[]>([]);
  const [stories, setStories] = useState<StoryBrief[]>([]);
  const [performancePreview,setPerformancePreview]=useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(()=>setPerformancePreview(false),[selected]);
  const [story, setStory] = useState<Story | null>(null);
  const [premise, setPremise] = useState("");
  const [cast, setCast] = useState<string[]>([]);
  const [details, setDetails] = useState("");
  const [outfits, setOutfits] = useState<Record<string, string>>({});
  const [outfitMode, setOutfitMode] = useState<OutfitMode>("default");
  const [continuing, setContinuing] = useState<string | null>(null);
  const [length, setLength] = useState<StoryLength>("short");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [voice, setVoice] = useState(false);
  const deleted = useRef(new Set<string>());
  const draftLoaded = useRef(false);
  const draftNeedsOutfitMigration = useRef(false);
  useEffect(() => {
    try {
      const draft = JSON.parse(localStorage.getItem("kaibun-composer-draft") || "null");
      if (draft) { draftNeedsOutfitMigration.current = draft.outfitPolicyVersion !== 1; setPremise(draft.premise || ""); setDetails(draft.details || ""); setCast(draft.cast || []); setOutfits(draft.outfits || {}); setOutfitMode(composerOutfitMode(draft.outfitMode)); setLength(draft.length || "short"); setContinuing(draft.continuing || null); }
    } catch {} finally { setTimeout(() => { draftLoaded.current = true; }, 0); }
  }, []);
  useEffect(() => {
    if (selected || !draftLoaded.current || !actors.length) return;
    try { localStorage.setItem("kaibun-composer-draft", JSON.stringify({ premise, details, cast, outfits, outfitMode, length, continuing, outfitPolicyVersion:1 })); } catch {}
  }, [selected, premise, details, cast, outfits, outfitMode, length, continuing, actors.length]);
  const readLibrary = useCallback(() => request<StoryBrief[]>("stories").then(items => { pruneDeletedStoryDrafts(items); setStories(items.filter(item => !deleted.current.has(item.id))); }), []);
  useEffect(() => {
    let active = true;
    Promise.all([request<Actor[]>("characters"), request<StoryBrief[]>("stories"), request<{ voice: boolean }>("health")])
      .then(([actors, stories, health]) => { if (active) { if (draftNeedsOutfitMigration.current) {setOutfits(current => migrateOutfitDefaults(current,actors));draftNeedsOutfitMigration.current=false;} setActors(actors); setStories(stories); setVoice(health.voice); pruneDeletedStoryDrafts(stories); } })
      .catch((e) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!selected) { setStory(null); return; }
    let active = true;
    let reading = false;
    const controller = new AbortController();
    const read = async () => {
      if (reading) return;
      reading = true;
      try {
        const doc = await request<Story>("stories/" + selected, undefined, controller.signal);
        if (active && !deleted.current.has(selected)) { setStory(doc); setError(""); }
      } catch(e) { if (active && !deleted.current.has(selected)) setError(e instanceof Error ? e.message : t.unavailable); }
      finally { reading = false; }
    };
    setStory(null); void read();
    const timer = setInterval(() => { void read(); void readLibrary().catch(() => {}); }, 3000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [selected, readLibrary]);

  async function create(tuning = false) {
    if (!premise.trim() || busy) return;
    setBusy(true); setError("");
    try {
      if (continuing) {
        await request("stories/" + continuing + "/continue", { instruction: details || premise, cast, outfits: Object.fromEntries(cast.filter(id => outfits[id]).map(id => [id, outfits[id]])), length, outfit_mode: outfitMode, tuning });
        setSelected(continuing); setContinuing(null); await readLibrary(); return;
      }
      const doc = await request<Story>("stories", { premise, cast, length, details, tuning, outfit_mode: outfitMode, outfits: Object.fromEntries(cast.filter(id => outfits[id]).map(id => [id, outfits[id]])) });
      setSelected(doc.id); setStory(doc); await readLibrary();
    } catch (e) { setError(e instanceof Error ? e.message : t.unavailable); }
    finally { setBusy(false); }
  }
  async function startContinuation(source: Story) {
    try {
      const saved = await request<Story & OutfitPreferences>("stories/" + source.id + "/pause-for-edit", {});
      setContinuing(saved.id);
      setCast(saved.outline?.cast ?? []);
      setOutfits(saved.requested_outfits ?? {});
      setOutfitMode(composerOutfitMode(saved.outfit_mode));
      setLength(saved.length);
      setPremise(saved.premise);
      setDetails("");
      setSelected(null);
    } catch (error) { setError(error instanceof Error ? error.message : t.unavailable); }
  }
  async function pin(item: StoryBrief) {
    try { await request("stories/" + item.id + "/pin", { pinned: !item.pinned }); await readLibrary(); }
    catch (e) { setError(e instanceof Error ? e.message : t.unavailable); }
  }
  async function remove(item: StoryBrief) {
    if (!window.confirm(`删除“${item.title || item.premise}”？这会一并清除该故事的配音、导出视频、演出缓存和备份。`)) return;
    try {
      await request("stories/" + item.id + "/delete", {});
      try {
        localStorage.removeItem("kaibun-position-" + item.id);
        localStorage.removeItem("kaibun-tune-draft-" + item.id);
        const composer = JSON.parse(localStorage.getItem("kaibun-composer-draft") || "null");
        if (composer?.continuing === item.id) localStorage.removeItem("kaibun-composer-draft");
      } catch {}
      deleted.current.add(item.id);
      setSelected(current => current === item.id ? null : current);
      setStory(current => current?.id === item.id ? null : current);
      setStories(current => current.filter(story => story.id !== item.id));
      await readLibrary();
    } catch (e) { setError(e instanceof Error ? e.message : t.unavailable); }
  }
  async function rename(item: StoryBrief) {
    const title = window.prompt("重命名", item.title || item.premise);
    if (!title?.trim()) return;
    try { await request("stories/" + item.id + "/rename", { title: title.trim() }); await readLibrary(); if (selected === item.id) setStory(await request<Story>("stories/" + item.id)); }
    catch (e) { setError(e instanceof Error ? e.message : t.unavailable); }
  }
  async function revise(item: Story) {
    try { const copy = await request<Story>("stories/" + item.id + "/revise", {}); setSelected(copy.id); setStory(copy); await readLibrary(); }
    catch (e) { setError(e instanceof Error ? e.message : t.unavailable); }
  }
  const library = <StoryLibrary stories={stories} selected={selected} onSelect={setSelected} onPin={item => { void pin(item); }} onDelete={item => { void remove(item); }} onRename={item => { void rename(item); }} />;

  return <div className="flex min-h-dvh flex-col bg-background text-foreground">
    <header className="flex min-h-16 items-center justify-between gap-4 border-b px-4 md:px-6">
      <div className="flex items-center gap-3"><StoryIcon className="size-5" /><span className="text-header">{t.title}</span></div>
      <nav className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1 text-sm text-muted-foreground">
        <a href="/outfit-review" className="hover:text-foreground">默认衣装选择</a>
        <a href="/about" className="flex items-center gap-1 hover:text-foreground"><BackIcon className="size-4" />使用说明与来源</a>
      </nav>
    </header>
    <div className="flex min-h-0 flex-1">
      <aside className="sticky top-0 hidden h-dvh w-72 shrink-0 overflow-y-auto border-r lg:block">
        <div className="flex items-center justify-between px-4 pt-5 pb-3"><h2 className="text-row-name">{t.library}</h2><Button variant="ghost" size="icon-lg" aria-label={t.newStory} onClick={() => { setContinuing(null); setSelected(null); }}><StoryNewIcon /></Button></div>
        {library}
      </aside>
      <main className="min-w-0 flex-1">
        <div className="flex items-center justify-between border-b px-4 py-2 lg:hidden"><details className="relative"><summary className="cursor-pointer py-1 text-sm">{t.library} · {stories.length}</summary><div className="absolute left-0 z-20 max-h-96 w-72 overflow-y-auto rounded-lg border bg-background">{library}</div></details><Button variant="ghost" onClick={() => { setContinuing(null); setSelected(null); }}><StoryNewIcon />{t.newStory}</Button></div>
        {error && <div role="alert" className="mx-auto mt-4 flex max-w-4xl items-center gap-3 rounded-lg border px-4 py-3 text-sm"><span className="min-w-0 flex-1">{error}</span><Button variant="outline" onClick={() => window.location.reload()}>{t.retry}</Button></div>}
        {selected ? story ? story.tuning && story.tuning !== "production" && !performancePreview ? <StoryTune key={story.id} story={story} actors={actors} onUpdated={setStory} onPreview={()=>setPerformancePreview(true)} /> : <>{performancePreview&&<div className="flex items-center gap-3 px-5 pt-4"><Button variant="outline" onClick={()=>setPerformancePreview(false)}>返回剧本精调</Button><span className="text-sm text-muted-foreground">演出预览 · 字幕播放</span></div>}<StoryView key={story.id} story={story} actors={actors} voice={performancePreview?false:voice} onUpdated={setStory} onRevise={() => { void revise(story); }} onContinue={() => { void startContinuation(story); }} onResume={() => { void request("stories/" + story.id + "/resume", {}).catch((e) => setError(e.message)); }} /></> : <p role="status" className="p-8 text-sm text-muted-foreground">{t.loading}</p> :
          <div className="mx-auto flex max-w-3xl flex-col gap-8 px-5 py-12 md:px-10 md:py-20">
            <div className="flex flex-col gap-3"><h1 className="text-3xl font-semibold tracking-tight md:text-4xl">{continuing ? "续写" : t.newStory}</h1></div>
            <StoryImport cast={cast} onImported={doc => {setSelected(doc.id);setStory(doc);void readLibrary();}} />
            <form className="flex flex-col gap-5" onSubmit={(event) => { event.preventDefault(); void create(); }}>
              <Textarea rows={4} value={premise} maxLength={2000} aria-label="故事设定" onChange={(e) => setPremise(e.target.value)} />
              <div className="flex flex-wrap items-center gap-2"><span className="mr-2 text-sm text-muted-foreground">{t.length}</span>{(["short", "standard", "half_hour", "hour_plus"] as const).map((value) => <Button type="button" variant={length === value ? "secondary" : "ghost"} key={value} aria-pressed={length === value} onClick={() => setLength(value)}>{t[value]}</Button>)}</div>
              <details><summary className="cursor-pointer text-sm">{t.cast}{cast.length ? " · " + cast.length : ""}</summary><div className="mt-3 flex flex-wrap gap-2">{actors.map((actor) => <button type="button" key={actor.id} aria-pressed={cast.includes(actor.id)} onClick={() => setCast((list) => list.includes(actor.id) ? list.filter((id) => id !== actor.id) : [...list, actor.id])} className={cn("flex items-center gap-2 rounded-full border px-3 py-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-40", cast.includes(actor.id) ? "bg-muted" : "hover:bg-muted/60")}><MarkedAvatar name={actor.name} src={actor.avatar_url} size="sm" />{actor.short_name ?? actor.name}</button>)}</div></details>
              <div className="flex flex-col gap-2 text-sm">
                <label>服装安排 <select className="ml-2 rounded border bg-background p-2" value={outfitMode} onChange={event => setOutfitMode(composerOutfitMode(event.target.value))}>
                  <option value="story">随剧情换装</option>
                  <option value="default">默认衣装</option>
                  <optgroup label="季节配装">{Object.entries(seasons).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</optgroup>
                </select></label>
                <p className="text-muted-foreground">单独手选的角色衣装优先。</p>
                {cast.length > 0 && <StoryOutfits actors={actors} cast={cast} outfits={outfits} onChange={(id, value) => setOutfits(current => ({ ...current, [id]: value }))} />}
              </div>
              <label className="flex flex-col gap-2 text-sm">{t.details}<Textarea rows={3} value={details} maxLength={12000} placeholder={t.detailsPlaceholder} onChange={event => setDetails(event.target.value)} /></label>
              <div className="flex items-center gap-4 border-t pt-5"><Button type="submit" size="lg" disabled={busy || premise.trim().length < 2 || !actors.length}>{busy ? <Spinner /> : <StoryIcon />}{busy ? t.generating : continuing ? "开始续写" : t.generate}</Button><Button type="button" variant="outline" disabled={busy || premise.trim().length < 2 || !actors.length} onClick={() => { void create(true); }}>剧本精调</Button></div>
            </form>
          </div>}
      </main>
    </div>
  </div>;
}

function StoryView({ story, actors, voice, onResume, onUpdated, onContinue, onRevise }: { story: Story; actors: Actor[]; voice: boolean; onResume: () => void; onUpdated: (story: Story) => void; onContinue: () => void; onRevise: () => void }) {
  const [watching, setWatching] = useState(false);
  const [script, setScript] = useState(false);
  const [fine, setFine] = useState(false);
  const [fineLine, setFineLine] = useState<string | null>(null);
  const [preview, setPreview] = useState<{line: Line; segment: number} | null>(null);
  const [jump, setJump] = useState<{id: string; token: number} | null>(null);
  const [actionError, setActionError] = useState("");
  const playerTop = useRef<HTMLDivElement>(null);
  const scriptPanel = useRef<HTMLDivElement>(null);
  const [currentLine, setCurrentLine] = useState<string | null>(null);
  const [locate, setLocate] = useState<{id: string; token: number} | null>(null);
  function openScript(edit: boolean, target?: string) {
    const entries = story.scenes.flatMap(scene => scene.lines.filter(line => line.speaker !== "narrator").map(line => ({line,scene})));
    let id = target ?? currentLine;
    if (!id) {
      let position = 0;
      try { position = Number(localStorage.getItem("kaibun-position-" + story.id)) || 0; } catch {}
      id = entries[Math.min(position, entries.length - 1)]?.line.id ?? null;
    }
    setScript(true);
    if (!id) return;
    setLocate({id, token: Date.now()});
    if (edit) {
      const entry = entries.find(entry => entry.line.id === id);
      if (!entry) return;
      setFineLine(id); setPreview(null);
      setJump({id, token: Date.now()});
    }
  }
  useEffect(() => {
    if (!script || !locate || !scriptPanel.current) return;
    const panel = scriptPanel.current;
    const timer = requestAnimationFrame(() => {
      const target = Array.from(panel.querySelectorAll<HTMLElement>("[data-script-line]")).find(node => node.dataset.scriptLine === locate.id);
      if (target) panel.scrollTop += target.getBoundingClientRect().top - panel.getBoundingClientRect().top;
    });
    return () => cancelAnimationFrame(timer);
  }, [script, locate]);
  const unfinished = story.status !== "ready";
  const actions = story.work?.actions;
  const outfitIssues = useMemo(() => reviewOutfitActions(story, actors).missing, [story, actors]);
  const firstOutfitIssue = outfitIssues.keys().next().value;
  function previewOutfitLine(id: string) {
    setFine(false);
    setWatching(true);
    openScript(false, id);
    setJump({id, token: Date.now()});
  }
  const names = Object.fromEntries(actors.map((actor) => [actor.id, actor.name]));
  names.producer = t.producer; names.narrator = t.narrator;
  for (const guest of story.outline?.guests ?? []) names[guest.id] = guest.name;
  return <div className={cn("mx-auto flex max-w-7xl flex-col gap-5 px-4 py-6 md:px-8 md:py-8", script && watching && "h-dvh overflow-hidden")}>
    <div className="flex flex-wrap items-start justify-between gap-4"><div className="flex min-w-0 flex-1 flex-col gap-2"><p className="text-meta text-muted-foreground">{t.title} · {t.status[story.status]}</p><h1 className="text-2xl font-semibold tracking-tight">{story.title || story.premise}</h1></div><Button variant="ghost" onClick={() => { if (script) setScript(false); else openScript(false); }}>{script ? t.closeScript : t.read}</Button></div>
    {unfinished && <div role="status" className="flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm">{story.status === "generating" || story.status === "preparing" || story.status === "queued" ? <Spinner /> : null}<span className="flex-1">{story.phase.replace(/ · 配音.*$/, "")}{story.outline ? " · " + t.scene(story.scenes.length, story.outline.scenes.length) : ""}</span>{actions?.pause_writing ? <Button variant="outline" onClick={() => { void request<Story>("stories/" + story.id + "/stop", {}).then(onUpdated).catch((e) => setActionError(e.message)); }}>暂停写作</Button> : null}{actions?.resume ? <Button variant="outline" onClick={onResume}>{story.work?.writing === "complete" ? "继续制作" : t.resume}</Button> : null}</div>}
    <ProductionStatus story={story} onUpdated={onUpdated} onLocate={id=>{setWatching(true);setJump({id,token:Date.now()});}} />
    {story.status === "generating" && story.writing_notice && <details className="text-sm text-muted-foreground"><summary>创作正在恢复</summary><p>{story.writing_notice}</p></details>}
    {story.writing_draft && <details className="rounded-lg border px-4 py-3 text-sm"><summary className="cursor-pointer">第 {story.writing_draft.scene_number} 幕草稿 · 已保存 {story.writing_draft.lines.length} 句，完成本幕后准备配音</summary><p className="mt-2 text-muted-foreground">继续时会读取全部已完成正文及这些台词，从保存处接写。</p>{story.writing_draft.lines.map(line => <div key={line.id} className="mt-3"><p className="font-medium">{names[line.speaker] ?? line.speaker}</p><p>{line.ja}</p><p className="text-muted-foreground">{line.zh}</p></div>)}</details>}
    <div ref={playerTop} className={cn(script && watching && "sticky top-0 z-30 bg-background")} />{actionError && <p role="alert">{actionError}</p>}{story.scenes.length ? <div className={cn(script && watching && "sticky top-0 z-30 bg-background")} >{!watching ? <div className="flex min-h-72 flex-col items-center justify-center gap-5 rounded-2xl bg-muted/50"><div className="flex -space-x-2">{(story.outline?.cast ?? []).map((id) => { const actor = actors.find((a) => a.id === id); return actor ? <MarkedAvatar key={id} name={actor.name} src={actor.avatar_url} size="list" /> : null; })}</div><p className="text-sm text-muted-foreground">{t.scene(story.scenes.length, story.outline?.scenes.length ?? 0)}</p><Button size="lg" onClick={() => setWatching(true)}><StoryPlayIcon />{unfinished ? t.watchFirst : t.watch}</Button></div> : <StoryPlayer story={story} actors={actors} names={names} voice={voice} jump={jump} compact={script} editing={fine} onUpdated={onUpdated} preview={fine ? preview : null} onPosition={setCurrentLine} />}</div> : <div className="flex min-h-72 items-center justify-center rounded-2xl bg-muted/50"><StoryIcon className="size-10 text-muted-foreground" /></div>}
    {actions?.edit && <Button variant="outline" onClick={() => { if (fine) setFine(false); else { setFine(true); setWatching(true); openScript(true); } }}>微调</Button>}
    {!script && story.outline && <details className="rounded-lg border px-4 py-3"><summary className="cursor-pointer text-sm">{t.outline}</summary><p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-muted-foreground">{story.outline.opening?.trim() || story.premise}</p></details>}
    {!script && actions?.edit && <StoryBackgrounds story={story} onUpdated={onUpdated} />}
    {!script && actions?.edit && story.outline && <SceneOutfits story={story} actors={actors} onUpdated={onUpdated} onPreview={previewOutfitLine} />}
    {firstOutfitIssue && <div className="flex flex-wrap items-center gap-2 text-sm text-destructive" role="status"><span>{outfitIssues.size} 句台词的部分动作不适用于当前衣装，播放时使用替代动作。</span><Button variant="outline" onClick={() => previewOutfitLine(firstOutfitIssue)}>定位第一处衣装演出</Button></div>}
    {!script && (actions?.continue_story || actions?.export) && <div className="flex flex-wrap items-center gap-2">{actions.continue_story && <><Button variant="outline" onClick={onContinue}>续写</Button><Button variant="outline" onClick={onRevise}>调整剧本</Button></>}{actions.export && <StoryExport storyId={story.id} scenes={story.scenes} onReview={id=>{setFine(true);setWatching(true);openScript(true,id);}} />}</div>}
    {script && <div ref={scriptPanel} className="min-h-0 flex-1 overflow-y-auto">{story.scenes.map(scene => <section key={scene.index} className="flex flex-col gap-4 border-t pt-5">
      <h2 className="text-header">第{scene.index + 1}幕 · {scene.place}</h2>
      {scene.lines.filter(line => line.speaker !== "narrator").map(line => <div key={line.id} data-script-line={line.id}>
        <button type="button" className={cn("flex w-full gap-3 rounded-lg p-2 text-left hover:bg-muted", outfitIssues.has(line.id) && "border border-destructive/50 text-destructive")} onClick={() => { setWatching(true); setJump({id: line.id, token: Date.now()}); if (fine) { setFineLine(line.id); setPreview(null); } }}>
          <span className="w-24 shrink-0 text-subtitle text-muted-foreground">{line.speakers?.length ? "多人" : names[line.speaker] ?? line.speaker}</span>
          <div><p className="text-japanese-line"><Ja>{line.ja}</Ja></p><p className={cn("text-subtitle", !outfitIssues.has(line.id) && "text-muted-foreground")}>{line.zh}</p>{outfitIssues.has(line.id) && <p className="text-meta">{outfitIssues.get(line.id)?.map(id => names[id] ?? id).join("、")}：当前衣装使用替代动作</p>}</div>
        </button>
        {fine && fineLine === line.id && <StoryFineTune key={line.id} story={story} scene={scene} line={line} actors={actors} names={names} onPreview={setPreview} onUpdated={onUpdated} onDeleted={updated => { setPreview(null); const next = updated.scenes.flatMap(s => s.lines).find(l => l.speaker !== "narrator"); setFineLine(next?.id ?? null); if (next) setJump({id: next.id, token: Date.now()}); onUpdated(updated); }} />}
      </div>)}
    </section>)}</div>}
  </div>;
}

function StoryExport({ storyId, scenes, onReview }: { storyId: string; scenes: Story["scenes"]; onReview: (id:string)=>void }) {
  const [scope, setScope] = useState("");
  const suffix = scope ? `?scene=${scope}` : "";
  const [job, setJob] = useState<{status: string; message: string; progress: number}>({status: "idle", message: "", progress: 0});
  const [error, setError] = useState("");
  const [reviewLine, setReviewLine] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    const update = () => request<typeof job>(`stories/${storyId}/export${suffix}`).then(value => { if (active) setJob(value); }).catch(() => {});
    void update();
    const timer = setInterval(update, 2500);
    return () => { active = false; clearInterval(timer); };
  }, [storyId, suffix]);
  async function start() {
    setError("");
    setReviewLine(null);
    try {
      const check = await request<{missing_audio:string[];performance_review:string[];untranslated:string[];ready:boolean}>(`stories/${storyId}/export-check${suffix}`);
      if (check.untranslated.length) throw new Error("导入剧本还有未整理的日文和中文台词，请先完成剧本整理。");
      if (check.performance_review.length) {
        setReviewLine(check.performance_review[0]);
        throw new Error(`所选范围有 ${check.performance_review.length} 句的演出区间需要确认。请在微调中检查并保存，再导出视频。`);
      }
      setJob(await request<typeof job>(`stories/${storyId}/export${suffix}`, {}));
    }
    catch (e) { setError(e instanceof Error ? e.message : "导出失败，请重试"); }
  }
  async function stop() { try { setJob(await request<typeof job>(`stories/${storyId}/export/stop${suffix}`, {})); } catch (e) { setError(e instanceof Error ? e.message : "暂停失败，请重试"); } }
  return <><select aria-label="导出范围" disabled={job.status === "working"} value={scope} onChange={e => {setScope(e.target.value); setError(""); setReviewLine(null); setJob({status:"idle",message:"",progress:0});}}><option value="">全部导出</option>{scenes.map((scene,i) => <option key={i} value={String(i)}>第{i+1}幕 · {scene.place}</option>)}</select><Button variant="outline" disabled={job.status === "working"} onClick={() => { void start(); }}>导出视频</Button>{job.status === "working" && <><span role="status" className="text-sm text-muted-foreground">{job.message} · {job.progress}%</span><Button variant="ghost" onClick={() => { void stop(); }}>暂停</Button></>}{job.status === "ready" && <a className="text-sm underline" download href={`/api/kaibun/stories/${storyId}/export/download${suffix}`}>下载视频</a>}{job.status === "interrupted" && <span className="text-sm text-muted-foreground">{job.message}</span>}{(error || job.status === "failed") && <span role="alert" className="text-sm text-destructive">{error || job.message}</span>}{reviewLine && <Button variant="outline" onClick={()=>onReview(reviewLine)}>定位第一句待确认台词</Button>}</>;
}

function StoryPlayer({ story, actors, names, voice, jump, compact, editing, preview, onPosition, onUpdated }: { story: Story; actors: Actor[]; names: Record<string, string>; voice: boolean; jump: { id: string; token: number } | null; compact: boolean; editing: boolean; preview: {line: Line; segment: number} | null; onPosition: (id: string) => void; onUpdated:(story:Story)=>void }) {
  const [index, setIndex] = useState(() => {
    const position = jump ? story.scenes.flatMap((scene) => scene.lines.filter((line) => line.speaker !== "narrator")).findIndex((line) => line.id === jump.id) : 0;
    if (jump) return Math.max(0, position);
    try { return Math.max(0, Number(localStorage.getItem("kaibun-position-" + story.id)) || 0); } catch { return 0; }
  });
  const [playing, setPlaying] = useState(!jump);
  useEffect(() => { if (editing && preview) setPlaying(false); }, [editing, preview]);
  const [sound, setSound] = useState(voice);
  const [speaking, setSpeaking] = useState<string[]>([]);
  const [segmentIndex, setSegmentIndex] = useState(0);
  const [notice, setNotice] = useState("");
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const lines = story.scenes.flatMap((scene) => scene.lines.filter((line) => line.speaker !== "narrator").map((line) => ({ ...line, scene })));
  const savedLine = lines[index];
  const isPreview = editing && preview && preview.line.id === savedLine?.id;
  const line = isPreview ? {...preview.line, scene: savedLine.scene} : savedLine;
  const shot = !isPreview ? savedLine?.shot : undefined;
  const voiceKey = savedLine ? JSON.stringify([audioKey(savedLine), savedLine.voice_targets]) : "";
  useEffect(() => { if (line) onPosition(line.id); }, [line?.id, onPosition]);
  useEffect(() => { try { if (lines.length) localStorage.setItem("kaibun-position-" + story.id, String(Math.min(index, lines.length - 1))); } catch {} }, [index, story.id, lines.length]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input,textarea,select,[contenteditable=true]")) return;
      if ((event.code === "Space" || event.key === " " || event.key === "Spacebar") && !event.ctrlKey && !event.altKey && !event.metaKey) {
        event.preventDefault();
        event.stopPropagation();
        if (!event.repeat) setPlaying(value => !value);
      }
      else if (event.key === "ArrowLeft") { event.preventDefault(); setIndex(value => Math.max(0, value - 1)); }
      else if (event.key === "ArrowRight" || event.key === "Enter") { event.preventDefault(); setIndex(value => Math.min(lines.length, value + 1)); }
    };
    window.addEventListener("keydown", key, { capture: true });
    return () => window.removeEventListener("keydown", key, { capture: true });
  }, [lines.length]);
  useEffect(() => {
    setSegmentIndex(0);
    if (jump) { const position = lines.findIndex((l) => l.id === jump.id); if (position >= 0) { setIndex(position); setPlaying(false); } }
  }, [jump]);
  const audioCache = useRef<StoryAudioCache | null>(null);
  useEffect(() => {
    const cache = new StoryAudioCache();
    audioCache.current = cache;
    return () => { cache.close(); audioCache.current = null; };
  }, []);
  const next = useCallback(() => setIndex((value) => value + 1), []);
  const storyRef = useRef(story); storyRef.current = story;
  const audioFor = useCallback((lineId: string) => {
    const source = storyRef.current.scenes.flatMap(s => s.lines).find(l => l.id === lineId.split("~")[0]);
    if (!source || !audioCache.current) return Promise.reject(new Error(t.voiceFailed));
    return audioCache.current.get({ key: audioKey(source, lineId), url: "/api/kaibun/stories/" + story.id + "/audio/" + lineId });
  }, [story.id]);
  const audioWindow = sound ? [...lines.slice(index, index + 4), ...lines.slice(Math.max(0, index - 1), index)]
    .flatMap(line => Object.values(line.voice_targets ?? {})
      .map(id => ({key: audioKey(line, id), url: "/api/kaibun/stories/" + story.id + "/audio/" + id}))) : [];
  const windowKey = JSON.stringify(audioWindow);
  useEffect(() => {
    const cache = audioCache.current;
    if (!cache) return;
    cache.keep(audioWindow.map(target => target.key));
    // Let rapid repeated seeks settle; foreground playback starts immediately.
    const timer = setTimeout(() => { void cache.prefetch(audioWindow); }, 100);
    return () => clearTimeout(timer);
  }, [windowKey]);
  const [transitioning, setTransitioning] = useState(false);
  const [transitionEffect, setTransitionEffect] = useState("");
  const previousLine = useRef<{scene: number; id: string} | null>(null);
  useEffect(() => {
    if (!line) return;
    const previous = previousLine.current;
    previousLine.current = {scene: line.scene.index, id: line.id};
    const effect = previous && previous.scene !== line.scene.index ? line.scene.transition : line.scene.line_transitions?.[line.id];
    if (!playing || !previous || previous.id === line.id || !effect || effect === "cut") {setTransitioning(false);return;}
    setTransitionEffect(effect); setTransitioning(true);
    const duration = previous.scene !== line.scene.index ? line.scene.transition_length : line.scene.line_transition_lengths?.[line.id];
    const timer = setTimeout(() => setTransitioning(false), ({short:400,medium:800,long:1400} as const)[duration || "short"]);
    return () => clearTimeout(timer);
  }, [line?.id]);
  useEffect(() => {
    if (!playing || !savedLine || transitioning) return;
    const line = savedLine;
    let active = true;
    let audio: HTMLAudioElement[] = [];
    let timer: ReturnType<typeof setTimeout> | undefined;
    setNotice(""); setSpeaking([]); setSegmentIndex(0);
    let segmentTimer: ReturnType<typeof setInterval> | undefined;
    const subtitleTimer = () => { const duration = line.kind === "thought" ? Math.min(20000, Math.max(5000, line.zh.length * 150)) : Math.min(12000, Math.max(3000, line.zh.length * 115)); const started = Date.now(); clearInterval(segmentTimer); segmentTimer = setInterval(() => setSegmentIndex(segmentAt(line.performance_segments, (Date.now() - started) / duration)), 100); timer = setTimeout(() => { if (active) next(); }, line.kind === "thought" ? Math.min(20000, Math.max(5000, line.zh.length * 150)) : Math.min(12000, Math.max(3000, line.zh.length * 115))); };
    const targets = Object.entries(line.voice_targets ?? {});
    if (!sound || !targets.length) subtitleTimer();
    else {
      Promise.allSettled(targets.map(async ([actor, id]) => ({actor, url: await audioFor(id)}))).then(results => {
        if (!active) return;
        const tracks = results.flatMap(result => result.status === "fulfilled" ? [result.value] : []);
        if (tracks.length < targets.length) setNotice(tracks.length ? "部分配音暂不可用" : t.voiceFailed);
        if (!tracks.length) { subtitleTimer(); return; }
        audio = tracks.map(({url}) => new Audio(url));
        segmentTimer = setInterval(() => { const duration = Math.max(...audio.map(a => Number.isFinite(a.duration) ? a.duration : 0)); const elapsed = Math.max(...audio.map(a => a.currentTime)); if(duration > 0) setSegmentIndex(segmentAt(line.performance_segments, elapsed / duration)); }, 100);
        let remaining = audio.length;
        let played = false;
        let failures = targets.length - tracks.length;
        audio.forEach((player, index) => {
          const {actor} = tracks[index];
          let finished = false;
          const finish = (failed: boolean) => {
            if (!active || finished) return;
            finished = true;
            setSpeaking(current => current.filter(id => id !== actor));
            if (failed) setNotice(++failures < targets.length ? "部分配音暂不可用" : t.voiceFailed);
            if (--remaining === 0) {
              clearInterval(segmentTimer);
              if (played) timer = setTimeout(next, 450);
              else subtitleTimer();
            }
          };
          player.onplaying = () => { if (active && !finished) { played = true; setSpeaking(current => [...new Set([...current, actor])]); } };
          player.onended = () => finish(false);
          player.onerror = () => finish(true);
          void player.play().catch(() => finish(true));
        });
      });
    }
    return () => { active = false; clearTimeout(timer); clearInterval(segmentTimer); audio.forEach(player => player.pause()); setSpeaking([]); };
  }, [voiceKey, playing, sound, audioFor, next, transitioning]);
  const cast = story.outline?.cast ?? [];
  const previewLines = !shot && line ? line.scene.lines.map(entry => entry.id === line.id ? line : entry) : [];
  const previewIndex = previewLines.findIndex(entry => entry.id === line?.id);
  const focused = shot?.cast ?? orderedStageCast(line?.screen_cast ? line.screen_cast : line?.speakers?.length ? cast.filter(id => line.speakers?.includes(id)).slice(0, 7) : stageCast(previewLines, previewIndex, cast, 5), line?.actor_order);
  const passage = line ? speechPassage(line.scene.lines, line.scene.lines.findIndex(l => l.id === line.id)) : null;
  const presentation = line ? scenePresentation(line.scene, line.id) : null;
  const sceneActors = actors.map(actor => {
    const costume = presentation?.wardrobe[actor.id];
    return costume ? { ...actor, stand: costume, stand_bounds: costume.bounds ?? actor.stand_bounds } : actor;
  });
  const poses = shot ? {...shot.poses} : stagePoses(previewLines, previewIndex, cast, 5, story.id);
  const segment = line?.performance_segments?.[editing && preview?.line.id === line.id && !playing ? preview.segment : segmentIndex];
  if (line) for (const id of focused) poses[id] = performancePose(line, segment, id, poses[id]);
  const visible = focused.flatMap(id => { const actor = sceneActors.find(a => a.id === id); return actor ? [actor] : []; });
  return <div className="overflow-hidden rounded-2xl border">
    <div className={cn("relative flex flex-col bg-linear-to-b from-muted to-background transition-opacity duration-300", transitioning && transitionEffect === "fade" && "opacity-0", compact ? "h-[min(35vh,20rem)] min-h-36" : "h-[min(60vh,36rem)] min-h-72")}>
      {presentation?.background && <img key={presentation.background.id} data-story-background={presentation.background.id} src={presentation.background.url} alt="" className="pointer-events-none absolute inset-0 size-full object-cover" />}
      <div className="z-20 flex items-center justify-between gap-3 bg-background/85 px-4 py-3 text-meta text-muted-foreground"><span>{line ? "第" + (story.scenes.indexOf(line.scene) + 1) + "幕" : ""}</span><span>{line?.scene.place ?? ""}</span></div>
      {line && <StoryStage actors={visible} allActors={sceneActors.filter(actor => cast.includes(actor.id))} line={line} poses={poses} speaking={line.kind === "thought" ? [] : speaking} reducedMotion={reducedMotion} />}
      {!line && <div className="z-10 m-auto flex max-w-md flex-col items-center gap-4 rounded-xl bg-background/95 p-6 text-center"><p className="text-body">{story.work?.writing === "complete" ? t.end : story.work?.writing === "writing" ? t.waiting : "已播完目前保存的剧本。"}</p>{story.work?.writing === "complete" && <Button onClick={() => { setIndex(0); setPlaying(true); }}>{t.replay}</Button>}</div>}
    </div>
    {!editing && <div className="relative flex min-h-40 cursor-pointer flex-col gap-2 border-t bg-background px-5 py-4 md:px-8" onClick={event => { const rect = event.currentTarget.getBoundingClientRect(); setIndex(value => event.clientX < rect.left + rect.width / 2 ? Math.max(0, value - 1) : Math.min(lines.length, value + 1)); }}>
      <div className="flex items-center justify-between gap-3"><span className="text-row-name">{line ? line.speakers?.length ? "多人" : names[line.speaker] ?? line.speaker : ""}{line?.kind === "thought" && <span className="ml-2 text-meta text-muted-foreground">{t.thought}</span>}</span><span className="text-meta text-muted-foreground">{t.line(Math.min(index + 1, lines.length), lines.length)}</span></div>
      {line && <><p className="text-japanese-line leading-7"><Ja>{passage?.ja ?? line.ja}</Ja></p><p className="text-sm leading-6 text-muted-foreground">{passage?.zh ?? line.zh}</p></>}
      <p role="status" className="min-h-4 text-meta text-muted-foreground">{notice}</p>
    </div>}
    <div className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2">
      <div className="flex flex-wrap items-center gap-2"><Button variant="ghost" aria-pressed={sound} disabled={!voice} onClick={() => setSound(!sound)}>{sound ? <LetterSoundIcon /> : <LetterMutedIcon />}{sound ? t.sound : t.muted}</Button><StoryVoiceControls story={story} lineId={savedLine?.id} voice={voice} onUpdated={onUpdated} onAction={()=>setPlaying(false)} /></div>
      <div className="flex gap-2"><Button variant="ghost" size="icon-lg" aria-label={t.previous} disabled={index === 0} onClick={() => setIndex(Math.max(0, index - 1))}><StoryPreviousIcon /></Button><Button variant="secondary" size="icon-lg" aria-label={playing ? t.pause : t.play} onClick={() => setPlaying(!playing)}>{playing ? <StoryPauseIcon /> : <StoryPlayIcon />}</Button><Button variant="ghost" size="icon-lg" aria-label={t.next} disabled={index >= lines.length} onClick={next}><StoryNextIcon /></Button></div>
    </div>
  </div>;
}
