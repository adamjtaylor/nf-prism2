# Embedding visualisation for `nf-prism2-cmu1-gpu-7`

Tower run `1ZxV1cKkK3M7hP` (SUCCEEDED, 19 Aug 2026), commit `dfda462`, CMU-1 on A10G.

## Where the data came from

| Artefact | Source |
|---|---|
| `data/CMU-1.features.h5` | TRIDENT work dir `s3://mc2-project-tower-scratch/work/64/8f288726701ed9b3929f9274b22512/`. **Not published**: `TRIDENT_EMBED` only publishes `qc/*` |
| `data/outdir/` | `s3://mc2-project-tower-scratch/nf-prism2-cmu1-gpu/` |
| `data/CMU-1.svs` | https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/CMU-1.svs (for tile crops) |

The tile embeddings (6182 x 1280 Virchow2 class token) are the pipeline's most
reusable output but currently live only in the work dir. Publishing
`${meta.id}.features.h5` from `TRIDENT_EMBED` would make this reproducible from
`--outdir` alone.

## Reproduce

```bash
uv run python bin/visualize_embeddings.py \
  --features   analysis/data/CMU-1.features.h5 \
  --slide-npz  analysis/data/outdir/prism2/CMU-1/CMU-1.embeddings.npz \
  --thumb      analysis/data/outdir/tiles/CMU-1/qc/CMU-1.jpg \
  --prism2-json analysis/data/outdir/prism2/CMU-1/CMU-1.prism2.json \
  --wsi        analysis/data/CMU-1.svs \
  --outdir     analysis/figures
```

Method: L2-normalise the class tokens (cosine geometry), PCA to 50 components,
UMAP (`n_neighbors=30`, `min_dist=0.1`, cosine), then Leiden
(`RBConfigurationVertexPartition`, resolution 0.5) on UMAP's own fuzzy kNN graph,
the same connectivities scanpy clusters on, so partition and layout share one
neighbourhood structure. `--cluster kmeans --k N` switches back to k-means.

## Figures

| File | What it shows |
|---|---|
| `01_overview` | TRIDENT segmentation, tile grid in slide space, UMAP: one series each |
| `02_clusters_umap_spatial` | 9 clusters, same colours in UMAP and slide space, direct labels |
| `03_cluster_small_multiples` | one cluster per panel against grey context (the colourblind-safe version: past three categorical slots a scatter cannot stay separable) |
| `04_continuous` | PC1, PC2, cosine-to-slide-mean as sequential/diverging fields in both spaces |
| `05_slide_embedding` | PRISM2 `base` (2560-d) and `diagnostic` (3072-d); n = 1 slide, so no UMAP |
| `06_representative_tiles` | 8 tiles nearest each cluster centroid, cropped at 224 px / 20x |
| `representative_tiles/cluster_NN/` | the same crops as individual PNGs |
| `CMU-1.tile_projection.npz` | coords, UMAP, PC1-10, cluster labels, cosine-to-mean |

Slide-level UMAP needs a cohort (roughly n >= 20 slides); with one slide the
`base`/`diagnostic` vectors are shown as value fields only.

See [`EMBEDDING_VISUALISATION.md`](EMBEDDING_VISUALISATION.md) for what the
figures show and what the clusters turn out to be.
