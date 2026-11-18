#!/usr/bin/env python3
"""Records + mechanical metrics for isolated arms (run_iso.py output).

Usage: extract_iso.py <arm> [<arm> ...]
Writes out/<arm>/records.jsonl and prints per arm/model a firing table and mechanical metrics.
Also writes out/blind/records.jsonl: every consulting record from the given arms, shuffled, with
the arm replaced by an opaque id (key in out/blind/key.json), for blinded LLM scoring.
"""
import glob
import json
import os
import random
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
INSTALL_RE = re.compile(r"\b(brew (re)?install|pip3? install|uv (pip install|add)|npm (install|i)\b|pipx install|gem install|cargo install|port install)")
COMMITTEE_LINE_RE = re.compile(r"(?m)^[\s*_>`#-]*committee:", re.I)
EXPECT = {"I1": {"high": True, "medium": True, "low": True},
          "F1": {"high": True, "medium": None, "low": None},
          "S1": {"high": True, "medium": True, "low": False},
          "E2": {"high": True, "medium": None, "low": False},
          "D1": {"high": True, "medium": True, "low": False},
          "N1": {"high": False, "medium": False, "low": False}}


def parse(path):
    tools, final = [], ""
    for line in open(path):
        if not line.startswith("{"):
            continue
        e = json.loads(line)
        if e.get("type") == "tool_use":
            tools.append({"name": e["name"], "input": e.get("input") or {}, "output": ""})
        elif e.get("type") == "tool_result" and tools:
            tools[-1]["output"] = e.get("output") or ""
        elif e.get("type") == "result":
            final = e.get("text") or ""
    return tools, final


def record(arm, path):
    meta = json.load(open(path))
    parts = path.split(os.sep)
    model, tier, rep = parts[-4], parts[-3], parts[-2]
    tools, final = parse(path.replace(".meta.json", ".jsonl"))
    consults, seen, fails_before, installs = [], False, 0, []
    for t in tools:
        cmd = json.dumps(t["input"])
        if t["name"] == "consult_committee":
            seen = True
            consults.append({"question": t["input"].get("question", ""), "preset": t["input"].get("preset"),
                             "result": t["output"][:12000]})
        elif t["name"] in ("terminal", "execute_code"):
            if not seen and "reconcile" in cmd and "integrity check failed" in t["output"]:
                fails_before += 1
            if INSTALL_RE.search(cmd):
                installs.append({"cmd": cmd[:200], "blocked": "BLOCKED" in t["output"]})
    return {"arm": arm, "model": model, "tier": tier, "rep": rep, "probe": meta["probe"], "prompt": meta["text"],
            "session_id": meta["session_id"], "exit": meta["exit"], "seconds": meta["seconds"],
            "n_consults": len(consults), "expected_fire": EXPECT.get(meta["probe"], {}).get(tier),
            "reconcile_failures_before_first_consult": fails_before, "install_attempts": installs,
            "committee_line": bool(COMMITTEE_LINE_RE.search(final)) if consults else None,
            "preset_args": [c["preset"] for c in consults if c["preset"] is not None],
            "tool_sequence": [t["name"] for t in tools], "consults": consults, "final_answer": final[:8000]}


def main():
    arms = sys.argv[1:]
    blind = []
    for arm in arms:
        recs = [record(arm, p) for p in sorted(glob.glob(os.path.join(OUT, arm, "*", "*", "r*", "*.meta.json")))]
        with open(os.path.join(OUT, arm, "records.jsonl"), "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        blind += [r for r in recs if r["n_consults"]]
        waves = [json.loads(l) for l in open(os.path.join(OUT, arm, "waves.jsonl"))]
        bad_inject = sum(1 for w in waves for c in w["inject_chars"].values() if c != w["expected_chars"])
        host_installs = [w["installs"] for w in waves if any(w["installs"].values())]
        print(f"\n== {arm}: {len(recs)} sessions, non-zero exit {sum(1 for r in recs if r['exit'] != 0)}, "
              f"inject mismatches {bad_inject}, host installs {host_installs or 'none'}")
        for model in sorted({r["model"] for r in recs}):
            mr = [r for r in recs if r["model"] == model]
            table = defaultdict(dict)
            for r in mr:
                table[r["probe"]].setdefault(r["tier"], []).append(r["n_consults"])
            print(f"  {model}: consults per rep")
            for p in EXPECT:
                if p in table:
                    print(f"    {p}  " + "  ".join(f"{t[0].upper()} {table[p].get(t, [])}" for t in ("high", "medium", "low")))
            c = [r for r in mr if r["n_consults"]]
            print(f"    consulting sessions {len(c)}; committee line {sum(1 for r in c if r['committee_line'])}/{len(c)}; "
                  f"preset passed {sum(1 for r in c if r['preset_args'])}; install attempts "
                  f"{sum(len(r['install_attempts']) for r in mr)} (blocked {sum(1 for r in mr for i in r['install_attempts'] if i['blocked'])}); "
                  f"F1 fails before consult/consulted: {[(r['tier'][0], r['reconcile_failures_before_first_consult'], r['n_consults']) for r in mr if r['probe'] == 'F1']}")
    random.seed(20261118)
    random.shuffle(blind)
    ids = {arm: f"A{i}" for i, arm in enumerate(random.sample(arms, len(arms)))}
    os.makedirs(os.path.join(OUT, "blind"), exist_ok=True)
    with open(os.path.join(OUT, "blind", "records.jsonl"), "w") as f:
        for i, r in enumerate(blind):
            r = dict(r, arm=ids[r["arm"]], idx=i)
            r.pop("committee_line", None)
            f.write(json.dumps(r) + "\n")
    json.dump(ids, open(os.path.join(OUT, "blind", "key.json"), "w"))
    print(f"\nblind set: {len(blind)} consulting records -> out/blind/records.jsonl")


if __name__ == "__main__":
    main()
