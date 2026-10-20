# [P2] Mascot sprite and banner

## Objective
Mascot sprite and banner.

## Scope
A small pixel-art mascot, for example a robot carrying a briefcase or a paper plane carrying CVs. Make it an SVG plus PNG at 64/128/256 px, a README banner (1280x320) and a social preview image (1280x640). Optionally add idle/working/done frames for the dashboard empty state. Use original artwork only.

Out of scope: cloud hosting, multi-user accounts, large frameworks, real employer submissions during tests, publishing private data.

## Acceptance criteria
- [ ] Assets are in docs/brand/ and render in light and dark GitHub themes.
- [ ] The social preview is set in repo settings.

## Implementation context
docs/brand/

## Dependencies
Blocked by: None
Related existing issues: None
Epic: Step 2 — Public repo presence. Priority: P2. Size: Small. Risk: Low.

## Light verification
Visual check in both GitHub themes.
All fixtures synthetic.

## Stop conditions
If an operation could publish private content, block it. If browser/auth capability is unavailable, record a typed blocker and finish independent synthetic work; do not fabricate success.

<!-- agent-orchestration:issue-key=auto-linkedin:sprite -->
