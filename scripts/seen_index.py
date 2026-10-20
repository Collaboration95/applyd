"""Private filesystem identity index; no record scans after initialization."""

import hashlib
import os
import re
import tempfile
from pathlib import Path


def normal(value):
    return " ".join(str(value).casefold().split())


def hashed(prefix, value):
    return prefix + hashlib.sha1(value.encode()).hexdigest()


def identity_keys(record, canonical):
    keys = [hashed("url-", canonical)]
    match = re.fullmatch(r"https://www\.linkedin\.com/jobs/view/(\d+)", canonical)
    if match:
        keys.insert(0, "li-" + match[1])
    elif record.get("platform_job_id") and record.get("platform"):
        keys.append(hashed("platform-", normal(record["platform"]) + "|" + str(record["platform_job_id"])))
    if record.get("employer_requisition_id"):
        keys.append(hashed("req-", normal(record["company"]) + "|" + normal(record["employer_requisition_id"])))
    keys.append(hashed("xpost-", "|".join(normal(record[k]) for k in ("company", "role", "location"))))
    return keys


def atomic_marker(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".marker-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def update(root, record, canonicalize):
    directory = root / "applications/index"
    for key in identity_keys(record, canonicalize(record.get("canonical_listing_url") or record["listing_url"])):
        atomic_marker(directory / key, record["application_id"] + " " + record["status"] + "\n")


def reindex(root, all_records, canonicalize):
    directory = root / "applications/index"
    directory.mkdir(parents=True, exist_ok=True)
    expected = {}
    # Prefer confirmations if historical records contain overlapping identities.
    ordered = sorted(all_records, key=lambda r: (bool(r.get("submissions")), r["application_id"]))
    for record in ordered:
        for key in identity_keys(record, canonicalize(record.get("canonical_listing_url") or record["listing_url"])):
            expected[key] = record["application_id"] + " " + record["status"] + "\n"
    for key, value in sorted(expected.items()):
        atomic_marker(directory / key, value)
    for path in directory.iterdir():
        if path.is_file() and not path.name.startswith(".") and path.name not in expected:
            path.unlink()
    atomic_marker(directory / ".initialized", "1\n")
    return {"markers": len(expected)}


def lookup(root, data, canonical):
    directory = root / "applications/index"
    for key in identity_keys(data, canonical):
        marker = directory / key
        if marker.exists():
            fields = marker.read_text().split()
            if len(fields) != 2:
                raise ValueError("Invalid seen index; run reindex")
            return {"application_id": fields[0], "status": fields[1], "key": key}
    return None
