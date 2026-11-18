// What the stand should look like (the stand direction, spec「立绘指令」) and which of an
// outfit's animations show it on each track (spec「选动画」). Every fallback lives here, in one
// pure function; the engine only plays what it is given.

/** Her emotion (GLOSSARY.md「情绪」): 平静、开心、害羞、惊讶、难过、生气、思考. */
export type Emotion = "calm" | "happy" | "shy" | "surprised" | "sad" | "angry" | "thinking";

/** A nod (`yes`) or a shake of the head (`no`); each gets a new id, so it is played once. */
export type Nod = { kind: "yes" | "no"; id: number };

/** How she should look right now, with no word of which animations do it. */
export type StandDirection = {
  idleOnly?: boolean;
  idleSeed?: string;
  entrySeed?: string;
  idleEntrance?: boolean;
  faceAction?:string;
  mouthAction?:string;
  restMouthAction?:string;
  /** Exact original face clip; speech emotion remains independent. */
  expression?: string | null;
  /** Body-only director selection; never changes the facial emotion. */
  bodyAction?: string;
  /** Legacy story beat counter; it must not insert idle between spoken lines. */
  bodyVariant?: number;
  /** Independently selected body movement; omitted follows the facial emotion. */
  bodyMood?: Emotion;
  /** Her emotion over the call so far; calm when it starts. */
  mood: Emotion;
  /** Her mouth is moving. */
  speaking: boolean;
  /** She looks at the user. */
  looking: boolean;
  nod: Nod | null;
};

/** How she looks when a call starts. */
export const CALM: StandDirection = { mood: "calm", speaking: false, looking: false, nod: null };

/** What each track should play, by name; null leaves the track empty. */
export type TrackAnimations = {
  /** Track 0, looped. */
  body: string;
  /** Track 1, looped; `blank` hands the face back to the body's own expression. */
  face: string;
  /** Track 5: her talking mouth, looping while open and held on its first frame while shut;
   * null if the outfit has none. */
  mouth: { name: string; open: boolean } | null;
  /** Track 2, played once. */
  nod: { name: string; id: number } | null;
  /** Track 3. */
  eye: string | null;
};

/** Body animations for each emotion, the first the outfit has being played; `wait` if none. */
const BODY: Record<Emotion, string[]> = {
  calm: ["wait", "wait1", "wait2", "wait3", "wait4", "wait5", "wait6", "wait7"],
  happy: ["smile1", "smile2", "smile3", "smile4"],
  shy: ["shy1", "shy2", "shy3", "smile2"],
  surprised: ["surp1", "surp2", "surp3", "surp4"],
  sad: ["sad1", "sad2", "sad3"],
  angry: ["anger1", "anger2", "anger3"],
  thinking: ["think", "think1", "think2", "wait4", "wait"],
};

/** Faces for each emotion; `blank` if none, the face `face_wait` being only for `wait` bodies. */
const FACE: Record<Emotion, string[]> = {
  calm: ["face_wait"],
  happy: ["face_smile", "face_smile1", "face_smile2"],
  shy: ["face_shy", "face_shy2"],
  surprised: ["face_surp"],
  sad: ["face_sad"],
  angry: ["face_anger", "face_anger1", "face_anger2"],
  thinking: ["face_serious"],
};

/** The `<k>` of each emotion's talking mouth, `lip_<k>`. */
const LIP: Record<Emotion, string> = {
  calm: "wait",
  happy: "smile",
  shy: "shy",
  surprised: "surp",
  sad: "sad",
  angry: "anger",
  thinking: "serious",
};

/**
 * Which animations show `direction` in an outfit with these `animations` (name → duration in
 * seconds). With reduced motion the body is always `wait`; faces, mouth, nods and gaze go on.
 */
export function pickIdle(animations: ReadonlyMap<string, number>, seed = "idle"): string {
  const names=[...animations.keys()].filter(n=>/^wait\d*$/.test(n)).sort();
  if (!names.length) return "wait";
  let hash=2166136261;
  for (const ch of seed) {hash^=ch.charCodeAt(0);hash=Math.imul(hash,16777619)>>>0;}
  return names[hash % names.length];
}

export function missingStandActions(direction: Pick<StandDirection, "bodyAction" | "faceAction" | "expression" | "mouthAction" | "restMouthAction" | "idleOnly">, animations: ReadonlySet<string>): boolean {
  const face = direction.faceAction || direction.expression;
  return [direction.idleOnly ? undefined : direction.bodyAction, face, direction.mouthAction, direction.restMouthAction]
    .some(name => !!name && !animations.has(name));
}

export function pickAnimations(
  direction: StandDirection,
  animations: ReadonlyMap<string, number>,
  reducedMotion: boolean,
): TrackAnimations {
  const pick = (names: string[]) => names.find((name) => animations.has(name));
  const { mood, nod } = direction;
  const k = LIP[mood];
  const explicitFace=(direction.expression?.startsWith("face_")||direction.expression==="blank") && animations.has(direction.expression!) ? direction.expression : undefined;
  const presetFace=direction.faceAction&&animations.has(direction.faceAction)?direction.faceAction:undefined;
  const faceKey=explicitFace?.slice(5);
  // Use matching original talking mouths when present. Closed-eye and other
  // faces without a dedicated mouth keep the speech emotion's mouth.
  const selectedMouth=direction.speaking?direction.mouthAction:direction.restMouthAction;
  const mouth = selectedMouth&&animations.has(selectedMouth)?selectedMouth:pick([...(faceKey?[`lip_${faceKey}`]:[]),`lip_${k}`, `lip_${k}1`, `lip_${k}2`, "lip_smile", "lip_wait"]) ?? anyMouth(animations);
  const nodding = nod && pick([nod.kind, `${nod.kind}2`]);
  return {
    body: reducedMotion ? "wait" : direction.idleOnly ? pickIdle(animations,direction.idleSeed) : direction.bodyAction
      ? (animations.has(direction.bodyAction) ? direction.bodyAction : animations.has("wait") ? "wait" : pickIdle(animations, direction.idleSeed))
      : (pick(BODY[direction.bodyMood ?? mood]) ?? "wait"),
    face: presetFace ?? explicitFace ?? pick(FACE[mood]) ?? "blank",
    mouth: mouth ? { name: mouth, open: direction.speaking } : null,
    nod: nod && nodding ? { name: nodding, id: nod.id } : null,
    eye: !presetFace && !explicitFace && direction.looking && animations.has("eye_front") ? "eye_front" : null,
  };
}

/** Any talking mouth that moves: not a still `lip_*_s`, nor `lip_a`. */
function anyMouth(animations: ReadonlyMap<string, number>): string | undefined {
  for (const [name, duration] of animations)
    if (name.startsWith("lip_") && !/_s\d*$/.test(name) && name !== "lip_a" && duration > 0) return name;
  return undefined;
}
