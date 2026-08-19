# Visualising the CMU-1 embeddings from `nf-prism2-cmu1-gpu-7`

Tower run `1ZxV1cKkK3M7hP`: SUCCEEDED, 19 Aug 2026, pipeline commit `dfda462`,
A10G on `manual-shared-ce-prod-project-ondemand-v13`. One slide (CMU-1), the
`questions_test.yaml` question set, bf16 scoring.

The question this answers: **what do the two embedding layers PRISM2 sits on top of
actually look like, in UMAP space and on the slide?**

---

## What the run produced, and what it kept

nf-prism2 has two embedding layers:

| Layer | Shape | Produced by | Published? |
|---|---|---|---|
| Tile embeddings | 6182 x 1280 | `TRIDENT_EMBED` (Virchow2 class token, 224 px @ 20x) | **No** |
| Slide embeddings | `base` 1 x 2560, `diagnostic` 1 x 3072 | `PRISM2_INFER` | Yes, `prism2/CMU-1/CMU-1.embeddings.npz` |

`TRIDENT_EMBED` declares `publishDir ... pattern: 'qc/*'`, so
`CMU-1.features.h5`, the 32 MB file holding every tile embedding and the most
reusable artefact the pipeline generates, exists only in the task work
directory:

```
s3://mc2-project-tower-scratch/work/64/8f288726701ed9b3929f9274b22512/CMU-1.features.h5
```

Everything below was produced from that file. It survives only as long as the
Tower scratch work dir does. **Adding `${meta.id}.features.h5` to the publish
pattern in `modules/local/trident_embed/main.nf` would make this analysis
reproducible from `--outdir` alone**, worth doing before the next run, since
downstream work (clustering, spatial maps, retraining an aggregator, any
tile-level QC) needs the tile embeddings and nothing else in the pipeline
regenerates them cheaply.

---

## Method

L2-normalise the class tokens first. Virchow2 embeddings are compared by cosine
similarity, so any Euclidean method (PCA, k-means, UMAP's own metric fallback)
needs unit vectors to behave.

1. PCA to 50 components. PC1-3 carry 18.4% / 11.0% / 8.8%; PC1-10 carry 58.6%.
2. UMAP on those components: `n_neighbors=30`, `min_dist=0.1`, cosine.
3. **Leiden** (`RBConfigurationVertexPartition`, resolution 0.5) on UMAP's own
   fuzzy kNN graph, the same connectivities scanpy clusters on, so the partition
   and the layout come from one neighbourhood structure rather than two
   independent ones. 12 clusters, modularity 0.754.
4. The 4 smallest clusters fold into "Other" rather than inventing hues past the
   categorical palette, leaving 9 rendered groups.

`--cluster kmeans --k N` switches back to k-means on the same PCA if a fixed
cluster count is wanted for comparison across slides.

## Figures

All in `figures/`, prefixed `CMU-1.`:

| File | What it shows |
|---|---|
| `01_overview.png` | TRIDENT segmentation, the tile grid in slide space, the bare UMAP: one series each, no colour encoding yet |
| `02_clusters_umap_spatial.png` | the 9 clusters, same colours in UMAP and in slide space, direct-labelled at each cluster's densest point |
| `03_cluster_small_multiples.png` | one cluster per panel against grey context, in both spaces |
| `04_continuous.png` | PC1, PC2 and cosine-to-slide-mean as continuous fields, UMAP above, slide below |
| `05_slide_embedding.png` | the PRISM2 `base` and `diagnostic` vectors as value fields |
| `06_representative_tiles.png` | the 8 tiles nearest each cluster centroid, cropped at 224 px / 20x |
| `representative_tiles/cluster_NN/` | those crops as individual PNGs, named `CMU-1_rankNN_x<X>_y<Y>.png` |
| `CMU-1.tile_projection.npz` | coords, UMAP coords, PC1-10, cluster labels, cosine-to-mean, for reuse without recomputing |
| `CMU-1.viz_summary.json` | cluster sizes, PC variance ratios, representative tile coordinates, the run's PRISM2 output |

`03` exists because of a hard constraint rather than for completeness: on a
scatter plot, where every pair of colours can end up adjacent, only three
categorical hues stay separable under simulated colour-vision deficiency. Past
three the honest presentation is one cluster per panel. `02` is the readable
summary and carries direct labels so identity never rests on colour alone; `03`
is the version that holds up.

---

## What the clusters are

The centroid crops in `06` make the partition legible without a pathologist:

| Cluster | Tiles | Morphology in the centroid crops |
|---|---|---|
| 1 | 975 (16%) | loose spindled dermal stroma, scattered nuclei |
| 2 | 919 (15%) | more cellular spindled stroma, occasional clear spaces |
| 3 | 893 (14%) | glandular / adnexal epithelium, crisp basophilic epithelial lining against pink stroma |
| 4 | 603 (10%) | fibrous stroma with wispy collagen and small vessels |
| 5 | 594 (10%) | dense sheets of uniform round nuclei, the nested tumour compartment |
| 6 | 582 (9%) | epidermis with overlying keratin, the surface layer |
| 7 | 515 (8%) | dense eosinophilic collagen, nearly acellular |
| 8 | 421 (7%) | tumour nests interleaved with collagen, a boundary morphology |
| Other | 680 (11%) | background glass, section-edge debris, pen/ink fragments |

Two things follow from this.

**The embeddings carry morphology, not just stain intensity.** Cluster 6
(epidermis + keratin) and cluster 3 (glandular epithelium) are both densely
basophilic and would collapse together under any colour-based feature, but they
occupy opposite regions of the UMAP. Cluster 8 sitting between 5 (pure tumour)
and 7 (pure collagen) in both spaces is the behaviour you want from a
representation: a mixed tile lands between its pure endpoints.

**The spatial map is concentric, and that is the strongest validity signal here.**
CMU-1 has four tissue fragments. In `02` each one shows the same layered
structure from the outside in: epidermis/keratin (6) at the rim, collagen (7)
and fibrous stroma (4) beneath it, then stroma (1, 2), with tumour (5, 8) in the
fragment interiors. Nothing in the pipeline knows a tile's neighbours, since TRIDENT
embeds each 224 px tile independently, so this ordering is recovered purely from
appearance. Tissue architecture reappearing in a spatially blind representation
is the check worth keeping when this runs on real cohorts.

`04` shows the same thing continuously. PC1 (18.4%) is essentially
tumour-versus-rim: negative across the fragment margins, positive through the
interiors. The cosine-to-slide-mean panel is close to a "typicality" map: the
fragment interiors are typical of the slide, the rims and the debris are not,
which is also a usable tile-level QC signal.

The "Other" group is the one to watch operationally. 680 tiles (11%) of glass,
edge debris and ink passed `--segmenter otsu` at `--seg_conf_thresh 0.5` and were
embedded and fed to PRISM2. On this slide it is 11% wasted compute and a small
amount of noise in the aggregation; on a cohort with pen marks or scanner
artefacts it would be worth comparing `--segmenter hest` or enabling
`--remove_penmarks` / `--remove_artifacts`.

## The slide embeddings

`05` shows `base` (2560-d, ||v|| = 50.5) and `diagnostic` (3072-d, ||v|| = 89.4)
as value fields with their distributions. Both are roughly zero-centred and
unimodal; `diagnostic` has heavier tails.

**There is no UMAP of the slide embeddings, and there cannot be one from this
run.** UMAP needs a population; n = 1 slide is a single point. A slide-level
UMAP, the plot that would show whether PRISM2 separates diagnoses, needs
roughly 20+ slides through the pipeline, and that is the next thing to run if
slide-level structure is the question. The tile UMAP is the only embedding
manifold this run can support.

Nor can the slide embedding be projected into the tile UMAP: the tile space is
1280-d Virchow2 and the slide space is 2560-d/3072-d PRISM2 output. They are not
the same space and the two plots are not comparable point-for-point.

## An inconsistency in the run's own answers

Worth flagging separately from the visualisation, because the crops bear on it.
The run's `CMU-1.prism2.json` reports:

- `invasive_carcinoma` score **0.018** (essentially "no")
- `cancer_type` open-ended answer: **"This is a melanoma."**
- `report`: **"The examination reveals a compound nevus."**

Melanoma and compound nevus are different diagnoses, and a melanoma would not
score 0.018 on invasive carcinoma. That said, "invasive carcinoma" is arguably the
wrong question for a melanocytic lesion, since melanoma is not a carcinoma, so
the low score may be correct and the question simply inapplicable. Cluster 5's
crops (uniform round nuclei in dense nests, no obvious gland formation) are
consistent with a melanocytic lesion but do not settle nevus versus melanoma
from morphology at 20x alone.

This is `questions_test.yaml` on a single public test slide with `max_new_tokens
32` and bf16 scoring, so it is a smoke-test artefact rather than a finding. It
does say the question set needs to match the tissue before any of these scores
mean anything, and it is a reason to re-run the scoring in fp32
(`--scoring_dtype fp32`) before treating any yes/no score as calibrated.

## Reproducing

Requires `openslide` (brew) plus `openslide-bin`, and the source WSI for the tile
crops. Everything else comes from the run.

```bash
uv run python bin/visualize_embeddings.py \
  --features    analysis/data/CMU-1.features.h5 \
  --slide-npz   analysis/data/outdir/prism2/CMU-1/CMU-1.embeddings.npz \
  --thumb       analysis/data/outdir/tiles/CMU-1/qc/CMU-1.jpg \
  --prism2-json analysis/data/outdir/prism2/CMU-1/CMU-1.prism2.json \
  --wsi         analysis/data/CMU-1.svs \
  --outdir      analysis/figures
```

Inputs were staged as recorded in `README.md`. `analysis/data/` is gitignored:
the SVS is 169 MB and the feature h5 is 32 MB.

Cost context from the run's own trace: `TRIDENT_EMBED` 12m 49s wall (56s
compute), `PRISM2_INFER` 11m 48s wall (2m 26s compute). The wall-clock is
dominated by Batch scheduling and model staging, not inference.
