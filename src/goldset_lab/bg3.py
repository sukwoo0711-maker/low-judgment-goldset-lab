"""Read the public BG3 SQLite snapshot without copying it into this repository."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


DOCUMENT_TABLE = "\uBB38\uC11C"
CHUNK_TABLE = "\uC6D0\uBB38\uCCAD\uD06C"


@dataclass(frozen=True)
class Chunk:
    chunk_id: int
    document_id: int
    chunk_number: int
    title: str
    text: str
    source_url: str
    content_digest: str
    retrieved_at: str
    source_revision: str
    license_basis: str

    @property
    def content_id(self) -> str:
        return f"doc:{self.document_id}:chunk:{self.chunk_number}:sha256:{self.content_digest}"


def integrity(path: Path) -> tuple[str, int, int]:
    connection = sqlite3.connect(path)
    try:
        status = connection.execute("pragma integrity_check").fetchone()[0]
        documents = connection.execute(f'SELECT count(*) FROM "{DOCUMENT_TABLE}"').fetchone()[0]
        chunks = connection.execute(f'SELECT count(*) FROM "{CHUNK_TABLE}"').fetchone()[0]
    finally:
        connection.close()
    return str(status), int(documents), int(chunks)


def load_chunks(path: Path, *, min_chars: int = 120) -> list[Chunk]:
    sql = f'''SELECT c.id, c."\uBB38\uC11Cid", c."\uCCAD\uD06C\uBC88\uD638", c."\uC81C\uBAA9", c."\uBCF8\uBB38",
                     c."\uCD9C\uCC98URL", c."\uCF58\uD150\uCE20\uD574\uC2DC", d."\uC870\uD68C\uC2DC\uAC01",
                     coalesce(d."\uC218\uC815\uC2DC\uAC01", d."\uC870\uD68C\uC2DC\uAC01"), d."\uB77C\uC774\uC120\uC2A4URL"
              FROM "{CHUNK_TABLE}" c JOIN "{DOCUMENT_TABLE}" d ON d.id = c."\uBB38\uC11Cid"
              WHERE length(c."\uBCF8\uBB38") >= ? ORDER BY c.id'''
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(sql, (min_chars,)).fetchall()
    finally:
        connection.close()
    return [Chunk(*row) for row in rows]


def all_source_urls(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        rows = []
        for table in (DOCUMENT_TABLE, CHUNK_TABLE):
            rows.extend(
                value[0]
                for value in connection.execute(f'SELECT "\uCD9C\uCC98URL" FROM "{table}"')
                if value[0]
            )
    finally:
        connection.close()
    return rows
