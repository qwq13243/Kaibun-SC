import type { Line, ActorState } from "./api";
type Turn = Line;
export type StandBounds = { width: number; height: number };
export type StagePose = {autoBodyAction?:string;autoFaceAction?:string;autoMouthAction?:string;autoRestMouthAction?:string;presetControlled?:boolean;faceAction?:string;mouthAction?:string;restMouthAction?:string;idleEntrance?:boolean;manualIdle?:boolean;mood:string;expression?:string|null;bodyVariant:number;bodyMood?:string;bodyAction?:string;manualBody?:boolean;entryIdle?:boolean;idleOnly?:boolean;idleSeed?:string;entrySeed?:string};

/** Keep the chosen shot membership, arranging it by the saved left-to-right order. */
export function orderedStageCast(visible: readonly string[], order: readonly string[] = []): string[] {
  return [...new Set([...order.filter(id => visible.includes(id)), ...visible])];
}

/** The people in this exchange; being in the room does not put everyone in the shot. */
export function stageCast(lines: readonly Turn[], index: number, cast: readonly string[], limit = 5): string[] {
  const current = lines[index];
  if (!current) return [];
  if (current.screen_cast) return current.screen_cast.filter(id => cast.includes(id)).slice(0, 7);
  const known = new Set(cast);
  const present = new Set(current.onstage ?? cast);
  if (known.has(current.speaker)) present.add(current.speaker);
  const chosen: string[] = [];
  const add = (id: string) => {
    if (known.has(id) && present.has(id) && !chosen.includes(id) && chosen.length < limit) chosen.push(id);
  };
  add(current.speaker);
  if (current.focus?.length) {
    let anchor = index;
    while (anchor > 0 && lines[anchor - 1].speaker === current.speaker) anchor--;
    (lines[anchor].focus ?? current.focus).forEach(add);
  } else {
    // Keep the immediate partner while they answer; replace them as the exchange moves on.
    for (let i = index - 1; i >= Math.max(0, index - 5) && chosen.length < limit; i--) add(lines[i].speaker);
    for (let i = index + 1; i < Math.min(lines.length, index + 5) && chosen.length < limit; i++) add(lines[i].speaker);
  }
  return cast.filter(id => chosen.includes(id));
}

/** Restore each visible actor's latest pose in this uninterrupted shot, including when seeking. */
export function stagePoses(lines: readonly (Turn & {body_actions?:Record<string,string>})[], index: number, cast: readonly string[], limit = 5, scope=""): Record<string, StagePose> {
  const visible = (i: number) => lines[i]?.screen_cast ? stageCast(lines, i, cast, limit) : lines[i]?.speakers?.length
    ? cast.filter(id => lines[i].speakers?.includes(id)).slice(0, 7)
    : stageCast(lines, i, cast, limit);
  const result: Record<string, StagePose> = {};
  for (const actor of visible(index)) {
    let carry: ActorState = {};
    let variant = 0;
    let entrySeed="",idleSeed="",entryIdle=false,wasVisible=false,wasIdle=false,manualMood=false;
    for (let i = 0; i <= index; i++) {
      const turn = lines[i], speaker = (turn.speakers || [turn.speaker]).includes(actor);
      if (!visible(i).includes(actor)) { carry = {}; wasVisible=false; manualMood=false; if (speaker) variant++; continue; }
      if(!wasVisible){entrySeed=`${scope}:${turn.id}:${actor}`;idleSeed=entrySeed;entryIdle=!speaker;wasIdle=false;}
      wasVisible=true;
      if(speaker)entryIdle=false;
      if (speaker) { carry = {emotion:turn.emotion || "calm",body_mood:turn.body_mood,expression:turn.expression,body_action:turn.body_action,mouth_action:turn.mouth_action,rest_mouth_action:turn.rest_mouth_action}; variant++; }
      if(speaker)manualMood=!!turn.body_mood&&!!turn.performance_segments?.length;
      if(Object.hasOwn(turn.actor_states?.[actor]||{},'body_mood'))manualMood=!!turn.actor_states?.[actor]?.body_mood&&!!turn.performance_segments?.length;
      const current = {...carry, ...turn.actor_states?.[actor]};
      if (i < index) for (const segment of turn.performance_segments || []) {
        if (speaker) {current.emotion = segment.emotion || current.emotion || "calm"; current.body_mood = segment.body_mood; current.expression=segment.expression; current.body_action=segment.body_action;current.mouth_action=segment.mouth_action;current.rest_mouth_action=segment.rest_mouth_action;}
        if(speaker)manualMood=!!segment.body_mood;
        if(Object.hasOwn(segment.actor_states?.[actor]||{},'body_mood'))manualMood=!!segment.actor_states?.[actor]?.body_mood;
        Object.assign(current, segment.actor_states?.[actor]);
      }
      const mood = current.emotion || "calm";
      const idleOnly=!current.body_action && !(manualMood&&current.body_mood!=="calm") && (entryIdle || (current.body_mood || mood)==="calm");
      if(idleOnly&&!wasIdle&&!entryIdle)idleSeed=`${scope}:${turn.id}:${actor}:calm`;
      wasIdle=idleOnly;
      result[actor] = {mood,mouthAction:current.mouth_action||undefined,restMouthAction:current.rest_mouth_action||undefined,manualIdle:manualMood&&current.body_mood==="calm",expression:current.expression,entrySeed,idleSeed,entryIdle,idleOnly,manualBody:!!current.body_action||(manualMood&&current.body_mood!=="calm"),...(current.body_action?{bodyAction:current.body_action}:{}),bodyVariant:Math.max(0,variant-1),...(current.body_mood ? {bodyMood:current.body_mood} : {})};
      if (speaker || turn.listener_state_persist !== false) carry = current;
    }
  }
  for (const [cid, pose] of Object.entries(result)) {
    const performance=lines[index]?.performance_actions?.[cid];
    if(performance){
      pose.presetControlled=true;pose.autoBodyAction=performance.body;pose.autoFaceAction=performance.face;pose.autoMouthAction=performance.mouth;pose.autoRestMouthAction=performance.rest;pose.faceAction=pose.expression||performance.face;
      pose.mouthAction=pose.mouthAction||performance.mouth;pose.restMouthAction=pose.restMouthAction||performance.rest;
      if(!pose.manualBody)pose.bodyAction=performance.body;
      pose.idleOnly=!!pose.manualIdle;pose.idleEntrance=!!performance.entry_idle;
    }
    const action = lines[index]?.body_actions?.[cid];
    if (action && !pose.bodyAction) pose.bodyAction = action;
  }
  return result;
}

/** One world scale for every actor, with enough horizontal space for the widest outfit. */
export function stageFrameHeight(bounds: readonly StandBounds[], width: number, height: number, count: number): number | undefined {
  if (!bounds.length || width <= 0 || height <= 0 || count <= 0) return undefined;
  const aspect = width / count / height;
  return Math.max(...bounds.map(b => Math.max(b.height, b.width / aspect))) * 1.08;
}

/** Adjacent beats by one speaker form one continuous subtitle passage. */
export function speechPassage<T extends {speaker:string; kind?:string; speakers?:string[]; zh:string; ja:string}>(lines:readonly T[], index:number) {
  const current=lines[index];
  if(!current)return {zh:"",ja:""};
  const same=(line:T)=>line.speaker===current.speaker && line.kind===current.kind && JSON.stringify(line.speakers||[])===JSON.stringify(current.speakers||[]);
  let start=index,end=index;
  while(start>0&&same(lines[start-1]))start--;
  while(end+1<lines.length&&same(lines[end+1]))end++;
  return {zh:lines.slice(start,end+1).map(line=>line.zh).join(""),ja:lines.slice(start,end+1).map(line=>line.ja).join("")};
}
