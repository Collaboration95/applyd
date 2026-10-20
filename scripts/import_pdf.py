#!/usr/bin/env python3
"""Archive a PDF with deterministic text extraction, hashes, links and provenance."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--destination", required=True, type=Path, help="Workspace-relative archive directory")
    parser.add_argument("--original-name")
    parser.add_argument("--content-updated-at", default=None, help="Only supply a verified content date")
    args = parser.parse_args()
    import fitz  # PyMuPDF is needed only for PDF import, not routine workspace commands.

    source = args.pdf.expanduser().resolve()
    destination = (ROOT / args.destination).resolve()
    if not destination.is_relative_to(ROOT):
        parser.error("Destination must be inside the workspace")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if destination.exists():
        existing = json.loads((destination / "metadata.json").read_text())
        if existing["sha256"] != digest or existing["source_id"] != args.id:
            parser.error("Archive exists with different content or ID; use a new destination")
        if hashlib.sha256((destination / "original.pdf").read_bytes()).hexdigest() != digest:
            parser.error("Archived original was modified")
        print(json.dumps({"source_id": args.id, "path": str(destination), "already_imported": True}))
        return
    text = subprocess.run(["pdftotext", "-layout", str(source), "-"], capture_output=True, text=True, check=True).stdout
    info = subprocess.run(["pdfinfo", "-isodates", str(source)], capture_output=True, text=True, check=True).stdout
    links = []
    with fitz.open(source) as document:
        pages = len(document)
        for number, page in enumerate(document, 1):
            for link in page.get_links():
                if link.get("uri"):
                    links.append({"page": number, "uri": link["uri"], "rect": list(link["from"]),
                                  "label": page.get_textbox(link["from"]).strip()})
    metadata = {"source_id": args.id, "original_name": args.original_name or source.name,
                "original_path": str(source), "sha256": digest, "pages": pages,
                "size_bytes": source.stat().st_size,
                "imported_at": datetime.now().astimezone().isoformat(),
                "filesystem_modified_at": datetime.fromtimestamp(source.stat().st_mtime).astimezone().isoformat(),
                "content_updated_at": args.content_updated_at,
                "date_caveat": "Import and filesystem times do not establish a content-update date.",
                "extraction": "Poppler pdftotext -layout; PyMuPDF link annotations"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=destination.parent))
    try:
        shutil.copy2(source, staging / "original.pdf")
        (staging / "extracted.txt").write_text(text)
        (staging / "pdfinfo.txt").write_text(info)
        for filename, value in [("metadata.json", metadata), ("links.json", links)]:
            (staging / filename).write_text(json.dumps(value, indent=2) + "\n")
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(json.dumps({"source_id": args.id, "path": str(destination), "sha256": digest}))


if __name__ == "__main__":
    main()
