import type { Background, Scene, Wardrobe } from "./api";

/** Derive a frame from saved events so seeking backward is identical to replaying it. */
export function scenePresentation(scene: Scene, lineId: string): { wardrobe: Record<string, Wardrobe>; background: Background | null } {
  const wardrobe = { ...scene.wardrobe };
  let background = scene.background_asset ?? null;
  const shot = scene.lines.find(line => line.id === lineId)?.shot;
  if (shot) {
    for (const [actor, source] of Object.entries(shot.outfitSources)) {
      const outfit = scene.wardrobe_changes?.[source]?.[actor];
      if (outfit) wardrobe[actor] = outfit;
      else delete wardrobe[actor];
    }
    if (shot.backgroundSource) background = scene.background_change_assets?.[shot.backgroundSource] ?? null;
    return { wardrobe, background };
  }
  for (const line of scene.lines) {
    Object.assign(wardrobe, scene.wardrobe_changes?.[line.id]);
    if (scene.background_change_assets && Object.hasOwn(scene.background_change_assets, line.id)) background = scene.background_change_assets[line.id];
    if (line.id === lineId) break;
  }
  return { wardrobe, background };
}
