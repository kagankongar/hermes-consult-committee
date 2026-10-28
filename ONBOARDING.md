# Onboarding — consult-committee

## Coding route (standing order)

Route all code-writing to Muse; Hermes owns brief, verification, changelog, commit.

Canonical invocation (always pass `--model muse-spark-1.3-contributor`, prefer `--yolo`):

```bash
timeout <cap> muse exec --json --model muse-spark-1.3-contributor --yolo --trust-workspace --workspace <scratch> --prompt-file <brief> --max-model-steps <N> --user-input-auto-resolve --disable-web-tools >run.jsonl 2>run.err
```

Procedure per `call-muse-code` skill:
1. `muse --version` first; baseline `git status` clean + suite green.
2. Scratch copy of target commit; brief lists allowed files, forbids git/network, 60 s per-command timeout, max-model-steps bound.
3. Caps by size (120/600/1500 s); heartbeat 60–180 s; stop-and-report past 1.5× phase budget.
4. Verify disk + `git log -1` / `reflog -3` / `status`; stage by filename; diff-back.
5. Parse `run.jsonl` `task.lifecycle.output` chunks for the verdict; never judge by wrapper exit alone.

Full rules: `call-muse-code` skill (`skill_view(name='call-muse-code')`).
Related: `cli-agent-supervision`.
