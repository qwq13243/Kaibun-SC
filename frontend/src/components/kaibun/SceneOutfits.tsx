import { useState } from "react";
import { Button } from "@/components/ui/button";
import { StoryOutfits } from "./StoryOutfits";
import { request, seasons, type Season, type Actor, type Story } from "./api";
import { reviewOutfitActions } from "./outfitActionReview";

const profiles = { default: "默认衣装", ...seasons };

export function SceneOutfits({story, actors, onUpdated, onPreview}: {story:Story; actors:Actor[]; onUpdated:(s:Story)=>void; onPreview:(id:string)=>void}) {
  const [scene,setScene]=useState(0);
  const [character,setCharacter]=useState(story.outline?.cast[0] || "");
  const [later,setLater]=useState(true);
  const [season,setSeason]=useState<Season | "default" | "">("");
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState("");
  const [error,setError]=useState("");
  const [unverified,setUnverified]=useState<string>();
  function updated(next: Story) {
    setUnverified(reviewOutfitActions(next, actors, story).firstUnverified);
    onUpdated(next);
  }
  async function saveOutfit(outfit: string) {
    if (!outfit || busy) return;
    setBusy(true);setError("");setNotice("");setUnverified(undefined);
    try {
      updated(await request<Story>(`stories/${story.id}/scenes/${scene}/outfit`, {character,outfit,apply_later:later,base_fingerprint:story.base_fingerprint}));
      setNotice("已更新角色衣装。");
    } catch (error) { setError(error instanceof Error ? error.message : "换装失败"); }
    finally { setBusy(false); }
  }
  async function applySeason() {
    if (!season || busy) return;
    setBusy(true);setError("");setNotice("");setUnverified(undefined);
    try {
      const result=await request<{story:Story;applied:string[];skipped:string[]}>(`stories/${story.id}/scenes/${scene}/outfit-season`, {season,apply_later:later,base_fingerprint:story.base_fingerprint});
      updated(result.story);
      const skipped=result.skipped.map(id=>actors.find(actor=>actor.id===id)?.name || id);
      const summary=result.applied.length ? `已为 ${result.applied.length} 位角色应用${profiles[season]}。` : `没有可应用的${profiles[season]}。`;
      setNotice(summary+(skipped.length ? `${skipped.join("、")}未设置该类衣装或素材未安装，原衣装不变。` : ""));
    } catch (error) { setError(error instanceof Error ? error.message : "批量换装失败"); }
    finally { setBusy(false); }
  }
  return <details className="rounded-lg border px-4 py-3"><summary className="cursor-pointer text-sm">场景服装</summary><div className="mt-3 flex flex-col gap-3">
    <fieldset disabled={busy} className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-3"><label>幕 <select className="rounded border bg-background p-2" value={scene} onChange={e=>setScene(Number(e.target.value))}>{story.scenes.map((s,i)=><option key={i} value={i}>第{i+1}幕 · {s.place}</option>)}</select></label><label>人物 <select className="rounded border bg-background p-2" value={character} onChange={e=>setCharacter(e.target.value)}>{(story.outline?.cast || []).map(id=><option key={id} value={id}>{actors.find(a=>a.id===id)?.name || id}</option>)}</select></label></div>
      <label className="text-sm"><input type="checkbox" checked={later} onChange={e=>setLater(e.target.checked)} /> 同时应用到后续幕</label>
      <StoryOutfits actors={actors} cast={character?[character]:[]} outfits={story.scenes[scene]?.outfits || {}} allowAutomatic={false} onChange={(_,outfit)=>{void saveOutfit(outfit);}} />
      <div className="flex flex-col gap-2 border-t pt-3">
        <div className="flex flex-wrap items-center gap-3"><label>默认与季节衣装 <select className="rounded border bg-background p-2" value={season} onChange={event=>setSeason(event.target.value as Season | "default" | "")}>
          <option value="">请选择分类</option>
          {Object.entries(profiles).map(([id,label])=><option key={id} value={id}>{label}</option>)}
        </select></label><Button type="button" variant="outline" disabled={!season || busy || !story.outline?.cast.length} onClick={()=>{void applySeason();}}>应用到所有角色</Button></div>
        <p className="text-sm text-muted-foreground">应用后会替换所选范围内的原衣装，包括手选；不重做配音。</p>
      </div>
    </fieldset>
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {unverified && <div role="status" className="flex flex-wrap items-center gap-2 text-sm">部分衣装的动作无法预先核对，建议预览。<Button variant="outline" onClick={() => { setUnverified(undefined); onPreview(unverified); }}>预览受影响的台词</Button></div>}
    {error && <p role="alert" className="text-sm">{error}</p>}
  </div></details>;
}
