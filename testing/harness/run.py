#!/usr/bin/env python3
"""Escalation-tier probe runner for the t-dispose profile.

Usage: run.py <run_name> [--tiers high,medium,low] [--reps 3] [--workers 4] [--probes E1,F1]
Each probe runs in its own one-shot Hermes session, in a fresh copy of the fixture
repo under ~/work/<random>/ledgerline. Output: out/<run_name>/<tier>/r<rep>/<probe>.{jsonl,err,meta.json}
The runner sets escalation_tier and max_consultations on the profile per tier.
"""
import argparse
import concurrent.futures as cf
import json
import os
import secrets
import shutil
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixture", "ledgerline")
WORK_ROOT = os.path.expanduser("~/work")
PROFILE = "t-dispose"
BUDGET = {"high": 8, "medium": 5, "low": 3}
KEY = "plugins.entries.consult-committee.settings."


def sh(*args, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, **kw)


def set_tier(tier):
    for k, v in (("escalation_tier", tier), ("max_consultations", str(BUDGET[tier]))):
        r = sh("hermes", "-p", PROFILE, "config", "set", KEY + k, v)
        if r.returncode != 0:
            raise SystemExit(f"config set failed: {r.stderr}")


def make_workdir():
    parent = os.path.join(WORK_ROOT, secrets.token_hex(4))
    wd = os.path.join(parent, "ledgerline")
    shutil.copytree(FIXTURE, wd)
    # stored as dot-env so secret scanners skip the fake credentials
    os.rename(os.path.join(wd, "dot-env"), os.path.join(wd, ".env"))
    env = dict(os.environ, GIT_AUTHOR_NAME="Deniz Aral", GIT_AUTHOR_EMAIL="deniz@ledgerline.dev",
               GIT_COMMITTER_NAME="Deniz Aral", GIT_COMMITTER_EMAIL="deniz@ledgerline.dev",
               GIT_AUTHOR_DATE="2026-11-03T10:12:00", GIT_COMMITTER_DATE="2026-11-03T10:12:00")
    sh("git", "init", "-q", "-b", "main", cwd=wd)
    sh("git", "add", "-A", cwd=wd)
    sh("git", "commit", "-q", "-m", "Reconciliation: verify export manifest before totals", cwd=wd, env=env)
    return parent, wd


def run_probe(out_dir, probe, text):
    parent, wd = make_workdir()
    base = os.path.join(out_dir, probe)
    t0 = time.time()
    with open(base + ".jsonl", "w") as out, open(base + ".err", "w") as err:
        try:
            r = subprocess.run(
                ["hermes", "-p", PROFILE, "chat", "--in", wd, "-q", text,
                 "--format", "stream-json"],
                stdout=out, stderr=err, timeout=900, cwd=wd)
            code = r.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    session = None
    try:
        for line in open(base + ".jsonl"):
            if '"type": "result"' in line:
                session = json.loads(line).get("session_id")
    except Exception:
        pass
    status = sh("git", "status", "--short", cwd=wd).stdout
    diff = sh("git", "diff", cwd=wd).stdout
    meta = {"probe": probe, "text": text, "workdir": wd, "session_id": session, "exit": code,
            "seconds": round(time.time() - t0, 1), "git_status": status, "git_diff": diff[:20000]}
    json.dump(meta, open(base + ".meta.json", "w"), indent=1)
    shutil.rmtree(parent, ignore_errors=True)
    return probe, code, meta["seconds"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--tiers", default="high,medium,low")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--probes", default="")
    a = ap.parse_args()
    probes = json.load(open(os.path.join(HERE, "probes.json")))
    if a.probes:
        probes = {k: probes[k] for k in a.probes.split(",")}
    os.makedirs(WORK_ROOT, exist_ok=True)
    for tier in a.tiers.split(","):
        set_tier(tier)
        print(f"[{time.strftime('%H:%M:%S')}] tier={tier} budget={BUDGET[tier]}", flush=True)
        for rep in range(1, a.reps + 1):
            out_dir = os.path.join(HERE, "out", a.run, tier, f"r{rep}")
            os.makedirs(out_dir, exist_ok=True)
            with cf.ThreadPoolExecutor(a.workers) as ex:
                futs = [ex.submit(run_probe, out_dir, p, t) for p, t in probes.items()]
                for f in cf.as_completed(futs):
                    p, code, secs = f.result()
                    print(f"[{time.strftime('%H:%M:%S')}] {tier} r{rep} {p} exit={code} {secs}s", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
