import { missingStandActions } from "@/lib/standAnimations";
import type { Actor, Story } from "./api";
import { performancePose } from "./performance";

export function reviewOutfitActions(story: Story, actors: readonly Actor[], previous?: Story) {
  const defaults = new Map(actors.map(actor => [actor.id, actor.stand]));
  const previousOutfits = previous && new Map(previous.scenes.flatMap(scene => scene.lines.map(line => [line.id, line.shot?.outfits] as const)));
  const missing = new Map<string, string[]>();
  let firstUnverified: string | undefined;
  const inventories = new Map<string, Set<string>>();
  for (const scene of story.scenes) for (const line of scene.lines) {
    const shot = line.shot;
    if (!shot || line.speaker === "narrator") continue;
    for (const actor of shot.cast) {
      if (previousOutfits && previousOutfits.get(line.id)?.[actor] === shot.outfits[actor]) continue;
      const source = shot.outfitSources[actor];
      const outfit = !shot.outfits[actor] ? defaults.get(actor) : source
        ? scene.wardrobe_changes?.[source]?.[actor] : scene.wardrobe?.[actor];
      if (!outfit?.animation_names) {
        firstUnverified ??= line.id;
        continue;
      }
      let inventory = inventories.get(outfit.id);
      if (!inventory) {
        inventory = new Set(outfit.animation_names);
        inventories.set(outfit.id, inventory);
      }
      const segments = line.performance_segments?.length ? line.performance_segments : [undefined];
      if (segments.some(segment => missingStandActions(performancePose(line, segment, actor, shot.poses[actor]), inventory))) {
        const affected = missing.get(line.id) ?? [];
        affected.push(actor);
        missing.set(line.id, affected);
      }
    }
  }
  return { missing, firstUnverified };
}
