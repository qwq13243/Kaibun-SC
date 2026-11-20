import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { copy } from "@/i18n";
import { cn } from "@/lib/utils";
import { request, type Actor, type Wardrobe } from "./api";

const t = copy.kaibun;
const pageSize = 12;

export function StoryOutfits({ actors, cast, outfits, onChange, allowAutomatic = true }: {
  actors: Actor[]; cast: string[]; outfits: Record<string, string>; onChange: (id: string, outfit: string) => void;
  allowAutomatic?: boolean;
}) {
  return <div className="flex flex-col gap-3">{cast.map(id => {
    const actor = actors.find(actor => actor.id === id);
    return actor ? <OutfitPicker key={id} actor={actor} value={outfits[id] ?? ""} allowAutomatic={allowAutomatic} onChange={value => onChange(id, value)} /> : null;
  })}</div>;
}

function OutfitPicker({ actor, value, onChange, allowAutomatic }: { actor: Actor; value: string; onChange: (value: string) => void; allowAutomatic: boolean }) {
  const [open, setOpen] = useState(false);
  const [choices, setChoices] = useState<Wardrobe[] | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [shown, setShown] = useState(pageSize);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setError("");
    request<Wardrobe[]>(`characters/${encodeURIComponent(actor.id)}/outfits`).then(data => { if (active) setChoices(data); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [open, actor.id, attempt]);
  return <details onToggle={event => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer text-sm">{actor.short_name ?? actor.name} · {t.outfits}</summary>
    {open && <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-4">
      {allowAutomatic && <button type="button" aria-pressed={!value} onClick={() => onChange("")} className={cn("flex min-h-24 items-center justify-center rounded-lg border p-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50", !value ? "bg-muted" : "hover:bg-muted/60")}>{t.autoOutfit}</button>}
      {choices?.slice(0, shown).map(outfit => <OutfitCard key={outfit.id} outfit={outfit} selected={value === outfit.id} onSelect={() => onChange(outfit.id)} />)}
      {!choices && !error && Array.from({ length: 3 }, (_, index) => <Skeleton key={index} className="h-40 w-full" />)}
    </div>}
    {open && choices && shown < choices.length && <Button type="button" variant="ghost" className="mt-2" onClick={() => setShown(count => count + pageSize)}>{t.moreOutfits}</Button>}
    {open && choices?.length === 0 && <p role="status" className="mt-2 text-meta text-muted-foreground">该角色还没有可用的本地衣装。</p>}
    {!choices && !error && open && <p role="status" className="mt-2 text-meta text-muted-foreground">{t.loadingOutfits}</p>}
    {open && error && <div role="alert" className="mt-2 flex items-center gap-2 text-meta text-muted-foreground">{error}<Button type="button" variant="ghost" onClick={() => setAttempt(value => value + 1)}>{t.retry}</Button></div>}
  </details>;
}

function OutfitCard({ outfit, selected, onSelect }: { outfit: Wardrobe; selected: boolean; onSelect: () => void }) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const source = outfit.thumbnail ? outfit.thumbnail + (attempt ? (outfit.thumbnail.includes("?") ? "&" : "?") + "retry=" + attempt : "") : null;
  return <div className="relative min-w-0">
    <button type="button" aria-label={outfit.label} aria-pressed={selected} onClick={onSelect} className={cn("flex w-full flex-col items-center gap-2 rounded-lg border p-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50", selected ? "bg-muted" : "hover:bg-muted/60")}>
      <span className="relative flex h-28 w-full items-center justify-center">
        {source && !loaded && !failed && <Skeleton className="absolute inset-0" />}
        {source && <img key={source} src={source} alt="" loading="lazy" onLoad={() => setLoaded(true)} onError={() => { setFailed(true); setLoaded(false); }} className={cn("h-28 w-full object-contain transition-opacity duration-200 motion-reduce:transition-none", loaded ? "opacity-100" : "opacity-0")} />}
      </span>
      <span className="min-h-8 text-subtitle">{outfit.label}</span>
    </button>
    {failed && <div className="absolute inset-x-2 top-2 flex h-28 items-center justify-center"><Button type="button" variant="outline" size="sm" onClick={() => { setFailed(false); setLoaded(false); setAttempt(count => count + 1); }}>{t.retryThumbnail}</Button></div>}
  </div>;
}
