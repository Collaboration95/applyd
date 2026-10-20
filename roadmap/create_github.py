"""Create roadmap v2 epics/issues in Collaboration95/applyd plus a GitHub Project. Idempotent via body markers."""
import json, re, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO, OWNER, OLD_REPO = "Collaboration95/applyd", "Collaboration95", "Collaboration95/auto-linkedin"
PROJECT_TITLE = "applyd — Roadmap"
data = json.loads((HERE / "backlog-v2.json").read_text())
state_path = HERE / "github-state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {"epics": {}, "issues": {}}
save = lambda: state_path.write_text(json.dumps(state, indent=2) + "\n")


def gh(*args, obj=False):
    r = subprocess.run(["gh", *args], text=True, capture_output=True)
    if r.returncode:
        raise RuntimeError("gh " + " ".join(args[:3]) + ": " + r.stderr)
    return json.loads(r.stdout) if obj else r.stdout.strip()


def old_refs(text):  # "#6" -> cross-repo reference to the original backlog
    return re.sub(r"(?<![\w/])#(\d+)", OLD_REPO + r"#\1", text)


existing = gh("issue", "list", "--repo", REPO, "--state", "all", "--limit", "200", "--json", "number,url,body", obj=True)
labels = {x["name"] for x in gh("label", "list", "--repo", REPO, "--limit", "100", "--json", "name", obj=True)}
for name, color in [("P0", "B60205"), ("P1", "D93F0B"), ("P2", "FBCA04"), ("epic", "5319E7"),
                    ("step:1-stability", "0E8A16"), ("step:2-features", "1D76DB"),
                    ("area:stability", "C5DEF5"), ("area:control", "C5DEF5"), ("area:feedback", "C5DEF5"), ("area:public", "C5DEF5")]:
    if name not in labels:
        gh("label", "create", name, "--repo", REPO, "--color", color)

milestones = {1: "Step 1 — Stability", 2: "Step 2 — Features"}
have = {m["title"] for m in gh("api", f"repos/{REPO}/milestones", obj=True)}
for title in milestones.values():
    if title not in have:
        gh("api", f"repos/{REPO}/milestones", "-f", "title=" + title)


def create(key, title, body, labels, milestone, parent=None, blocked_by=()):
    marker = f"applyd:issue-key={key}"
    match = next((x for x in existing if marker in (x.get("body") or "")), None)
    if match:
        return {"number": match["number"], "url": match["url"]}
    path = HERE / f".body-{key}.md"
    path.write_text(body + f"\n\n<!-- {marker} -->\n")
    args = ["issue", "create", "--repo", REPO, "--title", title, "--body-file", str(path), "--milestone", milestone]
    for label in labels:
        args += ["--label", label]
    if parent:
        args += ["--parent", str(parent)]
    if blocked_by:
        args += ["--blocked-by", ",".join(map(str, blocked_by))]
    url = gh(*args)
    path.unlink()
    item = {"number": int(url.rsplit("/", 1)[1]), "url": url}
    existing.append({**item, "body": marker})
    return item


steps = {e["key"]: e["step"] for e in data["epics"]}
for e in data["epics"]:
    if e["key"] not in state["epics"]:
        body = f"## Outcome\n{e['outcome']}\n\nSub-issues track the work. Completion: all sub-issue acceptance checks pass; verification uses synthetic data only."
        state["epics"][e["key"]] = create("epic-" + e["key"], "[Epic] " + e["title"], body,
                                          ["epic", f"area:{e['key']}", "step:1-stability" if e["step"] == 1 else "step:2-features"],
                                          milestones[e["step"]])
        save(); print("epic", state["epics"][e["key"]]["url"], flush=True)

# Topological order so blocked-by targets exist before dependants.
pending, ordered = list(data["issues"]), []
while pending:
    ready = [i for i in pending if all(d.startswith("#") or d in {o["key"] for o in ordered} for d in i["deps"])]
    if not ready:
        raise SystemExit("dependency cycle")
    ordered += ready
    pending = [i for i in pending if i not in ready]

for i in ordered:
    if i["key"] in state["issues"]:
        continue
    blocked = [state["issues"][d]["number"] for d in i["deps"] if not d.startswith("#")]
    dep_text = ", ".join([f"#{n}" for n in blocked] + [old_refs(d) for d in i["deps"] if d.startswith("#")]) or "None"
    related = ", ".join(old_refs(r) for r in i["related"]) or "None"
    body = (f"## Objective\n{i['title']}.\n\n## Scope\n{old_refs(i['scope'])}\n\n"
            "Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.\n\n"
            "## Acceptance criteria\n" + "".join(f"- [ ] {old_refs(a)}\n" for a in i["accept"]) +
            f"\n## Implementation context\n{i['files']}\n\n"
            f"## Dependencies\nBlocked by: {dep_text}\nRelated (original backlog): {related}\n"
            f"Priority: {i['priority']}. Size: {i['size']}. Risk: {i['risk']}.\n\n"
            f"## Light verification\n{i['verify']}\nAll fixtures synthetic.\n\n"
            "## Stop conditions\nIf an operation could publish private content, block it. If browser/auth capability is unavailable, "
            "record a typed blocker and finish independent synthetic work; do not fabricate success.")
    step = steps[i["epic"]]
    state["issues"][i["key"]] = create(i["key"], f"[{i['priority']}] {i['title']}", body,
                                       [i["priority"], f"area:{i['epic']}", "step:1-stability" if step == 1 else "step:2-features"],
                                       milestones[step], state["epics"][i["epic"]]["number"], blocked)
    save(); print("issue", state["issues"][i["key"]]["url"], flush=True)

# Project board with fields.
projects = gh("project", "list", "--owner", OWNER, "--format", "json", "--limit", "100", obj=True)["projects"]
project = next((p for p in projects if p["title"] == PROJECT_TITLE), None) or \
    gh("project", "create", "--owner", OWNER, "--title", PROJECT_TITLE, "--format", "json", obj=True)
num, pid = str(project["number"]), project["id"]
state["project"] = {"number": project["number"], "url": project["url"]}; save()
subprocess.run(["gh", "project", "link", num, "--owner", OWNER, "--repo", REPO], capture_output=True)
fields = gh("project", "field-list", num, "--owner", OWNER, "--format", "json", "--limit", "100", obj=True)["fields"]
for name, options in [("Priority", "P0,P1,P2"), ("Step", "1 — Stability,2 — Features"), ("Size", "Small,Medium"),
                      ("Risk", "Low,Medium,High"), ("Epic", "Stability,Run control,Feedback,Public repo")]:
    if not any(f["name"] == name for f in fields):
        fields.append(gh("project", "field-create", num, "--owner", OWNER, "--name", name, "--data-type", "SINGLE_SELECT",
                         "--single-select-options", options, "--format", "json", obj=True))
fields = {f["name"]: f for f in gh("project", "field-list", num, "--owner", OWNER, "--format", "json", "--limit", "100", obj=True)["fields"]}
epic_names = {"stability": "Stability", "control": "Run control", "feedback": "Feedback", "public": "Public repo"}


def set_field(item_id, name, value):
    option = next(o["id"] for o in fields[name]["options"] if o["name"] == value)
    gh("project", "item-edit", "--id", item_id, "--project-id", pid, "--field-id", fields[name]["id"], "--single-select-option-id", option)


rows = [(state["epics"][e["key"]], None, e["key"], e["step"]) for e in data["epics"]] + \
       [(state["issues"][i["key"]], i, i["epic"], steps[i["epic"]]) for i in data["issues"]]
for issue, meta, epic, step in rows:
    item = gh("project", "item-add", num, "--owner", OWNER, "--url", issue["url"], "--format", "json", obj=True)
    set_field(item["id"], "Step", "1 — Stability" if step == 1 else "2 — Features")
    set_field(item["id"], "Epic", epic_names[epic])
    if meta:
        set_field(item["id"], "Priority", meta["priority"])
        set_field(item["id"], "Size", meta["size"])
        set_field(item["id"], "Risk", meta["risk"])
    if "Status" in fields:
        set_field(item["id"], "Status", "Todo")
print("project", project["url"])
