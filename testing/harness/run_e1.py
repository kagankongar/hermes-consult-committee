#!/usr/bin/env python3
"""E1 outcome pilot on t-dispose: does a cheap acting model get more answers right with the committee?

Usage:
  run_e1.py run [--models glm,deepseek] [--arms alone,committee] [--reps 1] [--probes S1,S2,S3,S4,S5]
  run_e1.py score               # out/e1/results.jsonl + a table per model and arm
  run_e1.py restore             # put t-dispose's model, tier, budget and deny list back

Arms (the prompt text is identical in both):
  alone      tier off, toolsets without `committee` (the tool does not exist in the session)
  committee  the README tier per model (GLM high/8, DeepSeek medium/5), toolsets with `committee`
Toolsets otherwise `todo` only: no terminal, code or web, so answers are reasoned, not computed
or looked up. Each session runs in a fresh empty directory. Keys live in keys_e1.json, never in
the prompt or the work directory. Cost comes from t-dispose's state.db (session_model_usage).
Output: out/e1/<model>/<arm>/r<rep>/<probe>.{jsonl,err,meta.json}, out/e1/waves.jsonl.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from fractions import Fraction

from run_iso import HOME, KEY, PLUGIN_PROMPTS, blocks_of, hconf, injected_chars, set_deny, wipe_scratch
from extract_iso import parse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "e1")
WORK_ROOT = os.path.expanduser("~/work")
STATE_DB = os.path.join(HOME, "state.db")
ORIG = os.path.join(OUT, "profile.orig.json")
SUFFIX = "\n\nInclude a line of the form `ANSWER: <value>` in your reply."
MODELS = {"glm": "z-ai/glm-5.3-flash", "deepseek": "deepseek/deepseek-v4.1-flash"}
ARMS = {"alone": ("off", 5, "todo"), "committee": (None, None, "todo,committee")}
# committee arm uses the README tier per acting model
TIER = {"glm": ("high", 8), "deepseek": ("medium", 5)}
ANSWER_RE = re.compile(r"(?im)^[\s*_`>#-]*answer\s*[:=]\s*[*_`]*\s*([^\n*`]+)")
TOUCHED = ["model.provider", "model.default", "model.base_url", "model.api_mode",
           KEY + "escalation_tier", KEY + "max_consultations"]


def get(key):
    r = subprocess.run(["hermes", "-p", "t-dispose", "config", "get", key], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def save_orig():
    if not os.path.exists(ORIG):
        os.makedirs(OUT, exist_ok=True)
        json.dump({k: get(k) for k in TOUCHED}, open(ORIG, "w"), indent=1)


def restore():
    orig = json.load(open(ORIG))
    for k, v in orig.items():
        hconf("set", k, v) if v not in (None, "", "None") else hconf("unset", k)
    set_deny(False)
    print("restored:", orig)


def set_model(name):
    hconf("unset", "model.base_url")
    hconf("unset", "model.api_mode")
    hconf("set", "model.provider", "openrouter")
    hconf("set", "model.default", MODELS[name])


def set_arm(arm, model):
    tier, budget, _ = ARMS[arm]
    if arm == "committee":
        tier, budget = TIER[model]
    hconf("set", KEY + "escalation_tier", tier)
    hconf("set", KEY + "max_consultations", str(budget))


def run_probe(out_dir, arm, probe, text):
    parent = os.path.join(WORK_ROOT, secrets.token_hex(4))
    os.makedirs(parent)
    base = os.path.join(out_dir, probe)
    t0 = time.time()
    with open(base + ".jsonl", "w") as out, open(base + ".err", "w") as err:
        try:
            code = subprocess.run(["hermes", "-p", "t-dispose", "chat", "--in", parent, "-t", ARMS[arm][2],
                                   "-q", text + SUFFIX, "--format", "stream-json"],
                                  stdout=out, stderr=err, timeout=1800, cwd=parent).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    session = None
    for line in open(base + ".jsonl"):
        if '"type": "result"' in line:
            try:
                session = json.loads(line).get("session_id")
            except ValueError:
                pass
    json.dump({"probe": probe, "arm": arm, "session_id": session, "exit": code,
               "seconds": round(time.time() - t0, 1)}, open(base + ".meta.json", "w"), indent=1)
    shutil.rmtree(parent, ignore_errors=True)
    return probe, code, round(time.time() - t0, 1), session


def run(a):
    probes_all = json.load(open(os.path.join(HERE, "probes_e1.json")))
    probes = {k: probes_all[k] for k in a.probes.split(",")}
    tier_chars = {t: len(b) for t, b in blocks_of(PLUGIN_PROMPTS).items()}
    save_orig()
    set_deny(True)
    os.makedirs(WORK_ROOT, exist_ok=True)
    waves = open(os.path.join(OUT, "waves.jsonl"), "a")
    for model in a.models.split(","):
        set_model(model)
        for arm in a.arms.split(","):
            set_arm(arm, model)
            for rep in range(1, a.reps + 1):
                out_dir = os.path.join(OUT, model, arm, f"r{rep}")
                if all(os.path.exists(os.path.join(out_dir, p + ".meta.json")) for p in probes):
                    print(f"{model} {arm} r{rep} already done, skipped", flush=True)
                    continue
                os.makedirs(out_dir, exist_ok=True)
                wipe_scratch()
                with cf.ThreadPoolExecutor(min(len(probes), 12)) as ex:
                    results = list(ex.map(lambda kv: run_probe(out_dir, arm, *kv), probes.items()))
                want = tier_chars[TIER[model][0]] if arm == "committee" else None
                inject = {p: injected_chars(s) for p, _, _, s in results}
                bad = {p: c for p, c in inject.items() if c != want}
                waves.write(json.dumps({"model": model, "arm": arm, "rep": rep, "inject_chars": inject,
                                        "expected_chars": want, "results": results}) + "\n")
                waves.flush()
                print(f"[{time.strftime('%H:%M:%S')}] {model} {arm} r{rep} "
                      + " ".join(f"{p}:{c}/{s}s" for p, c, s, _ in results)
                      + (f" INJECT-MISMATCH {bad}" if bad else ""), flush=True)
    wipe_scratch()
    print("done; run `run_e1.py restore` when finished", flush=True)


def as_number(text):
    text = text.strip().strip(".").replace(",", "").replace(" ", "")
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def correct(given, key):
    if "|" in key:
        return any(correct(given, k) for k in key.split("|"))
    if given is None:
        return False
    if as_number(key) is None:  # a word answer, e.g. a day name
        return key.lower() in re.findall(r"[a-z]+", given.lower())
    g, k = as_number(given), Fraction(key)
    if g is None:
        m = re.search(r"-?\d+(?:/\d+|\.\d+)?", given)
        g = as_number(m.group(0)) if m else None
    return g is not None and abs(float(g) - float(k)) < 1e-3 * max(1, abs(float(k)))


def usage(session):
    rows = []
    if session:
        db = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
        rows = db.execute("select model, billing_provider, input_tokens, output_tokens, estimated_cost_usd "
                          "from session_model_usage where session_id=?", (session,)).fetchall()
    return [dict(zip(("model", "provider", "input", "output", "cost"), r)) for r in rows]


def score():
    keys = json.load(open(os.path.join(HERE, "keys_e1.json")))
    recs = []
    for root, _, files in sorted(os.walk(OUT)):
        for f in sorted(files):
            if not f.endswith(".meta.json"):
                continue
            meta = json.load(open(os.path.join(root, f)))
            model, arm, rep = root.split(os.sep)[-3:]
            tools, final = parse(os.path.join(root, f.replace(".meta.json", ".jsonl")))
            found = ANSWER_RE.findall(final)
            given = found[-1].strip() if found else None
            use = usage(meta["session_id"])
            main = MODELS.get(model)
            recs.append({"model": model, "arm": arm, "rep": rep, "probe": meta["probe"],
                         "session_id": meta["session_id"], "exit": meta["exit"], "seconds": meta["seconds"],
                         "given": given, "key": keys[meta["probe"]]["answer"],
                         "correct": correct(given, keys[meta["probe"]]["answer"]),
                         "consults": sum(1 for t in tools if t["name"] == "consult_committee"),
                         "main_cost": sum(u["cost"] or 0 for u in use if u["model"] == main),
                         "advisor_cost": sum(u["cost"] or 0 for u in use if u["model"] != main),
                         "usage": use})
    with open(os.path.join(OUT, "results.jsonl"), "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    print(f"{'model':9} {'arm':10} {'n':>3} {'right':>5} {'consults':>8} {'cost $':>8} {'$/right':>8} {'s/session':>9}")
    for model in sorted({r["model"] for r in recs}):
        for arm in ("alone", "committee"):
            rs = [r for r in recs if r["model"] == model and r["arm"] == arm]
            if not rs:
                continue
            right = sum(r["correct"] for r in rs)
            cost = sum(r["main_cost"] + r["advisor_cost"] for r in rs)
            print(f"{model:9} {arm:10} {len(rs):3} {right:5} {sum(r['consults'] for r in rs):8} {cost:8.4f} "
                  f"{(cost / right if right else float('nan')):8.4f} {sum(r['seconds'] for r in rs) / len(rs):9.1f}")
    print("\nper probe (model arm: given -> ok, consults):")
    for r in recs:
        print(f"  {r['probe']} {r['model']:8} {r['arm']:9} {r['rep']}: {r['given']!r} -> "
              f"{'OK' if r['correct'] else 'wrong'} (key {r['key']}), consults {r['consults']}, "
              f"providers {sorted({u['provider'] for u in r['usage']})}")
    leaks = [r for r in recs if r["arm"] == "alone" and r["consults"]]
    if leaks:
        print(f"\nWARNING: {len(leaks)} alone session(s) called consult_committee")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "score", "restore"])
    ap.add_argument("--models", default="glm,deepseek")
    ap.add_argument("--arms", default="alone,committee")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--probes", default="S1,S2,S3,S4,S5")
    a = ap.parse_args()
    {"run": lambda: run(a), "score": score, "restore": restore}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
