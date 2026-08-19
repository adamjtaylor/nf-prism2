#!/usr/bin/env python3
"""Per-slide reports: within-slide Leiden communities, a spatial map, representative tiles, and
HTAN metadata against the PRISM2 answer.

Tile images and thumbnails are read from the Synapse pre-signed URL by HTTP range request via
tifffile's zarr store, so a 1 GB slide costs a few MB rather than a full download. Measured on
syn53640639: 6.8 MB pulled from a 1070 MB file.
"""
import argparse, csv, io, json, os, sys, time
import h5py, numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt

SURFACE, INK, INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#8a8880"]
mpl.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": "#dcdbd6", "axes.linewidth": 0.8, "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False})

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(HERE, "slide_reports")
FEAT = "/tmp/htan10/features"
MAX_TILES_FOR_CLUSTERING = 4000
N_COMMUNITIES_SHOWN = 8
TILES_PER_COMMUNITY = 3
RES = 0.3


class Counting(io.RawIOBase):
    def __init__(self, f):
        self.f, self.n = f, 0
    def read(self, size=-1):
        b = self.f.read(size); self.n += len(b); return b
    def readinto(self, buf):
        # tifffile passes numpy arrays here, so cast to a byte view before assigning
        mv = memoryview(buf).cast("B")
        data = self.f.read(len(mv))
        mv[:len(data)] = data
        self.n += len(data)
        return len(data)
    def seek(self, *a): return self.f.seek(*a)
    def tell(self): return self.f.tell()
    def seekable(self): return True
    def readable(self): return True


def rgb(a):
    """Coerce a tifffile region to HxWx3 uint8, or None if it is not an RGB image."""
    a = np.asarray(a)
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[-1] not in (3, 4):
        a = np.moveaxis(a, 0, -1)
    if a.ndim == 3 and a.shape[-1] >= 3:
        a = a[..., :3]
    elif a.ndim == 2:
        a = np.stack([a] * 3, -1)
    else:
        return None
    if a.dtype != np.uint8:
        a = a.astype(np.float32)
        hi = np.percentile(a, 99.5) or 1.0
        a = np.clip(a / hi, 0, 1) * 255
    return a.astype(np.uint8)


def stream_slide(syn_id, regions, thumb_max=1600):
    """Return (thumbnail, [tile arrays], megabytes_pulled) using range reads only."""
    import fsspec, tifffile, zarr, synapseclient
    syn = synapseclient.Synapse(silent=True); syn.login(silent=True)
    url = syn.restGET(f"/entity/{syn_id}/file?redirect=FALSE")
    fs = fsspec.filesystem("http", block_size=1 << 20)
    cnt = Counting(fs.open(url, "rb"))
    with tifffile.TiffFile(cnt) as tf:
        levels = tf.series[0].levels
        # smallest level that is still big enough to be a useful thumbnail
        thumb = None
        for lv in reversed(levels):
            h, w = lv.shape[:2] if lv.shape[0] > 4 else lv.shape[1:3]
            if max(h, w) >= 400 or lv is levels[0]:
                thumb = rgb(lv.asarray()); break
        z = zarr.open(levels[0].aszarr(), mode="r")
        arr = z if hasattr(z, "shape") else z["0"]
        H, W = (arr.shape[:2] if arr.shape[-1] in (3, 4) else arr.shape[1:3])
        tiles = []
        for (x, y, s) in regions:
            x, y, s = int(x), int(y), int(s)
            if y + s > H or x + s > W:
                tiles.append(None); continue
            reg = arr[y:y + s, x:x + s] if arr.shape[-1] in (3, 4) else arr[:, y:y + s, x:x + s]
            tiles.append(rgb(reg))
    return thumb, tiles, cnt.n / 1e6


def verdict(field, htan, answers):
    """Deliberately conservative: only claim agreement on an unambiguous string match."""
    a = " ".join(str(v).lower() for v in answers if v)
    h = str(htan).lower()
    if not h:
        return "no HTAN label"
    if not a:
        return "not asked"
    key = {"lung": ["lung"], "breast": ["breast"], "colon": ["colon", "colorect"],
           "skin": ["skin", "cutaneous"], "pancreas": ["pancrea"],
           "fallopian": ["fallopian", "tube", "ovar"], "trachea": ["lung", "trachea"]}
    for k, words in key.items():
        if k in h:
            return "agree" if any(w in a for w in words) else "differs"
    for w in ["adenocarcinoma", "squamous", "melanoma", "serous", "lobular", "in situ", "ductal"]:
        if w in h:
            return "agree" if w in a else ("broader" if "carcinoma" in a or "cancer" in a else "differs")
    if h in ("yes", "no"):
        return "compare the score against the label by hand"
    return "compare by hand"


def build(sample, meta_row, results, only=None):
    if only and sample != only:
        return None
    h5 = os.path.join(FEAT, f"{sample}.h5")
    if not os.path.exists(h5):
        print(f"  {sample}: no features (slide failed to read); skipping")
        return None
    import scanpy as sc, anndata as ad
    with h5py.File(h5, "r") as h:
        key = "features" if "features" in h else [k for k in h if h[k].ndim == 2][0]
        n = h[key].shape[0]
        rng = np.random.default_rng(0)
        idx = np.sort(rng.choice(n, size=min(MAX_TILES_FOR_CLUSTERING, n), replace=False))
        X = np.asarray(h[key][idx], dtype=np.float32)
        C = np.asarray(h["coords"][idx])
        Xall = np.asarray(h[key][:], dtype=np.float32)   # for assigning every tile to a community
        Call = np.asarray(h["coords"][:])
        at = dict(h["coords"].attrs)
    ps0 = int(at.get("patch_size_level0", 224))
    L0W, L0H = int(at.get("level0_width", 0)), int(at.get("level0_height", 0))

    a = ad.AnnData(X)
    sc.pp.pca(a, n_comps=min(50, X.shape[0] - 1, X.shape[1]), random_state=0)
    sc.pp.neighbors(a, n_neighbors=min(15, max(3, X.shape[0] // 20)), metric="cosine",
                    use_rep="X_pca", random_state=0)
    sc.tl.leiden(a, resolution=RES, key_added="leiden", flavor="igraph", n_iterations=2,
                 directed=False, random_state=0)
    sc.tl.umap(a, random_state=0)
    emb, cl = a.obsm["X_umap"], a.obs["leiden"].to_numpy().astype(int)
    P = a.obsm["X_pca"]
    sizes = {c: int((cl == c).sum()) for c in set(cl)}
    shown = [c for c, _ in sorted(sizes.items(), key=lambda kv: -kv[1])][:N_COMMUNITIES_SHOWN]
    disp = {c: j for j, c in enumerate(shown)}          # community -> display slot

    # Leiden ran on a subsample, so assign every tile to its nearest community centroid in the
    # same PCA space. Without this the spatial grid would only show the sampled tiles.
    pca = a.uns["pca"]; mean = X.mean(0)
    cent = np.stack([P[cl == c].mean(0) for c in shown])
    Pall = (Xall - mean) @ a.varm["PCs"]
    d = ((Pall[:, None, :] - cent[None, :, :]) ** 2).sum(-1) if len(Xall) < 8000 else None
    if d is None:      # chunk it for the big slides to keep memory sane
        assign = np.empty(len(Pall), dtype=int)
        for i in range(0, len(Pall), 8000):
            blk = Pall[i:i + 8000]
            assign[i:i + 8000] = ((blk[:, None, :] - cent[None, :, :]) ** 2).sum(-1).argmin(1)
    else:
        assign = d.argmin(1)

    # representative tiles: nearest to the community centroid in PCA space
    regions, picks = [], []
    for c in shown:
        m = np.where(cl == c)[0]
        d = np.linalg.norm(P[m] - P[m].mean(0), axis=1)
        for i in m[np.argsort(d)[:TILES_PER_COMMUNITY]]:
            regions.append((C[i, 0], C[i, 1], ps0)); picks.append((c, i))
    t0 = time.time()
    thumb, tiles, mb = stream_slide(meta_row["synapse_id"], regions)
    print(f"  {sample}: {n} tiles, {len(set(cl))} communities, streamed {mb:.1f} MB in {time.time()-t0:.0f}s")

    # ---------------- figure ----------------
    ncol = TILES_PER_COMMUNITY
    fig = plt.figure(figsize=(16.5, 8.2), constrained_layout=True)
    gs = fig.add_gridspec(len(shown), 3 + ncol, width_ratios=[1.15, 1.15, 1.5] + [0.58] * ncol)

    ax = fig.add_subplot(gs[:, 0])
    if thumb is not None:
        ax.imshow(thumb)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("Slide thumbnail", fontsize=9.5, color=INK)

    # categorical grid: one cell per 224 px tile, coloured by community
    ax = fig.add_subplot(gs[:, 1])
    gx, gy = int(np.ceil(L0W / ps0)), int(np.ceil(L0H / ps0))
    grid = np.full((gy, gx), -1, dtype=int)
    grid[(Call[:, 1] // ps0).clip(0, gy - 1), (Call[:, 0] // ps0).clip(0, gx - 1)] = assign
    from matplotlib.colors import ListedColormap, BoundaryNorm
    cmap = ListedColormap(["#f0efec"] + [CAT[j % len(CAT)] for j in range(len(shown))])
    norm = BoundaryNorm(list(range(-1, len(shown) + 1)), cmap.N)
    ax.imshow(grid, cmap=cmap, norm=norm, interpolation="nearest")
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(f"Community per tile ({gx} x {gy} grid)\ngrey = no tile kept", fontsize=9.5, color=INK)

    ax = fig.add_subplot(gs[:, 2])
    other = np.array([c not in shown for c in cl])
    if other.any():
        ax.scatter(emb[other, 0], emb[other, 1], s=3, color="#d8d7d2", linewidths=0,
                   label=f"smaller communities ({other.sum()})", rasterized=True)
    for j, c in enumerate(shown):
        m = cl == c
        ax.scatter(emb[m, 0], emb[m, 1], s=3.4, alpha=0.6, linewidths=0,
                   color=CAT[j % len(CAT)], label=f"community {j+1} ({sizes[c]})", rasterized=True)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel("UMAP 1"); ax.set_ylabel("UMAP 2")
    ax.set_aspect("equal")   # both UMAP axes are the same units; unequal aspect distorts it
    ax.legend(frameon=False, fontsize=7.5, markerscale=3.0, loc="best")
    ax.set_title(f"Within-slide Leiden, resolution {RES}", fontsize=9.5, color=INK)
    for j, c in enumerate(shown):
        for k in range(ncol):
            axt = fig.add_subplot(gs[j, 3 + k])
            t = tiles[j * ncol + k] if j * ncol + k < len(tiles) else None
            if t is not None:
                axt.imshow(t)
            else:
                axt.text(.5, .5, "n/a", ha="center", va="center", fontsize=7, color=INK2)
            axt.set_xticks([]); axt.set_yticks([])
            for sp in axt.spines.values():
                sp.set_visible(True); sp.set_color(CAT[j % len(CAT)]); sp.set_linewidth(2.2)
            if k == 0:
                # tissue content of the representatives, so a background-dominated community
                # is labelled as such rather than passed off as morphology
                row = [tiles[j * ncol + q] for q in range(ncol) if j * ncol + q < len(tiles)]
                row = [x for x in row if x is not None]
                frac = np.mean([(x.astype(np.float32).max(-1) - x.astype(np.float32).min(-1) > 18).mean()
                                for x in row]) if row else float("nan")
                axt.set_ylabel(f"c{j+1}\n{frac*100:.0f}% tissue", fontsize=7,
                               color=CAT[j % len(CAT)], rotation=0, labelpad=22, va="center")
    fig.suptitle(f"{sample}  ({meta_row['synapse_id']}, {meta_row['PrimaryDiagnosis'] or 'no diagnosis recorded'})"
                 f"\n{n:,} tiles at 20x, Leiden resolution {RES}, representative tiles nearest each community centroid",
                 fontsize=11.5, color=INK)
    png = os.path.join(OUT, f"{sample}.png")
    fig.savefig(png, dpi=150); plt.close(fig)

    # ---------------- markdown ----------------
    yn = results.get("yes_no", {}); oe = results.get("open_ended", {}); mc = results.get("multiple_choice", {})
    rows = [
        ("Primary site / organ", meta_row["TissueorOrganofOrigin"],
         f"{oe.get('primary_site',{}).get('answer','')}  |  MC: {mc.get('primary_site_mc',{}).get('answer','')}"),
        ("Diagnosis / histologic type", meta_row["PrimaryDiagnosis"],
         f"{oe.get('cancer_type',{}).get('answer','')}  |  MC: {mc.get('histologic_type_mc',{}).get('answer','')}"),
        ("Tumour grade", meta_row["TumorGrade"],
         f"{oe.get('tumor_grade',{}).get('answer','')}  |  high grade score: {yn.get('high_grade',{}).get('score',float('nan')):.3f}"),
        ("Lymphovascular invasion", meta_row["LymphaticInvasionPresent"],
         f"score {yn.get('lymphovascular_invasion',{}).get('score',float('nan')):.3f}"),
        ("Perineural invasion", meta_row["PerineuralInvasionPresent"],
         f"score {yn.get('perineural_invasion',{}).get('score',float('nan')):.3f}"),
        ("Pathologic stage", meta_row["AJCCPathologicStage"], "not asked"),
        ("Breslow thickness", meta_row["BreslowThickness"], "not asked"),
    ]
    md = [f"# {sample}", "",
          f"* Synapse: [`{meta_row['synapse_id']}`](https://www.synapse.org/#!Synapse:{meta_row['synapse_id']})",
          f"* HTAN participant: `{meta_row['participant']}`",
          f"* Tiles at 20x: **{n:,}**, level-0 {L0W} x {L0H}, tile {ps0} px at level 0",
          f"* Leiden communities (resolution {RES}): **{len(set(cl))}**, "
          f"largest {N_COMMUNITIES_SHOWN} shown",
          "", f"![report]({sample}.png)", "",
          "## HTAN metadata against the PRISM2 answer", "",
          "| Field | HTAN records | PRISM2 answered | Verdict |", "|---|---|---|---|"]
    for field, htan, ans in rows:
        v = "not asked" if ans == "not asked" else verdict(field, htan, [ans])
        md.append(f"| {field} | {htan or '_not recorded_'} | {ans} | {v} |")
    md += ["", "Verdicts are deliberately conservative and based on string matching, and the HTAN",
           "labels are **case-level**, so a disagreement is not necessarily a model error.", "",
           "## Generated report", "", f"> {results.get('report','')}", "",
           "## All yes/no scores", "", "| Question | Score |", "|---|---|"]
    for q, d in yn.items():
        md.append(f"| {d.get('question', q)} | {d['score']:.3f} |")
    open(os.path.join(OUT, f"{sample}.md"), "w").write("\n".join(md) + "\n")
    return dict(sample=sample, n_tiles=int(n), communities=int(len(set(cl))), mb_streamed=round(mb, 1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    clinical = {r["sample"]: r for r in csv.DictReader(open(os.path.join(REPO, "assets/htan10_clinical.csv")))}
    results = {r["sample"]: r for r in json.load(open(os.path.join(HERE, "results.json")))}
    summary = []
    for s in clinical:
        if s not in results:
            continue
        out = build(s, clinical[s], results[s], only=args.only)
        if out:
            summary.append(out)
    if summary:
        json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=2)
        print(f"wrote {len(summary)} slide reports, {sum(x['mb_streamed'] for x in summary):.0f} MB streamed in total")
