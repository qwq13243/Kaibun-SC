import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { StoryCastOrder } from "./StoryCastOrder";
import { stageCast, stagePoses } from "./stage";
import { timedSegments } from "./performance";
import { request, RequestError, type Actor, type Line, type PerformanceSegment, type Scene, type Story } from "./api";
import type { Emotion } from "@/lib/standAnimations";
import { standCatalog } from "@/lib/standCatalog";
import { scenePresentation } from "./presentation";

const moods: Emotion[] = ["calm", "happy", "shy", "surprised", "sad", "angry", "thinking"];
const labels = {calm:"平静",happy:"开心",shy:"害羞",surprised:"惊讶",sad:"难过",angry:"生气",thinking:"思考"};

export function StoryFineTune({story, scene, line, actors, names, onPreview, onUpdated, onDeleted}: {
  story: Story; scene: Scene; line: Line; actors: Actor[]; names: Record<string,string>;
  onPreview: (preview: {line: Line; segment: number}) => void; onUpdated: (story: Story) => void; onDeleted: (story: Story) => void;
}) {
  const [draft, setDraft] = useState<Line>(() => ({...line, screen_cast: line.screen_cast ?? stageCast(scene.lines, scene.lines.indexOf(line), story.outline?.cast || [])}));
  const [segments, setSegments] = useState<PerformanceSegment[]>(() => line.performance_segments?.length && line.performance_segments.every(s => typeof s.ja === "string") ? structuredClone(line.performance_segments) : [{ja:line.ja, zh:line.zh, emotion:line.emotion, expression:line.expression,mouth_action:line.mouth_action,rest_mouth_action:line.rest_mouth_action,body_action:line.body_action,body_mood:line.body_mood, actor_states:structuredClone(line.actor_states || {})}]);
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const token = useRef(crypto.randomUUID());
  const editBase = useRef(story.base_fingerprint);
  const cursors = useRef<Record<string, number>>({});
  const cast = story.outline?.cast || [];
  const order = draft.actor_order || cast;
  const speakers = draft.speakers?.length ? draft.speakers : [draft.speaker];
  const inherited = stagePoses(scene.lines, scene.lines.findIndex(l=>l.id===line.id),cast,5,story.id);
  const currentWardrobe=scenePresentation(scene,line.id).wardrobe;
  function resolvedSegments() {
    return timedSegments(segments.map(seg => ({...seg, actor_states:Object.fromEntries((draft.screen_cast || []).flatMap(id => {
      const state = {...draft.actor_states?.[id],...seg.actor_states?.[id]};
      return Object.keys(state).length ? [[id,state]] : [];
    }))})));
  }
  useEffect(() => { onPreview({line: {...draft, ja:segments.map(s => s.ja).join(""), zh:segments.map(s => s.zh).join(""), performance_segments:resolvedSegments()}, segment:active}); }, [draft, segments, active, onPreview]);
  function change(index: number, patch: Partial<PerformanceSegment>) { setActive(index); setSegments(v => v.map((s,i) => i === index ? {...s,...patch} : s)); }
  function split(index: number) {
    const seg = segments[index], ja = cursors.current[`${index}-ja`], zh = cursors.current[`${index}-zh`];
    if (!ja || ja >= seg.ja.length || !zh || zh >= seg.zh.length) { setError("请先在这个区间的日文、中文中分别点击要划分的位置。"); return; }
    setError(""); setSegments(v => v.flatMap((s,i) => i === index ? [{...s,ja:s.ja.slice(0,ja),zh:s.zh.slice(0,zh)},{...structuredClone(s),ja:s.ja.slice(ja),zh:s.zh.slice(zh)}] : [s])); setActive(index + 1);
  }
  async function save(regenerate: boolean) {
    setBusy(true); setError("");
    try {
      const updated = await request<Story>(`stories/${story.id}/lines/${line.id}/fine-tune`, {...draft, base_fingerprint:editBase.current, ja:segments.map(s => s.ja).join(""), zh:segments.map(s => s.zh).join(""), performance_segments:resolvedSegments(), speakers, edit_token:token.current, regenerate});
      token.current = crypto.randomUUID(); editBase.current = updated.base_fingerprint; onUpdated(updated);
    } catch(e) {
      // The text was committed before synthesis failed. Retry against that exact saved version.
      if (e instanceof RequestError && e.savedFingerprint) editBase.current = e.savedFingerprint;
      setError(e instanceof Error ? e.message : "保存失败"); token.current = crypto.randomUUID();
    }
    finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-4 rounded-lg border p-4">
    <fieldset className="flex flex-wrap gap-2"><legend>出场人物与站位</legend><StoryCastOrder actors={actors.filter(a => cast.includes(a.id))} order={order} selected={draft.screen_cast || []} onOrder={actor_order => setDraft(v => ({...v,actor_order}))} onSelected={screen_cast => setDraft(v => ({...v,screen_cast}))} /></fieldset>
    <fieldset className="flex flex-wrap gap-2"><legend>发言配音</legend>{[...cast,"producer"].map(id => <label key={id} className="flex items-center gap-1 rounded-lg border px-3 py-2 text-sm"><input type="checkbox" checked={speakers.includes(id)} onChange={e => {
      const people = e.target.checked ? id === "producer" ? [id] : [...speakers.filter(s => s !== "producer"),id] : speakers.filter(s => s !== id);
      if (people.length) setDraft(v => ({...v,speaker:people[0],speakers:people.length > 1 ? people : undefined}));
    }} />{names[id]}</label>)}</fieldset>
    {speakers.includes("fuyuko") && <label>声线 <select aria-label="声线" value={draft.voice_style || "natural"} onChange={e => setDraft(v => ({...v,voice_style:e.target.value as "natural" | "idol"}))}><option value="natural">本音</option><option value="idol">夹子音</option></select></label>}
    {speakers.includes('nichika') && <label>日花声线 <select aria-label="日花声线" value={draft.nichika_voice_style || 'natural'} onChange={e=>setDraft(v=>({...v,nichika_voice_style:e.target.value as 'natural' | 'idol'}))}><option value="natural">本音</option><option value="idol">偶像声线</option></select></label>}
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.listener_state_persist !== false} onChange={e=>setDraft(v=>({...v,listener_state_persist:e.target.checked}))} />将非发言角色的表情与动作延续到后续台词（同场景内，直到本人发言、离开画面或被新设置覆盖）</label>
    <div className="flex flex-col gap-3"><p>句内演出区间</p><p className="text-sm text-muted-foreground">整句只播放一次。分别在日文、中文中点击划分位置，再划分区间。点击预览即可检查人物演出，无需生成语音。播放切换位置按日文长度估算。</p>
      {line.performance_invalidated && <p role="alert" className="text-sm text-destructive">台词已改变，预览暂用原第一区间的动作。请确认或重新划分区间，并保存后再导出。</p>}
      {segments.map((seg,i) => <div key={i} className="flex flex-col gap-3 rounded-lg border p-3" data-performance-segment={i}>
        <div className="flex flex-wrap items-center gap-2"><span>区间 {i+1}</span><Button variant={active === i ? "secondary" : "outline"} onClick={() => setActive(i)}>预览区间 {i+1}</Button><Button variant="outline" disabled={busy || segments.length >= 20} onClick={() => split(i)}>在光标处划分</Button>{i > 0 && <Button variant="ghost" disabled={busy} onClick={() => {setSegments(v => v.flatMap((s,j) => j === i-1 ? [{...s,ja:s.ja+v[i].ja,zh:s.zh+v[i].zh}] : j === i ? [] : [s]));setActive(i-1);}}>并入上一区间</Button>}</div>
        {(["ja","zh"] as const).map(lang => <label key={lang} className="flex flex-col gap-1 text-sm">{lang === "ja" ? "日文" : "中文"}<Textarea aria-label={`区间${i+1}${lang === "ja" ? "日文" : "中文"}`} value={seg[lang]} onSelect={e => {cursors.current[`${i}-${lang}`] = e.currentTarget.selectionStart;}} onChange={e => change(i,{[lang]:e.target.value})} /></label>)}
        {(draft.screen_cast || []).map(id => {
          const state = {mouth_action:undefined as string|null|undefined,rest_mouth_action:undefined as string|null|undefined,emotion:(inherited[id]?.mood || "calm") as Emotion,expression:inherited[id]?.expression,body_action:undefined as string|null|undefined,body_mood:inherited[id]?.bodyMood as Emotion | undefined,...(speakers.includes(id) ? {emotion:seg.emotion,expression:seg.expression,mouth_action:seg.mouth_action,rest_mouth_action:seg.rest_mouth_action,body_action:seg.body_action,body_mood:seg.body_mood} : {}),...draft.actor_states?.[id],...seg.actor_states?.[id]};
          const row=standCatalog.characters.find(c=>c.character_id===id);
          const outfit=currentWardrobe[id] || actors.find(a=>a.id===id)?.stand;
          const clipNames=outfit?.animation_names || (row?.outfit===outfit?.id?row?.animations.map(a=>a.name):[]) || [];
          const faces=standCatalog.expressions.filter(e=>clipNames.includes(e.name));
          const bodies=(outfit?.hold_actions || row?.animations.filter(a=>a.sustained&&clipNames.includes(a.name)).map(a=>a.name) || []).filter(n=>!/^touch\d*$/.test(n));
          const mouths=clipNames.filter(n=>n.startsWith("lip_"));
          const talking=mouths.filter(n=>!/_s\d*$/.test(n));
          const update = (patch: Partial<typeof state>) => change(i,{actor_states:{...seg.actor_states,[id]:{...seg.actor_states?.[id],...patch}}});
          const missingOption = (action: string | null | undefined, options: string[]) => action && !options.includes(action) && <option value={action}>{outfit?.animation_names && !clipNames.includes(action) ? "当前衣装不支持" : "原设置"} · {action}</option>;
          return <div key={id} className="flex flex-wrap items-center gap-3 text-sm"><span>{names[id]}{speakers.includes(id) ? " · 发言" : ""}</span>{line.performance_actions?<span>剧本情绪：{labels[state.emotion||"calm"]}</span>:<label>情绪 <select aria-label={`区间${i+1}${names[id]}情绪`} value={state.emotion || "calm"} onChange={e => update({emotion:e.target.value as Emotion})}>{moods.map(m => <option key={m} value={m}>{labels[m]}</option>)}</select></label>}<label>表情 <select aria-label={`区间${i+1}${names[id]}表情`} value={state.expression || ""} onChange={e=>update({expression:e.target.value||null})}><option value="">沿用自动演出</option>{missingOption(state.expression, clipNames)}{clipNames.includes("blank")&&<option value="blank">保留动作原生表情</option>}{faces.map(f=><option key={f.name} value={f.name}>{f.label} · {f.name}</option>)}</select></label>{!line.performance_actions&&<label>动作 <select aria-label={`区间${i+1}${names[id]}动作`} value={state.body_mood || ""} onChange={e => update({body_mood:e.target.value as Emotion || null,body_action:null})}><option value="">随情绪</option>{moods.map(m => <option key={m} value={m}>{m === "calm" ? "待机" : labels[m]}</option>)}</select></label>}<label>说话口型 <select aria-label={`区间${i+1}${names[id]}说话口型`} value={state.mouth_action||""} onChange={e=>update({mouth_action:e.target.value||null})}><option value="">沿用自动演出</option>{missingOption(state.mouth_action, talking)}{talking.map(n=><option key={n} value={n}>{n}</option>)}</select></label><label>静止嘴型 <select aria-label={`区间${i+1}${names[id]}静止嘴型`} value={state.rest_mouth_action||""} onChange={e=>update({rest_mouth_action:e.target.value||null})}><option value="">沿用自动演出</option>{missingOption(state.rest_mouth_action, mouths)}{mouths.map(n=><option key={n} value={n}>{n}</option>)}</select></label><label>指定姿态 <select aria-label={`区间${i+1}${names[id]}指定姿态`} value={state.body_action || ""} onChange={e=>update({body_action:e.target.value||null})}><option value="">沿用自动演出</option>{missingOption(state.body_action, bodies)}{bodies.map(n=><option key={n} value={n}>{row?.animations.find(a=>a.name===n)?.label || n} · {n}</option>)}</select></label></div>;
        })}
      </div>)}
    </div>
    <p className="text-sm text-muted-foreground">仅调整演出会保留原配音。修改台词、发言人物或声线后，请生成语音。</p>
    {error && <p role="alert">{error}</p>}
    <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy} onClick={() => void save(false)}>保存</Button><Button disabled={busy} onClick={() => void save(true)}>保存并生成语音</Button><Button variant="outline" disabled={busy} onClick={async () => { if (!window.confirm("删除这句台词及其演出设置？")) return; setBusy(true); try {onDeleted(await request<Story>(`stories/${story.id}/lines/${line.id}/delete`,{}));} catch(e) {setError(e instanceof Error ? e.message : "删除失败");} finally {setBusy(false);} }}>删除本句</Button></div>
  </div>;
}
