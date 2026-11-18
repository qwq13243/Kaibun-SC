import type { Skeleton, SkeletonData, Slot } from "./spineRuntime";

type Branch = { primary: Slot; layers: Slot[] };

export function numberedLimbLayers(skeleton: Skeleton) {
  const groups = new Map<string, Map<string, { primary?: Slot; layers: Slot[] }>>();
  for (const slot of skeleton.slots) {
    const match = /^(arm_top|arm_bottom|sleeve|hand)_([LR])(\d*)(?:_([BF2]))?$/.exec(slot.data.name ?? "");
    if (!match) continue;
    const [, part, side, number, suffix] = match;
    const group = `${part}:${side}`;
    const variant = suffix === "2" ? `${number}_2` : number;
    if (!groups.has(group)) groups.set(group, new Map());
    const branches = groups.get(group)!;
    if (!branches.has(variant)) branches.set(variant, { layers: [] });
    const branch = branches.get(variant)!;
    branch.layers.push(slot);
    if (!suffix || suffix === "2") branch.primary = slot;
  }
  const paired: Branch[][] = [];
  const slots = new Set<Slot>();
  for (const branches of groups.values()) {
    if (!branches.get("")?.primary || ![...branches].some(([variant, branch]) => /^\d+$/.test(variant) && branch.primary)) continue;
    paired.push([...branches.values()].map(({ primary, layers }) => ({ primary: primary ?? layers[0], layers })));
    for (const branch of branches.values()) for (const slot of branch.layers) slots.add(slot);
  }
  return { paired, slots };
}

export function resolveNumberedLimbLayers(groups: Branch[][], setAlpha: (slot: Slot, alpha: number) => void) {
  for (const branches of groups) {
    let winner: Branch | undefined;
    let best = 0;
    let active = 0;
    let blending = false;
    for (const branch of branches) {
      const alpha = branch.primary.getAttachment() ? branch.primary.color.a : 0;
      if (alpha <= 0.00001) continue;
      active++;
      blending ||= alpha < 0.99999;
      if (alpha >= best) {
        best = alpha;
        winner = branch;
      }
    }
    if (!winner || active < 2 || !blending) continue;
    for (const branch of branches) {
      if (branch === winner) setAlpha(branch.primary, 1);
      else for (const slot of branch.layers) setAlpha(slot, 0);
    }
  }
}

export function repairOutfitRelay(outfitId: string, data: SkeletonData, skeleton: Skeleton) {
  if (outfitId !== "stand/1030120010") return;
  const lowering = data.findAnimation("arm_down_R");
  if (!lowering) return;
  const unrelated = new Set(["bag_body", "bag_string", "wara_sting3", "hand_L", "hand_L_2", "arm_top_L2", "arm_bottom_L2"]);
  const slots = new Set(skeleton.slots.filter(slot => unrelated.has(slot.data.name ?? "")).map(slot => slot.data.index));
  lowering.timelines = lowering.timelines.filter(timeline => !("slotIndex" in timeline && typeof timeline.slotIndex === "number" && slots.has(timeline.slotIndex)));
}
