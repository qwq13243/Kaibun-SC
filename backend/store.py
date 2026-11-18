"""Small, independent story storage. Never opens the chat application's database."""

import json
import sqlite3
import re
import uuid
import os
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class StoryStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "stories.db"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY, document TEXT NOT NULL)")
            # An unfinished story is explicitly resumable after a restart.
            rows = db.execute("SELECT id,document FROM stories").fetchall()
            for id, raw in rows:
                doc = json.loads(raw)
                from performance import restore_split_lines
                original = json.loads(raw)
                restored = restore_split_lines(doc)
                if restored:
                    backup = directory / 'backups' / 'before-sentence-performance'
                    backup.mkdir(parents=True, exist_ok=True)
                    snapshot = backup / (id + '.json')
                    if not snapshot.exists():
                        snapshot.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding='utf-8')
                    doc['restored_split_lines'] = restored
                    db.execute("UPDATE stories SET document=? WHERE id=?", (json.dumps(doc, ensure_ascii=False), id))
                if (doc.get('revision_progress') or {}).get('status') == 'working':
                    doc['revision_progress'] = dict(status='interrupted', message='调整已中断，已保存部分可继续')
                    db.execute("UPDATE stories SET document=? WHERE id=?", (json.dumps(doc, ensure_ascii=False), id))
                if doc["status"] in ("queued", "generating", "preparing"):
                    doc.update(status="interrupted", phase="生成中断，可以继续", error=None)
                    db.execute("UPDATE stories SET document=? WHERE id=?", (json.dumps(doc, ensure_ascii=False), id))
                if (doc.get('production') or {}).get('active'):
                    doc['production'].update(active=False,voices_active=False,staging_active=False,staging_retrying=False,voice_retrying=False)
                    db.execute("UPDATE stories SET document=? WHERE id=?", (json.dumps(doc, ensure_ascii=False), id))

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, premise, cast, length, details="", outfits=None, outfit_mode="story"):
        doc = dict(id=uuid.uuid4().hex, premise=premise, requested_cast=cast, length=length,
                   details=details, requested_outfits=outfits or {}, outfit_mode=outfit_mode, pinned=False,
                   title=premise, status="queued", phase="准备构思", outline=None, scenes=[],
                   story_memory="", error=None, model=None, created_at=datetime.now(UTC).isoformat())
        self.save(doc)
        return doc

    def save(self, doc):
        with self.connect() as db:
            current = db.execute("SELECT document FROM stories WHERE id=?", (doc["id"],)).fetchone()
            if current:
                stored = json.loads(current[0])
                doc["pinned"] = stored.get("pinned", False)
                if stored.get("custom_title"):
                    doc["custom_title"] = stored["custom_title"]
                    doc["title"] = stored["custom_title"]
            db.execute("INSERT INTO stories VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document",
                       (doc["id"], json.dumps(doc, ensure_ascii=False)))

    def get(self, id):
        with self.connect() as db:
            row = db.execute("SELECT document FROM stories WHERE id=?", (id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self):
        with self.connect() as db:
            rows = db.execute("SELECT document FROM stories ORDER BY rowid DESC").fetchall()
        documents = [json.loads(row[0]) for row in rows]
        documents.sort(key=lambda doc: bool(doc.get("pinned")), reverse=True)
        return [self.brief(doc) for doc in documents]

    def pin(self, id, pinned):
        with self.connect() as db:
            row = db.execute("SELECT document FROM stories WHERE id=?", (id,)).fetchone()
            if row is None:
                return None
            doc = json.loads(row[0])
            doc["pinned"] = pinned
            db.execute("UPDATE stories SET document=? WHERE id=?", (json.dumps(doc, ensure_ascii=False), id))
        return doc

    def rename(self, id, title):
        with self.connect() as db:
            row = db.execute("SELECT document FROM stories WHERE id=?", (id,)).fetchone()
            if row is None:
                return None
            doc = json.loads(row[0])
            doc["custom_title"] = doc["title"] = title
            db.execute("UPDATE stories SET document=? WHERE id=?", (json.dumps(doc, ensure_ascii=False), id))
        return doc

    def owned_files(self, id):
        """Enumerate only artifacts whose filename or checkpoint key owns this UUID."""
        if not re.fullmatch(r"[a-f0-9]{32}", id):
            raise ValueError("无效作品id")
        root = self.directory.resolve()
        files, directories = set(), set()

        def validate(path):
            if not path.resolve().is_relative_to(root):
                raise OSError('故事文件路径越界')
            cursor = path
            while cursor != self.directory:
                attrs = getattr(cursor.lstat(), 'st_file_attributes', 0)
                if cursor.is_symlink() or attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                    raise OSError('故事文件包含链接，请检查后重试')
                cursor = cursor.parent

        def walk(folder, owned=False):
            validate(folder)
            for base, names, leaves in os.walk(folder, followlinks=False):
                parent = Path(base)
                for name in names:
                    validate(parent / name)
                if owned:
                    directories.add(parent)
                for name in leaves:
                    path = parent / name
                    if owned or name == id + '.json' or name.startswith(id + '-'):
                        validate(path)
                        files.add(path)

        for folder in (self.directory, self.directory / 'exports', self.directory / 'diagnostics'):
            if not folder.exists():
                continue
            validate(folder)
            for path in folder.iterdir():
                if path.name.startswith(id + '-') or path.name in (id + '.json', id + '.mp4'):
                    validate(path)
                    if path.is_file():
                        files.add(path)
        checkpoints = self.directory / 'exports' / 'checkpoints'
        if checkpoints.exists():
            validate(checkpoints)
            for folder in checkpoints.iterdir():
                if re.fullmatch(re.escape(id) + r'(?:-scene-\d+)?', folder.name):
                    walk(folder, owned=True)
        backups = self.directory / 'backups'
        if backups.exists():
            walk(backups)
        return sorted(files), sorted(directories, key=lambda p: len(p.parts), reverse=True)

    def delete(self, id):
        files, directories = self.owned_files(id)
        removed = total = 0
        # Keep the row until every file is removed. A locked file fails visibly;
        # the user can close its player and retry using the existing sidebar row.
        for path in files:
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            path.unlink(missing_ok=True)
            removed += 1
            total += size
        for path in directories:
            path.rmdir()
        with self.connect() as db:
            db.execute("DELETE FROM stories WHERE id=?", (id,))
        return dict(files_deleted=removed, bytes_deleted=total)

    @staticmethod
    def brief(doc):
        return {**{key: doc[key] for key in ("id", "title", "premise", "status", "phase", "created_at", "length", "error")},
                "pinned": bool(doc.get("pinned")), "production": doc.get("production")}

    def audio_path(self, id, line_id, signature):
        return self.directory / f"{id}-{line_id}-{signature}.mp3"
