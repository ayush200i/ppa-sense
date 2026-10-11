import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from app.schemas.documents import Document
from app.schemas.terms import ExtractionResponse


class DocumentStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.upload_lock = Lock()
        self.ingestion_lock = Lock()
        self.extraction_lock = Lock()
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS terms (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.root / "catalog.sqlite3", timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, doc_id: str) -> Document | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return Document.model_validate_json(row[0]) if row else None

    def save(self, document: Document) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO documents VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (document.doc_id, document.model_dump_json()),
            )

    def list(self, limit: int, offset: int) -> list[Document]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM documents ORDER BY rowid LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [Document.model_validate_json(row[0]) for row in rows]

    def recover_interrupted(self) -> None:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM documents").fetchall()
            for row in rows:
                document = Document.model_validate_json(row[0])
                if document.status in {"queued", "processing"}:
                    document.status = "failed"
                    document.error = "Ingestion interrupted by restart; upload this PDF again."
                    db.execute(
                        "UPDATE documents SET data = ? WHERE id = ?",
                        (document.model_dump_json(), document.doc_id),
                    )

    def save_terms(self, result: ExtractionResponse) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO terms VALUES (?, ?)",
                (result.doc_id, result.model_dump_json()),
            )

    def get_terms(self, doc_id: str) -> ExtractionResponse | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM terms WHERE id = ?", (doc_id,)).fetchone()
        return ExtractionResponse.model_validate_json(row[0]) if row else None
