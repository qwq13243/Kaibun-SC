import {useEffect,useState} from "react";
import {Button} from "@/components/ui/button";
import {request,type Story} from "./api";
import {productionIssues,spokenPosition} from "./productionMessages";

export function ProductionStatus({story,onUpdated,onLocate}:{story:Story;onUpdated:(story:Story)=>void;onLocate?:(id:string)=>void}) {
  const [pending,setPending]=useState("");
  const [error,setError]=useState("");
  const [now,setNow]=useState(Date.now());
  const p=story.production,issues=productionIssues(story);
  useEffect(()=>{if(!p?.staging_active)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[p?.staging_active]);
  useEffect(()=>{if(story.status==="ready" || p?.active)setError("");},[story.status,p?.active]);
  async function retry(scope:"staging"|"voices") {
    setPending(scope);setError("");
    try {onUpdated(await request<Story>(`stories/${story.id}/production/retry`,{scope}));}
    catch(e){setError(e instanceof Error?e.message:"重试未能开始");}
    finally {setPending("");}
  }
  if(story.status==="ready")return null;
  const voiceMissing=!!p&&p.voices_done<p.voices_total;
  const stageMissing=!!p&&!p.staging_done;
  return <div className="flex flex-col gap-2 text-sm text-muted-foreground" data-production-status>
    {p&&<p>配音 {p.voices_done} / {p.voices_total}{p.staging_scenes_total!==undefined?` · 演出 ${p.staging_scenes_done??0} / ${p.staging_scenes_total} 幕`:""}</p>}
    {p?.staging_active&&!p.staging_retrying&&p.staging_progress?.status==='waiting-model'&&<p role="status">正在准备第 {p.staging_progress.scene_number} 幕，等待辅助模型返回{p.staging_progress.started_at?` · 已等待 ${Math.max(0,Math.floor((now-Date.parse(p.staging_progress.started_at))/1000))} 秒`:''}{p.staging_retry_attempt?` · 第 ${p.staging_retry_attempt+1} 次尝试`:''}</p>}
    {p?.staging_retrying&&<p role="status">演出连接暂时失败，正在自动重试（{p.staging_retry_attempt??1}/2），已完成场景保留。</p>}
    {p?.voice_retrying&&<p role="status">配音服务暂时失败，正在重试。</p>}
    {issues.map(issue=><details key={issue.kind} className="rounded-lg border px-3 py-2" data-production-issue={issue.kind}><summary className="cursor-pointer">{issue.summary} · 查看原因</summary><p role="alert" className="mt-2">{issue.detail}</p>{issue.kind==="voices"&&issue.detail.includes("没有可朗读")&&<p>请在微调中确认本句配音文本；这种情况自动重试无法修复。</p>}{issue.kind==="voices"&&p?.voice_failed_ids?.slice(0,5).map(id=><Button key={id} variant="ghost" size="sm" onClick={()=>onLocate?.(id)}>定位{spokenPosition(id)}</Button>)}</details>)}
    <div className="flex flex-wrap gap-2">
      {stageMissing&&!p?.staging_active&&<Button variant="outline" disabled={!!pending} onClick={()=>void retry("staging")}>{pending==="staging"?"正在启动…":"重试演出准备"}</Button>}
      {voiceMissing&&!p?.voices_active&&<Button variant="outline" disabled={!!pending} onClick={()=>void retry("voices")}>{pending==="voices"?"正在启动…":"重试缺失配音"}</Button>}
    </div>
    {story.status==="interrupted"&&(stageMissing||voiceMissing)&&<p>重试制作只处理已保存内容，不会继续写剧本。</p>}
    {error&&<p role="alert">{error}</p>}
  </div>;
}
