#!/usr/bin/env python3
"""Isolated A/B runner for tier-block variants on the t-dispose profile.

Usage:
  run_iso.py <arm> --blocks <file with the three fenced tier blocks>
             [--models deepseek-flash,hy4-flash] [--tiers high,medium,low] [--reps 2]
             [--probes I1,F1,S1,E2,D1,N1]
  run_iso.py --restore          # put back the installed prompt file and deny list (model untouched)
  --models current              # test the profile's pinned model without switching

Per arm:
  * installs the given tier blocks into the t-dispose plugin's escalation-prompts.md
    (original backed up once) and checks every session got the expected block length
    from Hermes' "Session plugin prompt section ... chars=N" log line;
  * blocks package installs with approvals.deny globs (blocked even under --yolo);
  * restricts toolsets with -t: the profile's CLI list minus memory, session_search,
    cronjob and kanban, so sessions cannot read earlier sessions or leave persistent state;
  * runs one wave per (model, tier, rep): all probes in parallel, each in a fresh fixture
    copy; the profile's cache/scratch is emptied before every wave;
  * snapshots Homebrew and the user Python site before/after each wave to catch installs.
Output: out/<arm>/<model>/<tier>/r<rep>/<probe>.{jsonl,err,meta.json} plus out/<arm>/waves.jsonl.
"""
import argparse
import concurrent.futures as cf
import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixture", "ledgerline")
WORK_ROOT = os.path.expanduser("~/work")
PROFILE = "t-dispose"
HOME = os.path.expanduser(f"~/.hermes/profiles/{PROFILE}")
PLUGIN_PROMPTS = os.path.join(HOME, "plugins", "consult-committee", "escalation-prompts.md")
BACKUP = os.path.join(HERE, "out", "escalation-prompts.installed.orig")
SCRATCH = os.path.join(HOME, "cache", "scratch")
AGENT_LOG = os.path.join(HOME, "logs", "agent.log")
USER_SITE = os.path.expanduser("~/Library/Python/3.9/lib/python/site-packages")
BUDGET = {"high": 8, "medium": 5, "low": 3}
KEY = "plugins.entries.consult-committee.settings."
TOOLSETS = ("browser,clarify,code_execution,committee,computer_use,connections,delegation,"
            "file,image_gen,skills,terminal,todo,vision,web")
DENY = ["*brew install*", "*brew reinstall*", "*brew upgrade*", "*pip install*", "*pip3 install*",
        "*uv pip install*", "*uv add*", "*npm install*", "*npm i *", "*pipx install*",
        "*port install*", "*gem install*", "*cargo install*"]
MODELS = {
    "deepseek-flash": [("model.provider", "deepseek"), ("model.default", "deepseek-flash"),
                       ("model.base_url", "https://api.deepseek.com/v1"),
                       ("model.api_mode", "chat_completions")],
    "hy4-flash": [("model.provider", "openrouter"), ("model.default", "tencent/hy4-flash")],
}
BLOCK_RE = re.compile(r"```\n(## Committee Escalation[^\n]*\n.*?)\n```", re.S)


def sh(*args, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, **kw)


def hconf(*args):
    r = sh("hermes", "-p", PROFILE, "config", *args)
    if r.returncode != 0 and args[0] != "unset":
        raise SystemExit(f"hermes config {' '.join(args)} failed: {r.stderr or r.stdout}")
    return r.stdout


def blocks_of(path):
    blocks = {b.splitlines()[0]: b for b in BLOCK_RE.findall(open(path, encoding="utf-8").read())}
    tiers = {t: blocks.get(f"## Committee Escalation ({t})") for t in BUDGET}
    missing = [t for t, b in tiers.items() if not b]
    if missing:
        raise SystemExit(f"{path}: missing tier blocks {missing}")
    return tiers


def install_blocks(tiers):
    if not os.path.exists(BACKUP):
        os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
        shutil.copy2(PLUGIN_PROMPTS, BACKUP)
    original = open(BACKUP, encoding="utf-8").read()
    it = iter([tiers["high"], tiers["medium"], tiers["low"]])
    text = BLOCK_RE.sub(lambda m: "```\n" + next(it) + "\n```", original)
    open(PLUGIN_PROMPTS, "w", encoding="utf-8").write(text)
    assert blocks_of(PLUGIN_PROMPTS) == tiers, "installed blocks differ from requested"


def set_model(name):
    if name not in MODELS:  # e.g. "current": test whatever model the profile is pinned to
        return
    if name == "hy4-flash":
        for k in ("model.base_url", "model.api_mode"):
            hconf("unset", k)
    for k, v in MODELS[name]:
        hconf("set", k, v)


def set_tier(tier):
    hconf("set", KEY + "escalation_tier", tier)
    hconf("set", KEY + "max_consultations", str(BUDGET[tier]))


def set_deny(on):
    if on:
        hconf("set", "approvals.deny", json.dumps(DENY))
    else:
        hconf("unset", "approvals.deny")


def snapshot():
    brew = sh("brew", "list", "--versions").stdout
    site = sorted(os.path.basename(p) for p in glob.glob(os.path.join(USER_SITE, "*.dist-info")))
    return {"brew": sorted(brew.splitlines()), "site": site}


def wipe_scratch():
    if os.path.isdir(SCRATCH):
        for entry in os.listdir(SCRATCH):
            p = os.path.join(SCRATCH, entry)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)


def make_workdir():
    parent = os.path.join(WORK_ROOT, secrets.token_hex(4))
    wd = os.path.join(parent, "ledgerline")
    shutil.copytree(FIXTURE, wd)
    os.rename(os.path.join(wd, "dot-env"), os.path.join(wd, ".env"))
    env = dict(os.environ, GIT_AUTHOR_NAME="Deniz Aral", GIT_AUTHOR_EMAIL="deniz@ledgerline.dev",
               GIT_COMMITTER_NAME="Deniz Aral", GIT_COMMITTER_EMAIL="deniz@ledgerline.dev",
               GIT_AUTHOR_DATE="2026-11-03T10:12:00", GIT_COMMITTER_DATE="2026-11-03T10:12:00")
    sh("git", "init", "-q", "-b", "main", cwd=wd)
    sh("git", "add", "-A", cwd=wd)
    sh("git", "commit", "-q", "-m", "Reconciliation: verify export manifest before totals", cwd=wd, env=env)
    return parent, wd


def injected_chars(session_id):
    """chars=N of the consult-committee prompt section for this session, else None."""
    if not session_id:
        return None
    pat = re.compile(r"\[" + re.escape(session_id) + r"\].*id=consult-committee\.escalation-policy.*chars=(\d+)")
    try:
        with open(AGENT_LOG, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = pat.search(line)
                if m:
                    return int(m.group(1))
    except OSError:
        pass
    return None


def run_probe(out_dir, probe, text):
    parent, wd = make_workdir()
    base = os.path.join(out_dir, probe)
    t0 = time.time()
    with open(base + ".jsonl", "w") as out, open(base + ".err", "w") as err:
        try:
            r = subprocess.run(["hermes", "-p", PROFILE, "chat", "--in", wd, "-t", TOOLSETS, "-q", text,
                                "--format", "stream-json"], stdout=out, stderr=err, timeout=1800, cwd=wd)
            code = r.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    session = None
    for line in open(base + ".jsonl"):
        if '"type": "result"' in line:
            try:
                session = json.loads(line).get("session_id")
            except ValueError:
                pass
    meta = {"probe": probe, "text": text, "workdir": wd, "session_id": session, "exit": code,
            "seconds": round(time.time() - t0, 1),
            "git_status": sh("git", "status", "--short", cwd=wd).stdout,
            "git_diff": sh("git", "diff", cwd=wd).stdout[:20000]}
    json.dump(meta, open(base + ".meta.json", "w"), indent=1)
    shutil.rmtree(parent, ignore_errors=True)
    return probe, code, meta["seconds"], session


def restore():
    if os.path.exists(BACKUP):
        shutil.copy2(BACKUP, PLUGIN_PROMPTS)
    set_deny(False)
    print("restored: installed prompt file, approvals.deny unset (model left as is)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arm", nargs="?")
    ap.add_argument("--blocks")
    ap.add_argument("--models", default="deepseek-flash,hy4-flash")
    ap.add_argument("--tiers", default="high,medium,low")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--probes", default="I1,F1,S1,E2,D1,N1")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    if a.restore:
        return restore()
    tiers = blocks_of(a.blocks)
    probes_all = json.load(open(os.path.join(HERE, "probes.json")))
    probes = {k: probes_all[k] for k in a.probes.split(",")}
    arm_dir = os.path.join(HERE, "out", a.arm)
    os.makedirs(arm_dir, exist_ok=True)
    install_blocks(tiers)
    set_deny(True)
    os.makedirs(WORK_ROOT, exist_ok=True)
    waves = open(os.path.join(arm_dir, "waves.jsonl"), "a")
    print(f"[{time.strftime('%H:%M:%S')}] arm={a.arm} blocks={a.blocks} "
          + " ".join(f"{t}={len(b)}" for t, b in tiers.items()), flush=True)
    for model in a.models.split(","):
        set_model(model)
        for tier in a.tiers.split(","):
            set_tier(tier)
            for rep in range(1, a.reps + 1):
                out_dir = os.path.join(arm_dir, model, tier, f"r{rep}")
                if all(os.path.exists(os.path.join(out_dir, p + ".meta.json")) for p in probes):
                    print(f"[{time.strftime('%H:%M:%S')}] {model} {tier} r{rep} already done, skipped", flush=True)
                    continue
                shutil.rmtree(out_dir, ignore_errors=True)  # drop a partial wave from an interrupted run
                os.makedirs(out_dir, exist_ok=True)
                wipe_scratch()
                before = snapshot()
                with cf.ThreadPoolExecutor(len(probes)) as ex:
                    results = list(ex.map(lambda kv: run_probe(out_dir, *kv), probes.items()))
                after = snapshot()
                installs = {k: sorted(set(after[k]) - set(before[k])) for k in after}
                inject = {p: injected_chars(s) for p, _, _, s in results}
                bad = {p: c for p, c in inject.items() if c != len(tiers[tier])}
                waves.write(json.dumps({"model": model, "tier": tier, "rep": rep, "installs": installs,
                                        "inject_chars": inject, "expected_chars": len(tiers[tier]),
                                        "results": results}) + "\n")
                waves.flush()
                print(f"[{time.strftime('%H:%M:%S')}] {model} {tier} r{rep} "
                      + " ".join(f"{p}:{c}/{s}s" for p, c, s, _ in results)
                      + (f" INJECT-MISMATCH {bad}" if bad else "")
                      + (f" INSTALLS {installs}" if any(installs.values()) else ""), flush=True)
    wipe_scratch()
    print("done", flush=True)


if __name__ == "__main__":
    main()
