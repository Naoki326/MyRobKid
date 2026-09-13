# MyRobKid

## Agent skills

### Issue tracker

Issues live in GitHub Issues (Naoki326/MyRobKid), operated via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage labels are used as-is (needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root, created lazily. See `docs/agents/domain.md`.

### Evidence rules

When a conclusion depends on a library, kernel, or upstream component rather than code this repo owns — confirm which implementation is actually built, treat `firmware/managed_components/` as untracked upstream, and mark unconfirmed conclusions as such. See `docs/agents/evidence-rules.md`.
