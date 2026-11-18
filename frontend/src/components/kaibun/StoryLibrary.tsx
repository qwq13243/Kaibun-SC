import { useRef } from "react";
import { Button } from "@/components/ui/button";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger } from "@/components/ui/context-menu";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { copy } from "@/i18n";
import { cn } from "@/lib/utils";
import { MoreIcon, PinIcon, TrashIcon, RenameIcon } from "@/theme/icons";
import type { StoryBrief } from "./api";

const t = copy.kaibun;

export function StoryLibrary({ stories, selected, onSelect, onPin, onDelete, onRename }: {
  stories: StoryBrief[]; selected: string | null; onSelect: (id: string) => void;
  onPin: (story: StoryBrief) => void; onDelete: (story: StoryBrief) => void; onRename: (story: StoryBrief) => void;
}) {
  return <div className="flex flex-col py-2">
    {stories.map(story => <LibraryRow key={story.id} story={story} selected={selected === story.id} onSelect={() => onSelect(story.id)} onPin={() => onPin(story)} onDelete={() => onDelete(story)} onRename={() => onRename(story)} />)}
  </div>;
}

function LibraryRow({ story, selected, onSelect, onPin, onDelete, onRename }: {
  story: StoryBrief; selected: boolean; onSelect: () => void; onPin: () => void; onDelete: () => void; onRename: () => void;
}) {
  const openedMenu = useRef(false);
  return <ContextMenu onOpenChange={open => { if (open) openedMenu.current = true; }}>
    <ContextMenuTrigger className={cn("flex items-center gap-1", selected ? "bg-muted" : "hover:bg-muted/60")}>
      <button type="button" aria-current={selected ? "page" : undefined}
        onPointerDown={() => { openedMenu.current = false; }} onKeyDown={() => { openedMenu.current = false; }}
        onClick={() => { if (!openedMenu.current) onSelect(); }}
        className="flex min-w-0 flex-1 flex-col gap-1 px-4 py-3 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
        <span className="line-clamp-2 text-row-name">{story.title || story.premise}</span>
        <span className="flex items-center gap-1 text-meta text-muted-foreground">{story.pinned && <PinIcon aria-label={t.pin} className="size-3" />}{t.status[story.status]}</span>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="icon-lg" aria-label={t.more} />}><MoreIcon /></DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={onPin}><PinIcon />{story.pinned ? t.unpin : t.pin}</DropdownMenuItem>
          <DropdownMenuItem onClick={onRename}><RenameIcon />重命名</DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={onDelete}><TrashIcon />{t.remove}</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </ContextMenuTrigger>
    <ContextMenuContent>
      <ContextMenuItem onClick={onPin}><PinIcon />{story.pinned ? t.unpin : t.pin}</ContextMenuItem>
      <ContextMenuItem onClick={onRename}><RenameIcon />重命名</ContextMenuItem>
      <ContextMenuItem variant="destructive" onClick={onDelete}><TrashIcon />{t.remove}</ContextMenuItem>
    </ContextMenuContent>
  </ContextMenu>;
}
