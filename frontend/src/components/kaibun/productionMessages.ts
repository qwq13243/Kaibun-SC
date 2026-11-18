import type { Story } from "./api";

export function productionIssues(story: Story) {
  if (story.status === "ready") return [];
  const p=story.production;
  const issues:{kind:"voices"|"staging"|"writing";summary:string;detail:string}[]=[];
  if (p && p.voices_done < p.voices_total && (p.voices_failed || p.voice_error) && !p.voice_retrying) {
    issues.push({kind:"voices",summary:`配音有 ${p.voices_failed || p.voices_total-p.voices_done} 句未完成`,detail:p.voice_error || "可重试缺失配音"});
  }
  if (p && !p.staging_done && p.staging_error && !p.staging_retrying) {
    issues.push({kind:"staging",summary:"演出准备尚未完成",detail:p.staging_error});
  }
  if (story.error && ["failed","interrupted"].includes(story.status) && (story.failure_stage!=="production" || !issues.length)) {
    // Production errors already explained by a component are not repeated as
    // another long, generic error paragraph.
    const inRecovery=p?.active || p?.staging_retrying || p?.voice_retrying;
    if (story.failure_stage!=="production" || !inRecovery) issues.push({kind:"writing",summary:story.failure_stage==="production"?"仍有制作内容待完成":"创作暂未完成",detail:story.error});
  }
  return issues;
}

export function spokenPosition(id:string) {
  const match=/^(\d+)-(\d+)$/.exec(id);
  return match?`第 ${Number(match[1])+1} 幕第 ${Number(match[2])+1} 句`:id;
}
