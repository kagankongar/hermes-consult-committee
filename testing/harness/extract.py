#!/usr/bin/env python3
"""Turn out/<run>/ transcripts + the call log into per-session records.

Usage: extract.py <run>  -> out/<run>/records.jsonl and prints a firing table.
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.expanduser("~/.hermes/profiles/t-dispose/logs/consult-committee.log")
EXPECT = {  # probe -> tier -> expected fire (True/False/None = not scored on firing)
    "D1": {"high": True, "medium": True, "low": False},
    "I1": {"high": True, "medium": True, "low": True},
    "F1": {"high": True, "medium": None, "low": None},  # Med/Low: only after 2 failures
    "S1": {"high": True, "medium": True, "low": False},
    "N1": {"high": False, "medium": False, "low": False},
    "N2": {"high": False, "medium": False, "low": False},
}


def log_blocks():
    blocks = defaultdict(list)
    text = open(LOG, encoding="utf-8", errors="replace").read()
    for b in re.split(r"(?m)^(?==== )", text):
        m = re.search(r"\| conversation (\S+) ", b.splitlines()[0] if b else "")
        if m:
            blocks[m.group(1)].append(b)
    return blocks


def parse(path):
    events = [json.loads(l) for l in open(path) if l.strip().startswith("{")]
    tools, final, consults = [], "", []
    for e in events:
        if e.get("type") == "tool_use":
            tools.append({"name": e["name"], "input": e.get("input")})
        elif e.get("type") == "tool_result" and tools:
            out = e.get("output") or ""
            tools[-1]["output"] = out if e["name"] == "consult_committee" else out[:600]
        elif e.get("type") == "result":
            final = e.get("text") or ""
    reconcile_fail_before_consult = 0
    seen_consult = False
    for t in tools:
        if t["name"] == "consult_committee":
            seen_consult = True
            consults.append({"question": (t.get("input") or {}).get("question", ""),
                             "preset": (t.get("input") or {}).get("preset"),
                             "result": t.get("output", "")[:9000]})
        elif not seen_consult and t["name"] in ("terminal", "execute_code"):
            cmd = json.dumps(t.get("input"))
            if "reconcile" in cmd and "integrity check failed" in (t.get("output") or ""):
                reconcile_fail_before_consult += 1
    return tools, final, consults, reconcile_fail_before_consult


def main():
    run = sys.argv[1]
    root = os.path.join(HERE, "out", run)
    blocks = log_blocks()
    recs = []
    for meta_path in sorted(glob.glob(os.path.join(root, "*", "r*", "*.meta.json"))):
        meta = json.load(open(meta_path))
        tier = meta_path.split(os.sep)[-3]
        rep = meta_path.split(os.sep)[-2]
        tools, final, consults, rfails = parse(meta_path.replace(".meta.json", ".jsonl"))
        sid = meta.get("session_id")
        lb = blocks.get(sid, [])
        exp = EXPECT.get(meta["probe"], {}).get(tier)
        recs.append({
            "run": run, "tier": tier, "rep": rep, "probe": meta["probe"], "prompt": meta["text"],
            "session_id": sid, "exit": meta["exit"], "seconds": meta["seconds"],
            "fired": len(consults) > 0, "n_consults": len(consults), "n_log_blocks": len(lb),
            "log_outcomes": [re.search(r"(?m)^outcome:\s*(.*)$", b).group(1) if re.search(r"(?m)^outcome:", b) else "?" for b in lb],
            "expected_fire": exp, "reconcile_failures_before_first_consult": rfails,
            "tool_sequence": [t["name"] for t in tools], "consults": consults,
            "final_answer": final[:6000], "git_status": meta.get("git_status", ""),
        })
    with open(os.path.join(root, "records.jsonl"), "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    # firing table
    table = defaultdict(lambda: defaultdict(list))
    for r in recs:
        table[r["probe"]][r["tier"]].append(r["n_consults"])
    tiers = ["high", "medium", "low"]
    print(f"{run}: consult calls per rep (fired/3)")
    print("probe  " + "  ".join(f"{t:>14}" for t in tiers))
    for p in sorted(table):
        cells = []
        for t in tiers:
            v = table[p][t]
            cells.append(f"{sum(1 for x in v if x)}/{len(v)} {v}".rjust(14))
        print(f"{p:6} " + "  ".join(cells))
    bad = [r for r in recs if r["exit"] != 0 or not r["session_id"]]
    print(f"sessions: {len(recs)}, non-zero exit or no session: {len(bad)}")
    print("log/transcript mismatches:", [(r["tier"], r["rep"], r["probe"]) for r in recs if r["n_log_blocks"] != r["n_consults"]])


if __name__ == "__main__":
    main()
