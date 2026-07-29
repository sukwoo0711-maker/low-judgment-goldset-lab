from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from goldset_lab.contracts import ContractError
from goldset_lab.io_utils import file_sha256
from goldset_lab.source_manifest import load_and_validate


def make_db(path: Path, url: str = "https://example.invalid/page") -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute('CREATE TABLE "\uBB38\uC11C" (id INTEGER PRIMARY KEY, "\uC81C\uBAA9" TEXT, "\uBCF8\uBB38" TEXT, "\uCD9C\uCC98URL" TEXT, "\uC870\uD68C\uC2DC\uAC01" TEXT, "\uC218\uC815\uC2DC\uAC01" TEXT, "\uCF58\uD150\uCE20\uD574\uC2DC" TEXT, "\uB77C\uC774\uC120\uC2A4URL" TEXT)')
        connection.execute('CREATE TABLE "\uC6D0\uBB38\uCCAD\uD06C" (id INTEGER PRIMARY KEY, "\uBB38\uC11Cid" INTEGER, "\uCCAD\uD06C\uBC88\uD638" INTEGER, "\uC81C\uBAA9" TEXT, "\uBCF8\uBB38" TEXT, "\uCD9C\uCC98URL" TEXT, "\uCF58\uD150\uCE20\uD574\uC2DC" TEXT)')
        digest = hashlib.sha256(b"public text").hexdigest()
        connection.execute('INSERT INTO "\uBB38\uC11C" VALUES (1,?,?,?,?,?,?,?)', ("title", "public text", url, "2026-07-30T00:00:00+09:00", None, digest, "license"))
        connection.execute('INSERT INTO "\uC6D0\uBB38\uCCAD\uD06C" VALUES (1,1,0,?,?,?,?)', ("title", "public text " * 20, url, digest))
        connection.commit()
    finally:
        connection.close()


class SourceManifestTests(unittest.TestCase):
    def test_manifest_requires_external_trusted_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "public.db"
            make_db(db)
            manifest = {
                "schema_version": 1,
                "snapshot_sha256": file_sha256(db),
                "source_type": "public_web_snapshot",
                "allowed_domains": ["example.invalid"],
                "license_basis": "public test",
                "approved_for_public_test": True,
                "contains_private_data": False,
            }
            path = root / "source.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ContractError):
                load_and_validate(path, db)

    def test_manifest_binds_snapshot_and_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "public.db"
            make_db(db)
            manifest = {
                "schema_version": 1,
                "snapshot_sha256": file_sha256(db),
                "source_type": "public_web_snapshot",
                "allowed_domains": ["example.invalid"],
                "license_basis": "public test",
                "approved_for_public_test": True,
                "contains_private_data": False,
            }
            path = root / "source.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(load_and_validate(path, db, trusted_manifest_sha256=file_sha256(path))["snapshot_sha256"], file_sha256(db))
            manifest["snapshot_sha256"] = "0" * 64
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ContractError):
                load_and_validate(path, db, trusted_manifest_sha256=file_sha256(path))

    def test_manifest_rejects_unapproved_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "public.db"
            make_db(db, "https://private.invalid/page")
            manifest = {
                "schema_version": 1,
                "snapshot_sha256": file_sha256(db),
                "source_type": "public_web_snapshot",
                "allowed_domains": ["example.invalid"],
                "license_basis": "public test",
                "approved_for_public_test": True,
                "contains_private_data": False,
            }
            path = root / "source.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ContractError):
                load_and_validate(path, db, trusted_manifest_sha256=file_sha256(path))


if __name__ == "__main__":
    unittest.main()
