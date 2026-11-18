import type { Line, PerformanceSegment } from "./api";
import type { StagePose } from "./stage";

export function segmentAt(segments: Line["performance_segments"], progress: number) {
  return (segments || []).reduce((last, seg, i) => progress >= (seg.start ?? i / segments!.length) ? i : last, 0);
}

export function performancePose(line: Line, segment: PerformanceSegment | undefined, id: string, base:StagePose = {mood: "calm", bodyVariant: 0}) {
  const speaker = (line.speakers || [line.speaker]).includes(id);
  const state = {...line.actor_states?.[id]};
  if (segment) for (const part of line.performance_segments || []) {
    Object.assign(state,part.actor_states?.[id]);
    if (part===segment) break;
  }
  if(base.presetControlled){
    const p={...base};
    const value=(key:"expression"|"mouth_action"|"rest_mouth_action"|"body_action")=>Object.hasOwn(state,key)?state[key]:speaker&&segment&&Object.hasOwn(segment,key)?segment[key]:undefined;
    const provided=(key:"expression"|"mouth_action"|"rest_mouth_action"|"body_action")=>value(key)!==undefined;
    if(provided("expression"))p.faceAction=value("expression")||base.autoFaceAction||base.faceAction;
    if(provided("mouth_action"))p.mouthAction=value("mouth_action")||base.autoMouthAction||base.mouthAction;
    if(provided("rest_mouth_action"))p.restMouthAction=value("rest_mouth_action")||base.autoRestMouthAction||base.restMouthAction;
    if(segment&&speaker&&segment.emotion!==line.emotion&&!provided("expression")){
      p.mood=segment.emotion;p.faceAction=undefined;p.expression=undefined;
      if(!provided("mouth_action"))p.mouthAction=undefined;
      if(!provided("rest_mouth_action"))p.restMouthAction=undefined;
    }
    const manual=!!line.last_fine_edit_token||!!line.performance_segments?.length||!!segment;
    const exact=value("body_action");
    const mood=state.body_mood||(speaker?segment?.body_mood:undefined);
    if(exact){p.bodyAction=exact;p.idleOnly=false;}
    else if(provided("body_action")){p.bodyAction=base.autoBodyAction;p.idleOnly=false;}
    else if(manual&&mood){p.bodyAction=undefined;p.bodyMood=mood;p.idleOnly=mood==="calm";}
    else p.idleOnly=!!base.manualIdle;
    return p;
  }
  const manual=state.body_action || (segment&&state.body_mood&&state.body_mood!=="calm") || (speaker?(segment?.body_action||line.body_action||(segment?.body_mood&&segment.body_mood!=="calm")):undefined);
  const idle=(pose:StagePose)=>({...pose,idleOnly:!manual&&!base.manualBody&&(!!pose.entryIdle||(pose.bodyMood||pose.mood)==="calm")});
  if (Object.keys(state).length) return idle({...base, expression:"expression" in state ? state.expression : speaker && segment ? segment.expression : base.expression, mood: state.emotion || base.mood, bodyMood: "body_mood" in state ? state.body_mood || undefined : base.bodyMood,
    bodyAction:state.body_action || (speaker?segment?.body_action:undefined) || (segment && (state.body_mood || (speaker && segment.body_mood)) ? undefined : base.bodyAction)});
  if (segment && speaker) return idle({...base, expression:segment.expression, mood: segment.emotion, bodyMood: segment.body_mood || segment.emotion, bodyAction:segment.body_action || (segment.body_mood ? undefined : base.bodyAction)});
  return base;
}

export function timedSegments(segments: PerformanceSegment[]) {
  const length = segments.reduce((n, s) => n + s.ja.length, 0);
  let offset = 0;
  return segments.map(s => { const start = offset / Math.max(1, length); offset += s.ja.length; return {...s, start}; });
}
