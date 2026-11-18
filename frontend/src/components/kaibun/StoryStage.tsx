import { useEffect, useRef, useState } from "react";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { Stand } from "@/components/conversations/stand/Stand";
import { cn } from "@/lib/utils";
import type { Actor, Line } from "./api";
import type { Emotion } from "@/lib/standAnimations";
import { stageFrameHeight, type StagePose } from "./stage";

export function StoryStage({ actors, allActors, line, speaking, reducedMotion, poses }: {
  actors: Actor[]; allActors: Actor[]; line: Line; speaking: readonly string[]; reducedMotion: boolean; poses: Record<string, StagePose>;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (!frame.current) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(frame.current);
    return () => observer.disconnect();
  }, []);
  const bounds = allActors.flatMap(actor => actor.stand_bounds ? [actor.stand_bounds] : []);
  // Reserve the ordinary two-person shot's scale when one person is speaking alone.
  const fullFrameHeight = stageFrameHeight(bounds, size.width, size.height, Math.max(2, actors.length));
  return <div ref={frame} data-story-stage data-speaking={speaking.length > 0} className="absolute inset-x-0 top-10 bottom-0 flex items-end justify-center px-3">
    {actors.map((actor, index) => <div key={actor.id} data-story-actor={actor.id} data-actor-speaking={speaking.includes(actor.id)} data-frame-height={fullFrameHeight} style={{ left: `${(index + .5) * 100 / actors.length - 25}%`, zIndex: actor.id === line.speaker ? 10 : index }} className={cn("absolute bottom-0 h-full w-1/2 transition-opacity duration-300", line.speaker === actor.id || line.speakers?.includes(actor.id) ? "opacity-100" : "opacity-85")}>
      {actor.stand ? <Stand outfits={[actor.stand]} framing="full" fullFrameHeight={fullFrameHeight} isPaused={false} entrance="fade" direction={{ faceAction:poses[actor.id]?.faceAction,mouthAction:poses[actor.id]?.mouthAction,restMouthAction:poses[actor.id]?.restMouthAction,idleEntrance:poses[actor.id]?.idleEntrance,idleOnly:poses[actor.id]?.idleOnly,idleSeed:poses[actor.id]?.idleSeed,entrySeed:poses[actor.id]?.entrySeed,expression:poses[actor.id]?.expression, mood: (poses[actor.id]?.mood || "calm") as Emotion, bodyMood: poses[actor.id]?.bodyMood as Emotion | undefined, bodyAction: poses[actor.id]?.bodyAction, bodyVariant: poses[actor.id]?.bodyVariant ?? 0, speaking: speaking.includes(actor.id), looking: true, nod: null }} isReducedMotion={reducedMotion} maxPixelRatio={1.5} /> : <div className="flex h-full flex-col items-center justify-center gap-3"><MarkedAvatar name={actor.name} src={actor.avatar_url} size="call" /><span className="text-meta text-muted-foreground">{actor.name}</span></div>}
    </div>)}
  </div>;
}
