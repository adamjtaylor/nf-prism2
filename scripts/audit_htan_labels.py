#!/usr/bin/env python3
"""Which HTAN clinical fields are actually populated for patients with H&E whole-slide images?

Question design should follow label availability, not the other way round. This audits every
column of the clinical tables against the H&E Level 2 cohort and ranks by coverage, so a question
is only asked when something exists to score it against.
"""
import json, os, re, subprocess, sys
PROJECT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HTAN = os.path.join(PROJECT, ".venv", "bin", "htan")

NULLS = "('','Not Reported','unknown','Unknown','Not Applicable','NA','not reported')"
HE_PATIENTS = ("SELECT DISTINCT HTANParticipantID FROM files "
               "WHERE level='Level 2' AND assayName='H&E' AND synapseId <> ''")
HE_BIOSPEC = ("SELECT arrayJoin(biospecimenIds) FROM files "
              "WHERE level='Level 2' AND assayName='H&E' AND synapseId <> ''")


def run(sql, out_json=False):
    cmd = [HTAN, "query", "portal", "sql", sql] + (["--output", "json"] if out_json else [])
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT)
    if r.returncode:
        sys.exit(r.stderr[-1500:])
    return json.loads(r.stdout[r.stdout.index("["):]) if out_json else r.stdout


def describe(table):
    r = subprocess.run([HTAN, "query", "portal", "describe", table],
                       capture_output=True, text=True, cwd=PROJECT)
    if r.returncode:
        sys.exit(r.stderr[-1500:])
    return r.stdout


def columns(table):
    txt = describe(table)
    cols = []
    for line in txt.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s+(String|Array\(String\)|U?Int\d+|Float\d+|Nullable)", line.strip())
        if m and m.group(1) not in ("Component",):
            cols.append((m.group(1), m.group(2)))
    return cols


def audit(table, key, subquery, label):
    cols = columns(table)
    scalar = [c for c, t in cols if not t.startswith("Array")]
    parts = [f"count(*) AS _total"]
    for c in scalar:
        parts.append(f"countIf(toString({c}) NOT IN {NULLS}) AS `pop_{c}`")
    sql = f"SELECT {', '.join(parts)} FROM {table} WHERE {key} IN ({subquery})"
    row = run(sql, out_json=True)[0]
    total = int(row.pop("_total"))
    ranked = sorted(((k[4:], int(v)) for k, v in row.items()), key=lambda kv: -kv[1])
    print(f"\n### {label}: {total} rows\n")
    print(f"{'field':<34}{'populated':>10}{'%':>7}")
    for k, v in ranked:
        if v:
            print(f"{k:<34}{v:>10}{100*v/total:>6.0f}%")
    zero = [k for k, v in ranked if v == 0]
    print(f"\ncompletely unpopulated ({len(zero)}): {', '.join(zero[:22])}"
          f"{' ...' if len(zero) > 22 else ''}")
    return {"table": table, "rows": total, "populated": dict(ranked)}


if __name__ == "__main__":
    out = {}
    out["diagnosis"] = audit("diagnosis", "HTANParticipantID", HE_PATIENTS,
                             "diagnosis, for patients with an H&E Level 2 slide")
    out["specimen"] = audit("specimen", "HTANBiospecimenID", HE_BIOSPEC,
                            "specimen, for biospecimens behind an H&E Level 2 slide")
    out["demographics"] = audit("demographics", "HTANParticipantID", HE_PATIENTS,
                                "demographics, for patients with an H&E Level 2 slide")
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "assets", "htan_label_availability.json"), "w"), indent=2)
