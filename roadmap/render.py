"""Render roadmap/backlog-v2.json into one Markdown file per epic/issue under roadmap/issues/."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
data = json.loads((HERE / "backlog-v2.json").read_text())
out = HERE / "issues"
out.mkdir(exist_ok=True)
for old in out.glob("*.md"):
    old.unlink()

titles = {i["key"]: i["title"] for i in data["issues"]}
index = ["# Roadmap v2 — local issue drafts", "", "Create epics first, then add each issue as a sub-issue of its epic. "
         "`#N` refers to existing GitHub issues; other dependencies are v2 keys.", ""]

for e in data["epics"]:
    children = [i for i in data["issues"] if i["epic"] == e["key"]]
    name = f"epic-{e['key']}.md"
    (out / name).write_text(
        f"# [Epic] {e['title']}\n\n## Outcome\n{e['outcome']}\n\n## Sub-issues\n"
        + "".join(f"- [ ] `{c['key']}` [{c['priority']}] {c['title']}\n" for c in children)
        + "\n## Completion\nAll sub-issue acceptance checks pass. No real employer submission or private data is needed for verification.\n"
        + f"\n<!-- agent-orchestration:issue-key=auto-linkedin:epic-{e['key']} -->\n")
    index += [f"## Step {e['step']} — [{e['title']}](issues/{name})", ""]
    for n, c in enumerate(children, 1):
        fname = f"{e['key']}-{n:02d}-{c['key']}.md"
        deps = ", ".join(d if d.startswith("#") else f"`{d}` ({titles[d]})" for d in c["deps"]) or "None"
        related = ", ".join(c["related"]) or "None"
        body = (
            f"# [{c['priority']}] {c['title']}\n\n"
            f"## Objective\n{c['title']}.\n\n## Scope\n{c['scope']}\n\n"
            "Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.\n\n"
            "## Acceptance criteria\n" + "".join(f"- [ ] {a}\n" for a in c["accept"]) +
            f"\n## Implementation context\n{c['files']}\n\n"
            f"## Dependencies\nBlocked by: {deps}\nRelated existing issues: {related}\n"
            f"Epic: {e['title']}. Priority: {c['priority']}. Size: {c['size']}. Risk: {c['risk']}.\n\n"
            f"## Light verification\n{c['verify']}\nAll fixtures synthetic.\n\n"
            "## Stop conditions\nIf an operation could publish private content, block it. If browser/auth capability is unavailable, "
            "record a typed blocker and finish independent synthetic work; do not fabricate success.\n"
            f"\n<!-- agent-orchestration:issue-key=auto-linkedin:{c['key']} -->\n")
        (out / fname).write_text(body)
        index.append(f"- [{c['priority']}] [{c['title']}](issues/{fname}) — blocked by: {deps}")
    index.append("")

(HERE / "README.md").write_text("\n".join(index))
print(f"{len(data['epics'])} epics, {len(data['issues'])} issues -> {out}")
