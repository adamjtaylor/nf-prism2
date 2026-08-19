#!/usr/bin/env python
"""
Visualise nf-prism2 embeddings for one slide.

Inputs
  --features   TRIDENT h5 with datasets `features` (N, 1280) and `coords` (N, 2),
               i.e. the Virchow2 class-token tile embeddings that PRISM2 consumes.
  --slide-npz  PRISM2 `<sample>.embeddings.npz` with `base` (1, 2560) and
               `diagnostic` (1, 3072).
  --thumb      Optional TRIDENT QC thumbnail, used as a spatial reference panel.

Outputs a set of PNGs: tile embeddings in UMAP space and in slide space, the
same clusters as validated small multiples, a continuous spatial gradient, and
the slide-level embedding profile.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import h5py
import matplotlib as mpl
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
from matplotlib.lines import Line2D
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA

# --- design tokens (dataviz reference palette, light mode) --------------------
SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
MUTED = '#898781'
AXIS = '#c3c2b7'
CONTEXT = '#dedcd6'          # out-of-focus marks in small multiples
# categorical slots in fixed order, never cycled
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300',
          '#4a3aa7', '#e34948']
# sequential: one hue, light -> dark (blue ramp steps 100..700)
BLUE_RAMP = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']
SEQ = LinearSegmentedColormap.from_list('blue_seq', BLUE_RAMP)
# diverging: blue <-> red with a neutral gray midpoint
DIV = LinearSegmentedColormap.from_list(
    'blue_red', ['#0d366b', '#3987e5', '#cde2fb', '#f0efec', '#f7c9c9', '#e34948', '#8f1f1f'])

mpl.rcParams.update({
    'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE, 'savefig.facecolor': SURFACE,
    'text.color': INK, 'axes.labelcolor': INK_2, 'xtick.color': MUTED, 'ytick.color': MUTED,
    'axes.edgecolor': AXIS, 'axes.linewidth': 0.8, 'font.size': 9,
    'axes.titlesize': 10, 'axes.titleweight': 'semibold', 'axes.titlelocation': 'left',
    'axes.titlepad': 8, 'legend.frameon': False, 'figure.dpi': 130,
    'font.family': ['Helvetica Neue', 'Helvetica', 'DejaVu Sans'],
})


def bare(ax, keep_frame=False):
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(keep_frame)


def load_tiles(path):
    with h5py.File(path, 'r') as f:
        feats = np.asarray(f['features'][:], dtype=np.float32)
        coords = np.asarray(f['coords'][:], dtype=np.int64)
        attrs = dict(f['coords'].attrs)
        encoder = f['features'].attrs.get('encoder', 'unknown')
    meta = {k: (v.item() if hasattr(v, 'item') else v) for k, v in attrs.items()}
    meta['encoder'] = str(encoder)
    return feats, coords, meta


def rasterise(coords, values, step, fill=np.nan):
    """Tiles sit on a regular grid; render them as a raster instead of a scatter."""
    col = ((coords[:, 0] - coords[:, 0].min()) // step).astype(int)
    row = ((coords[:, 1] - coords[:, 1].min()) // step).astype(int)
    grid = np.full((row.max() + 1, col.max() + 1), fill, dtype=float)
    grid[row, col] = values
    return grid


def leiden(graph, resolution, seed):
    """Leiden on UMAP's fuzzy kNN graph — the same connectivities scanpy clusters
    on, so the partition and the layout come from one neighbourhood structure."""
    import igraph as ig
    import leidenalg
    from scipy.sparse import triu

    g = triu(graph.tocoo(), k=1).tocoo()
    G = ig.Graph(n=graph.shape[0], edges=list(zip(g.row.tolist(), g.col.tolist())))
    G.es['weight'] = g.data.tolist()
    part = leidenalg.find_partition(
        G, leidenalg.RBConfigurationVertexPartition, weights='weight',
        resolution_parameter=resolution, n_iterations=-1, seed=seed)
    return np.asarray(part.membership), part.modularity


def dense_point(xy, mask, bins=25):
    """Label anchor: the densest spot of a cluster, not its centroid — ring- or
    crescent-shaped clusters put their centroid in empty space. Smooth first, or
    a thin dense streak outvotes the diffuse main mass."""
    from scipy.ndimage import gaussian_filter
    pts = xy[mask]
    h, xe, ye = np.histogram2d(pts[:, 0], pts[:, 1], bins=bins)
    h = gaussian_filter(h, 1.5)
    i, j = np.unravel_index(np.argmax(h), h.shape)
    peak = np.array([(xe[i] + xe[i + 1]) / 2, (ye[j] + ye[j + 1]) / 2])
    # snap to an actual member so the label never floats over empty space
    return tuple(pts[np.argmin(((pts - peak) ** 2).sum(axis=1))])


def spatial_extent(coords, step):
    x0, y0 = coords[:, 0].min(), coords[:, 1].min()
    x1 = coords[:, 0].max() + step
    y1 = coords[:, 1].max() + step
    return (x0, x1, y1, y0)          # y inverted: slide coords run top-down


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--features', required=True)
    ap.add_argument('--slide-npz')
    ap.add_argument('--thumb')
    ap.add_argument('--prism2-json')
    ap.add_argument('--wsi', help='source slide; enables representative tile crops')
    ap.add_argument('--tiles-per-cluster', type=int, default=8)
    ap.add_argument('--outdir', default='figures')
    ap.add_argument('--sample', default=None)
    ap.add_argument('--cluster', choices=['leiden', 'kmeans'], default='leiden')
    ap.add_argument('--resolution', type=float, default=0.5,
                    help='Leiden resolution; higher = more clusters')
    ap.add_argument('--k', type=int, default=6, help='k-means clusters (--cluster kmeans)')
    ap.add_argument('--max-legend', type=int, default=8,
                    help='clusters shown individually; the rest fold into "Other"')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    out = Path(args.outdir); out.mkdir(parents=True, exist_ok=True)
    feats, coords, meta = load_tiles(args.features)
    sample = args.sample or meta.get('name', 'slide')
    step = int(meta.get('patch_size_level0', meta.get('patch_size', 224)))
    n, d = feats.shape
    print(f'{sample}: {n} tiles x {d}-d {meta["encoder"]}; grid step {step} px')

    # Virchow2 class tokens are compared by cosine similarity, so L2-normalise
    # before any Euclidean method (PCA, k-means, UMAP).
    X = feats / np.linalg.norm(feats, axis=1, keepdims=True)

    pca = PCA(n_components=min(50, d), random_state=args.seed).fit(X)
    Xp = pca.transform(X)
    print(f'PCA: PC1-10 explain {pca.explained_variance_ratio_[:10].sum():.1%}')

    import umap
    reducer = umap.UMAP(n_neighbors=30, min_dist=0.1, metric='cosine',
                        random_state=args.seed).fit(Xp)
    emb = reducer.embedding_

    if args.cluster == 'leiden':
        lab, modularity = leiden(reducer.graph_, args.resolution, args.seed)
        method = f'Leiden (res {args.resolution:g}, modularity {modularity:.3f})'
    else:
        lab = KMeans(n_clusters=args.k, n_init=10, random_state=args.seed).fit_predict(Xp)
        method = f'k-means (k={args.k})'

    # order by size so slot 1 is always the largest morphology, then fold the
    # tail into "Other" rather than inventing hues past the palette
    k_raw = lab.max() + 1
    order = np.argsort(-np.bincount(lab, minlength=k_raw))
    remap = np.zeros(k_raw, dtype=int); remap[order] = np.arange(k_raw)
    lab = remap[lab]
    n_other = max(0, k_raw - args.max_legend)
    if n_other:
        lab = np.minimum(lab, args.max_legend)
    args.k = int(lab.max() + 1)
    counts = np.bincount(lab, minlength=args.k)
    print(f'{method}: {k_raw} clusters'
          + (f', smallest {n_other} folded into "Other"' if n_other else ''))

    extent = spatial_extent(coords, step)
    cluster_names = [f'Cluster {i + 1}' for i in range(args.k)]
    if n_other:
        cluster_names[-1] = f'Other ({n_other} small clusters)'
        palette = list(SERIES[:args.k - 1]) + [MUTED]
    else:
        palette = list(SERIES[:args.k])

    # ---------------------------------------------------------------- fig 1
    # Where the tiles are, before any colour encoding. One series, no legend.
    ncol = 3 if args.thumb else 2
    fig, axes = plt.subplots(1, ncol, figsize=(4.2 * ncol, 4.6))
    ax = axes[0]
    if args.thumb:
        from PIL import Image
        ax.imshow(np.asarray(Image.open(args.thumb)))
        ax.set_title(f'{sample}\nTRIDENT tissue segmentation')
        bare(ax); ax = axes[1]
    ax.imshow(rasterise(coords, np.ones(n), step), extent=extent, origin='upper',
              cmap=ListedColormap([SERIES[0]]), interpolation='nearest')
    ax.set_title(f'Slide space\n{n:,} tiles, {step} px @ {meta.get("target_magnification", 20):g}x')
    bare(ax, keep_frame=True)
    ax = axes[-1]
    ax.scatter(emb[:, 0], emb[:, 1], s=2, c=SERIES[0], alpha=0.35, linewidths=0)
    ax.set_title('UMAP of tile embeddings\n1280-d Virchow2 class token, cosine')
    ax.set_xlabel('UMAP 1'); ax.set_ylabel('UMAP 2')
    ax.set_box_aspect(axes[-2].get_images()[0].get_array().shape[0] /
                      axes[-2].get_images()[0].get_array().shape[1])
    bare(ax, keep_frame=True)
    fig.tight_layout()
    fig.savefig(out / f'{sample}.01_overview.png', bbox_inches='tight')
    plt.close(fig)

    # ---------------------------------------------------------------- fig 2
    # Clusters in both spaces. Categorical hues in fixed order + direct labels
    # at every centroid, so identity is never carried by colour alone.
    cmap_k = ListedColormap(palette)
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.2))
    ax = axes[0]
    ax.scatter(emb[:, 0], emb[:, 1], s=2.5, c=[palette[i] for i in lab],
               alpha=0.55, linewidths=0)
    for i in range(args.k):
        cx, cy = dense_point(emb, lab == i)
        ax.text(cx, cy, str(i + 1), ha='center', va='center', fontsize=11, weight='bold',
                color=INK, bbox=dict(boxstyle='circle,pad=0.28', fc=SURFACE, ec=palette[i], lw=2))
    ax.set_title('UMAP space')
    bare(ax, keep_frame=True)

    ax = axes[1]
    ax.imshow(rasterise(coords, lab.astype(float), step), extent=extent, origin='upper',
              cmap=cmap_k, vmin=-0.5, vmax=args.k - 0.5, interpolation='nearest')
    centres = coords + step / 2
    for i in range(args.k):
        cx, cy = dense_point(centres, lab == i)
        ax.text(cx, cy, str(i + 1), ha='center', va='center', fontsize=11, weight='bold',
                color=INK, bbox=dict(boxstyle='circle,pad=0.28', fc=SURFACE, ec=palette[i], lw=2))
    ax.set_title('Slide space')
    bare(ax, keep_frame=True)

    handles = [Line2D([], [], marker='s', ls='', ms=8, mfc=palette[i], mec=palette[i],
                      label=f'{cluster_names[i]}  ({counts[i]:,} tiles, {counts[i] / n:.0%})')
               for i in range(args.k)]
    fig.legend(handles=handles, loc='lower center', ncol=min(args.k, 3),
               bbox_to_anchor=(0.5, -0.10), labelcolor=INK_2)
    fig.suptitle(f'{sample} — {args.k} morphology clusters, {method}, '
                 f'same colours in both spaces',
                 x=0.055, ha='left', weight='semibold')
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out / f'{sample}.02_clusters_umap_spatial.png', bbox_inches='tight')
    plt.close(fig)

    # ---------------------------------------------------------------- fig 3
    # Small multiples: past three categorical slots a scatter cannot stay
    # colourblind-safe, so each cluster gets its own panel against grey context.
    fig, axes = plt.subplots(2, args.k, figsize=(2.35 * args.k, 5.6),
                             gridspec_kw=dict(hspace=0.18, wspace=0.06))
    axes = np.atleast_2d(axes)
    for i in range(args.k):
        m = lab == i
        ax = axes[0, i]
        ax.scatter(emb[~m, 0], emb[~m, 1], s=1.5, c=CONTEXT, linewidths=0)
        ax.scatter(emb[m, 0], emb[m, 1], s=1.8, c=SERIES[0], linewidths=0)
        ax.set_title(f'{cluster_names[i]}\n{counts[i]:,} tiles · {counts[i] / n:.0%}',
                     fontsize=9, color=INK)
        bare(ax, keep_frame=True)
        ax = axes[1, i]
        base = rasterise(coords, np.zeros(n), step)
        ax.imshow(base, extent=extent, origin='upper',
                  cmap=ListedColormap([CONTEXT]), interpolation='nearest')
        hl = rasterise(coords, np.where(m, 1.0, np.nan), step)
        ax.imshow(hl, extent=extent, origin='upper',
                  cmap=ListedColormap([SERIES[0]]), interpolation='nearest')
        bare(ax, keep_frame=True)
    axes[0, 0].set_ylabel('UMAP space', color=INK_2)
    axes[1, 0].set_ylabel('Slide space', color=INK_2)
    fig.suptitle(f'{sample} — one cluster per panel (grey = all other tiles)',
                 x=0.02, ha='left', weight='semibold')
    fig.savefig(out / f'{sample}.03_cluster_small_multiples.png', bbox_inches='tight')
    plt.close(fig)

    # ---------------------------------------------------------------- fig 4
    # Continuous structure: PC1/PC2 as sequential magnitude, and cosine
    # similarity of each tile to the slide's mean tile embedding.
    mean_dir = X.mean(axis=0); mean_dir /= np.linalg.norm(mean_dir)
    cos_to_mean = X @ mean_dir
    layers = [
        ('PC1', Xp[:, 0], f'{pca.explained_variance_ratio_[0]:.1%} of variance', DIV),
        ('PC2', Xp[:, 1], f'{pca.explained_variance_ratio_[1]:.1%} of variance', DIV),
        ('Cosine to slide mean tile', cos_to_mean, 'typicality of each tile', SEQ),
    ]
    fig, axes = plt.subplots(2, len(layers), figsize=(4.0 * len(layers), 7.4),
                             gridspec_kw=dict(hspace=0.22))
    for j, (name, val, sub, cm) in enumerate(layers):
        lo, hi = np.percentile(val, [1, 99])
        if cm is DIV:
            lim = max(abs(lo), abs(hi)); lo, hi = -lim, lim
        ax = axes[0, j]
        sc = ax.scatter(emb[:, 0], emb[:, 1], s=2.2, c=val, cmap=cm, vmin=lo, vmax=hi,
                        linewidths=0)
        ax.set_title(f'{name}\n{sub}')
        bare(ax, keep_frame=True)
        ax = axes[1, j]
        ax.imshow(rasterise(coords, val, step), extent=extent, origin='upper',
                  cmap=cm, vmin=lo, vmax=hi, interpolation='nearest')
        bare(ax, keep_frame=True)
        cb = fig.colorbar(sc, ax=axes[:, j], orientation='horizontal', fraction=0.05,
                          pad=0.03, aspect=30)
        cb.outline.set_visible(False)
        cb.ax.tick_params(length=0, labelsize=8, colors=MUTED)
    axes[0, 0].set_ylabel('UMAP space', color=INK_2)
    axes[1, 0].set_ylabel('Slide space', color=INK_2)
    fig.suptitle(f'{sample} — continuous embedding structure, UMAP (top) vs slide (bottom)',
                 x=0.02, ha='left', weight='semibold')
    fig.savefig(out / f'{sample}.04_continuous.png', bbox_inches='tight')
    plt.close(fig)

    # ---------------------------------------------------------------- fig 5
    if args.slide_npz:
        z = np.load(args.slide_npz)
        vecs = [(k, np.asarray(z[k]).ravel()) for k in ('base', 'diagnostic') if k in z]
        fig, axes = plt.subplots(len(vecs) + 1, 1, figsize=(10, 2.1 * (len(vecs) + 1)),
                                 gridspec_kw=dict(hspace=0.75))
        for ax, (name, v) in zip(axes, vecs):
            w = 64
            pad = (-len(v)) % w
            img = np.concatenate([v, np.full(pad, np.nan)]).reshape(-1, w)
            lim = np.nanpercentile(np.abs(v), 99)
            im = ax.imshow(img, cmap=DIV, vmin=-lim, vmax=lim, aspect='auto',
                           interpolation='nearest')
            ax.set_title(f'PRISM2 {name} embedding — {v.size}-d '
                         f'(‖v‖={np.linalg.norm(v):.1f}, {w} values per row)')
            bare(ax, keep_frame=True)
            cb = fig.colorbar(im, ax=ax, fraction=0.02, pad=0.01)
            cb.outline.set_visible(False)
            cb.ax.tick_params(length=0, labelsize=8, colors=MUTED)
        ax = axes[-1]
        for i, (name, v) in enumerate(vecs):
            ax.hist(v, bins=120, histtype='step', lw=2, color=SERIES[i], label=f'{name} ({v.size}-d)')
        ax.legend(labelcolor=INK_2)
        ax.set_title('Value distribution')
        ax.set_xlabel('embedding value'); ax.set_ylabel('dimensions')
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
        ax.grid(axis='y', color=AXIS, lw=0.5, alpha=0.5); ax.set_axisbelow(True)
        fig.suptitle(f'{sample} — slide-level PRISM2 embeddings (n = 1 slide, so no UMAP)',
                     x=0.02, ha='left', weight='semibold')
        fig.savefig(out / f'{sample}.05_slide_embedding.png', bbox_inches='tight')
        plt.close(fig)

    # ---------------------------------------------------------------- fig 6
    # What each cluster actually looks like: the tiles closest to the cluster
    # centroid in embedding space, cropped from the source WSI.
    reps = {}
    for i in range(args.k):
        idx = np.flatnonzero(lab == i)
        c = X[idx].mean(axis=0); c /= np.linalg.norm(c)
        reps[i] = idx[np.argsort(-(X[idx] @ c))[:args.tiles_per_cluster]]

    if args.wsi:
        import openslide
        slide = openslide.OpenSlide(args.wsi)
        tiledir = out / f'{sample}.representative_tiles'
        m = args.tiles_per_cluster
        fig, axes = plt.subplots(args.k, m, figsize=(1.15 * m + 2.4, 1.15 * args.k),
                                 gridspec_kw=dict(hspace=0.06, wspace=0.06,
                                                  left=0.19, right=0.995,
                                                  top=0.93, bottom=0.005))
        axes = np.atleast_2d(axes)
        for i in range(args.k):
            cdir = tiledir / f'cluster_{i + 1:02d}'
            cdir.mkdir(parents=True, exist_ok=True)
            for r, t in enumerate(reps[i]):
                x, y = int(coords[t, 0]), int(coords[t, 1])
                img = slide.read_region((x, y), 0, (step, step)).convert('RGB')
                img.save(cdir / f'{sample}_rank{r + 1:02d}_x{x}_y{y}.png')
                ax = axes[i, r]
                ax.imshow(np.asarray(img))
                bare(ax, keep_frame=True)
                for sp in ax.spines.values():
                    sp.set_edgecolor(palette[i]); sp.set_linewidth(2)
            axes[i, 0].text(-0.08, 0.5, f'{cluster_names[i]}\n{counts[i]:,} tiles',
                            transform=axes[i, 0].transAxes, ha='right', va='center',
                            fontsize=8.5, color=INK_2)
        fig.suptitle(f'{sample} — {m} most representative tiles per cluster '
                     f'({step} px @ {meta.get("target_magnification", 20):g}x, '
                     f'nearest the cluster centroid)', x=0.01, ha='left', weight='semibold')
        fig.savefig(out / f'{sample}.06_representative_tiles.png', bbox_inches='tight')
        plt.close(fig)
        slide.close()
        print('wrote representative tile crops to', tiledir)

    # ---------------------------------------------------------------- data
    np.savez_compressed(
        out / f'{sample}.tile_projection.npz',
        coords=coords, umap=emb, pca=Xp[:, :10], cluster=lab,
        cos_to_mean=cos_to_mean, step=step)
    summary = {
        'sample': sample, 'n_tiles': int(n), 'tile_dim': int(d),
        'encoder': meta['encoder'], 'patch_size_level0': step,
        'pc_variance_ratio': [float(v) for v in pca.explained_variance_ratio_[:10]],
        'cluster_method': method,
        'clusters': {cluster_names[i]: int(counts[i]) for i in range(args.k)},
        'representative_tiles': {
            cluster_names[i]: [[int(coords[t, 0]), int(coords[t, 1])] for t in reps[i]]
            for i in range(args.k)},
    }
    if args.prism2_json:
        summary['prism2'] = json.loads(Path(args.prism2_json).read_text())
    (out / f'{sample}.viz_summary.json').write_text(json.dumps(summary, indent=2))
    print('wrote figures to', out)


if __name__ == '__main__':
    main()
