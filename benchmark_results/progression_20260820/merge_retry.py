#!/usr/bin/env python3
"""Merge the retry run's slides into the progression cohort.

The 188-slide samplesheet lost 25 slides to segmentation OOM and reader failures on the first
pass. `nf-prism2-progression-retry25c` re-ran those with the fixes from `fc1802d`
(/dev/shm raised to 32g, dataloader `max_workers=1` instead of 0) and recovered 12 of them.

Both scoring scripts already prefer `results_merged.json` over `results.json` when it exists, so
this writes that file and nothing downstream needs to change. The originals are left untouched, so
the merge can always be undone by deleting one file.

Provenance is recorded per slide in `_run`, because the two runs differ in more than timing: the
retry ran on a different `/dev/shm` allocation and a different dataloader setting, and if anything
about the recovered slides ever looks anomalous, the first question will be which run produced it.
"""
import csv, json, os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RETRY = os.path.join(REPO, "analysis", "data", "retry_c")

SPOT_RUN = "nf-prism2-progression-188-resume (spot)"
RETRY_RUN = "nf-prism2-progression-retry25c"

spot = json.load(open(os.path.join(HERE, "results.json")))
retry = json.load(open(os.path.join(RETRY, "results.json")))
print(f"spot run: {len(spot)} slides; retry run: {len(retry)} slides")

merged = {}
for rec in spot:
    rec["_run"] = SPOT_RUN
    merged[rec["sample"]] = rec
replaced, added = [], []
for rec in retry:
    rec["_run"] = RETRY_RUN
    # the retry re-attempted C_Duke_primary_21, which had already failed once; if it is present and
    # still empty it is better to know than to let it silently overwrite anything
    (replaced if rec["sample"] in merged else added).append(rec["sample"])
    merged[rec["sample"]] = rec
print(f"added {len(added)}: {sorted(added)}")
if replaced:
    print(f"REPLACED {len(replaced)} already-scored slides: {sorted(replaced)}")

lab = {r["sample"]: r for r in csv.DictReader(
    open(os.path.join(REPO, "assets/samplesheet_progression_labels.csv")))}
unknown = [s for s in merged if s not in lab]
if unknown:
    print(f"WARNING: {len(unknown)} scored slides are not in the samplesheet: {unknown}")

out = [merged[s] for s in sorted(merged)]
json.dump(out, open(os.path.join(HERE, "results_merged.json"), "w"), indent=1)
print(f"wrote results_merged.json: {len(out)} slides")

# the tsv is only used for eyeballing, so it is concatenated rather than rebuilt
rows, header = [], None
for path, run in [(os.path.join(HERE, "results.tsv"), SPOT_RUN),
                  (os.path.join(RETRY, "results.tsv"), RETRY_RUN)]:
    with open(path) as fh:
        rd = list(csv.reader(fh, delimiter="\t"))
    header = rd[0] if header is None else header
    for r in rd[1:]:
        rows.append((r[0], run, r))
keep = {}
for sample, run, r in rows:
    if sample not in keep or run == RETRY_RUN:
        keep[sample] = (run, r)
with open(os.path.join(HERE, "results_merged.tsv"), "w", newline="") as fh:
    w = csv.writer(fh, delimiter="\t")
    w.writerow(header + ["_run"])
    for s in sorted(keep):
        run, r = keep[s]
        w.writerow(r + [run])
print(f"wrote results_merged.tsv: {len(keep)} slides")

# a short census of what the merge changed, per arm and class
def arm(s):
    a = lab[s]["arm"]
    return "A" if a.startswith("A") else a
before, after = {}, {}
for s in merged:
    if s not in lab:
        continue
    k = (arm(s), lab[s]["ttt"])
    after[k] = after.get(k, 0) + 1
    if merged[s]["_run"] == SPOT_RUN:
        before[k] = before.get(k, 0) + 1
print(f"\n{'arm / class':<34}{'before':>8}{'after':>8}")
for k in sorted(after):
    b, a_ = before.get(k, 0), after[k]
    print(f"{k[0] + '  ' + k[1]:<34}{b:>8}{a_:>8}" + ("   <-- gained" if a_ > b else ""))
json.dump(dict(spot_run=SPOT_RUN, retry_run=RETRY_RUN, n_spot=len(spot), n_retry=len(retry),
               n_merged=len(out), added=sorted(added), replaced=sorted(replaced),
               by_arm_class_after={f"{k[0]} | {k[1]}": v for k, v in sorted(after.items())},
               by_arm_class_before={f"{k[0]} | {k[1]}": v for k, v in sorted(before.items())}),
          open(os.path.join(HERE, "merge_provenance.json"), "w"), indent=2)
print("\nwrote merge_provenance.json")
