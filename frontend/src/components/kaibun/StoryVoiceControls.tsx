import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { request, type Story } from "./api";

type VoiceStatus = {
  total:number; ready:number; missing:number; current_required:number; current_missing:number; revision:string;
  job?: {id:string; status:string; completed:number; total:number; failed:number; message:string};
};

export function StoryVoiceControls({story, lineId, voice, onUpdated, onAction}: {
  story:Story; lineId?:string; voice:boolean; onUpdated:(story:Story)=>void; onAction:()=>void;
}) {
  const [state,setState] = useState<VoiceStatus | null>(null);
  const [pending,setPending] = useState(false);
  const [error,setError] = useState("");
  const seenRevision = useRef<string | null>(null);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    seenRevision.current = story.voice_revision || "";
    setState(null);
    let loading = false;
    async function refresh() {
      if (loading) return;
      loading = true;
      try {
        const value = await request<VoiceStatus>(`stories/${story.id}/voices${lineId ? `?line_id=${encodeURIComponent(lineId)}` : ""}`, undefined, controller.signal);
        if (!active) return;
        setState(value);setError("");
        if (value.revision !== seenRevision.current) {
          const updated = await request<Story>(`stories/${story.id}`);
          if (active) { seenRevision.current = value.revision; onUpdated(updated); }
        }
      } catch(e) {if(active)setError(e instanceof Error ? e.message : "配音状态读取失败");}
      finally { loading = false; }
    }
    const first = setTimeout(() => void refresh(), 250);
    const timer = setInterval(() => void refresh(),2500);
    return () => {active=false;controller.abort();clearTimeout(first);clearInterval(timer);};
  },[story.id,lineId,onUpdated]);
  const working = pending || state?.job?.status === "working";
  const locked = ["queued","generating","preparing"].includes(story.status) || !!story.production?.active || !!story.revision_proposal;
  async function generate(mode:"current" | "missing") {
    onAction();setPending(true);setError("");
    try {setState(await request<VoiceStatus>(`stories/${story.id}/voices`,{mode,line_id:lineId}));}
    catch(e){setError(e instanceof Error ? e.message : "配音生成失败");}
    finally{setPending(false);}
  }
  return <details>
    <summary className="cursor-pointer text-sm text-muted-foreground">配音工具</summary>
    <div className="mt-2 flex flex-wrap items-center gap-2">
    <Button variant="outline" disabled={!voice || !lineId || !state?.current_required || working || locked} onClick={()=>void generate("current")}>重新生成当前句语音</Button>
    <Button variant="outline" disabled={!voice || !state?.missing || working || locked} onClick={()=>void generate("missing")}>一键生成缺失语音{state?.missing ? `（${state.missing}）` : ""}</Button>
    {working && <Button variant="ghost" disabled={pending} onClick={async()=>{try{await request(`stories/${story.id}/voices/stop`,{});}catch(e){setError(e instanceof Error ? e.message : "暂停失败");}}}>暂停配音生成</Button>}
    <span role="status" className="text-meta text-muted-foreground">{state ? `配音 ${state.ready}/${state.total}` : ""}{state?.job ? ` · ${state.job.message}` : ""}{state && !state.current_required && lineId ? " · 当前句未配置配音音色" : ""}</span>
    {error && <span role="alert" className="text-sm text-muted-foreground">{error}</span>}
    </div>
  </details>;
}
