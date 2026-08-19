# HTAN 10-slide pilot set

Provenance for `samplesheet_htan10.csv`. Selected from HTAN **Data Release 7.0** (portal
`htan_2026_914`, `assayName='H&E'`, `level='Level 2'`, 2,474 files across 10 atlases), choosing
one slide per centre / cancer type / file format combination to cover the widest spread at the
smallest download.

| sample | Synapse | Centre | Organ | Diagnosis | Real format | Size | Pixels | MPP source |
|---|---|---|---|---|---|---|---|---|
| BU_lung_adenocarcinoma_svs | syn53640639 | HTAN BU | Lung | Adenocarcinoma NOS | svs | 1.07 GB | 81280 x 40210 @ 0.50 | file |
| BU_lung_squamous_ndpi | syn68493420 | HTAN BU | Lung | Squamous cell carcinoma NOS | **ndpi** (Hamamatsu) | 0.02 GB | metadata blank | file |
| DUKE_breast_dcis_svs | syn52289971 | HTAN Duke | Breast | Ductal carcinoma in situ | svs | 0.45 GB | 11264 x 11264 @ 0.161 | file |
| DUKE_breast_dcis_tiff | syn27056837 | HTAN Duke | Breast | Ductal carcinoma in situ | **tiff** (generic) | 0.22 GB | 11264 x 11264 @ 0.161 | samplesheet |
| HMS_ovarian_hgserous_svs | syn68629518 | HTAN HMS | Other/ill-defined | High-grade serous carcinoma | svs | 1.22 GB | 86016 x 204800 @ 0.137 | file |
| HMS_skin_melanoma_ometiff | syn51758553 | HTAN HMS | Skin | Malignant melanoma NOS | **ome.tif** | 0.03 GB | 5726 x 14865 @ 0.347 | samplesheet |
| HMS_colorectal_adenocarcinoma_ometiff | syn51692280 | HTAN HMS | Colorectal | Adenocarcinoma NOS | **ome.tif** (Orion) | 0.82 GB | 71708 x 49993 @ 0.325 | samplesheet |
| SRRS_lung_squamous_svs | syn52431239 | HTAN SRRS | Lung | Squamous cell carcinoma NOS | svs | not in BQ | not in BQ | file |
| WUSTL_pancreas_pbcarcinoma_svs | syn27393120 | HTAN WUSTL | Pancreas | Pancreatobiliary-type carcinoma | svs (**spaces in filename**) | 0.09 GB | 17928 x 26806 @ 0.505 | file |
| HTAPP_breast_lobular_svs | syn25882267 | HTAN HTAPP | Breast | Lobular carcinoma NOS | svs | 0.06 GB | 8377 x 10098 @ 0.276 | file |

Total download about 4 GB. Six centres, six organs, eight diagnoses, four container formats.

## Why these, and what each one tests

* **Format coverage is the point.** OpenSlide reads `svs` and `ndpi` natively. Generic `tiff` and
  `ome.tif` only work if the file is tiled and pyramidal, and they usually carry no MPP that
  OpenSlide can find, which is why those three rows set `mpp` explicitly from
  `imaging_level2_metadata_current.PhysicalSizeX`.
* **The portal's `FileFormat` column is unreliable.** `syn68493420` is registered as `svs` but is
  actually an NDPI, and several SRRS rows registered as `svs` are `.ome.tiff`. Always check the
  filename extension, not the metadata field.
* **`syn68493420` has junk pixel metadata** in BigQuery (`PhysicalSizeX = 16384.0`, `SizeX = 0`).
  Left blank deliberately so OpenSlide reads the real value from the file. Do not copy BigQuery
  MPP values blindly.
* **`syn27393120` has spaces in its filename** ("HT 168 P1 S1H3 Vp1 L1 L4 U10.svs"). Kept on
  purpose: HTAN filenames do contain spaces and the pipeline has to survive them.
* **`syn68629518` is 86016 x 204800 at 0.137 MPP**, about 17.6 gigapixels, which resamples to
  roughly 26,000 tiles at 20x. It is the throughput stress case.
## What the first run actually showed (2026-08-19, run nf-prism2-htan10-v2)

Two of the ten failed on read, and both were format problems rather than pipeline bugs. This is
what the format-diverse selection was for.

| sample | Outcome | Cause | Fix applied |
|---|---|---|---|
| `DUKE_breast_dcis_tiff` | failed, exit 1 | `Failed to initialize WSI with OpenSlide: Unsupported or missing image file`. The plain `.tiff` is not an OpenSlide-readable container. | `reader` column set to `image` for this row |
| `HTAPP_breast_lobular_svs` | failed, exit 1 | `Unable to extract MPP from slide metadata`. An **svs with no MPP tag**, so the assumption that Aperio files always carry it natively is wrong for cleaned or converted files. | `mpp` set to 0.2762 from `PhysicalSizeX` |

Both were dropped rather than killing the run, and appeared in `failed_samples.txt`.

Measured `TRIDENT_EMBED` wall clock on `p4d.24xlarge`, 8 slides packed per instance, with the
split model store in place: 143s (ndpi), 428s (ome.tif melanoma), 433s (ovarian svs), 473s
(Duke svs). The previous run, staging a single 47 GB cache per task, had not finished any tile
task by 2000s. Staging was the bottleneck, not tiling.

**Lesson for the cohort run:** do not assume a container format implies readable, and do not
assume svs implies MPP. Both need to be checked per file, and the samplesheet carries a
`reader` column plus an `mpp` override for exactly this.

## Clinical metadata available for probing

Pulled from the portal `diagnosis` table (HTAN Release 7.0). This is what the question set in
`questions_htan10.yaml` is written against, with expected answers in `htan10_ground_truth.csv`.

| sample | Participant | Diagnosis | Site | Grade | Stage | LVI | PNI | Other |
|---|---|---|---|---|---|---|---|---|
| BU_lung_adenocarcinoma_svs | HTA3_80014 | Adenocarcinoma NOS | Lung, middle lobe | G1 | IA1 | no | | vascular invasion no |
| BU_lung_squamous_ndpi | HTA3_70153 | Squamous cell carcinoma NOS | Lung, trachea | | | | | primary tumour |
| DUKE_breast_dcis_svs | HTA6_2411 | Ductal carcinoma in situ | Breast | G3 | | | | |
| DUKE_breast_dcis_tiff | HTA6_1020 | Ductal carcinoma in situ | Breast | Intermediate | | | | |
| HMS_ovarian_hgserous_svs | HTA7_1258 | High-grade serous carcinoma | Fallopian tube | | IC | | | |
| HMS_skin_melanoma_ometiff | HTA7_6 | Malignant melanoma NOS | Skin, scalp and neck | | | no | no | Breslow 1.2 mm |
| HMS_colorectal_adenocarcinoma_ometiff | HTA7_989 | Adenocarcinoma NOS | Transverse colon | Low Grade | IVC | yes | yes | |
| SRRS_lung_squamous_svs | HTA15_30001 | Squamous cell carcinoma NOS | Lung | | | | | file-level label only |
| WUSTL_pancreas_pbcarcinoma_svs | HTA12_16 | Pancreatobiliary-type carcinoma | Pancreas | G1 | | yes | yes | vascular invasion yes |
| HTAPP_breast_lobular_svs | HTA1_880 | Lobular carcinoma NOS | Breast | G2 | | | | |

What this supports, and what it does not:

* **Usable splits.** Invasive vs in situ is 8 yes / 2 no. Melanoma is 1 yes / 9 no, which is a
  specificity check. Lymphovascular invasion is 2 yes / 2 no, perineural invasion 2 yes / 1 no,
  high grade 2 yes / 4 no. Primary site and histologic type are scoreable on all ten.
* **These are CASE-level labels, not slide-level.** A DCIS case can have sections showing only
  benign tissue, and a case recorded with lymphovascular invasion may not show it in this
  section. So a disagreement is not necessarily a model error.
* **Two grade vocabularies.** Some cases use G1/G2/G3, others Low/Intermediate/High. The mapping
  is recorded in the ground truth file rather than assumed.
* **`HTA15_30001` has no record in either the portal `diagnosis` table or
  `clinical_tier1_diagnosis_current`.** Its label comes from the file-level `PrimaryDiagnosis`
  array only, so it is the weakest label in the set.
* **Consequence for metrics.** Use these for plausibility screening and for catching gross
  failures such as a model answering yes to everything. Do not publish an AUC from ten
  case-level labels, and note that bf16 scoring quantisation makes ties likely at this scale
  anyway (see `BENCHMARK_PLAN.md` 5.3).

## Deliberately excluded

* `HTAN TNP SARDANA` OME-TIFF (`syn25074523`), 13 GB. Fine later, too slow for a first run.
* `HTAN BU` **qptiff** (Akoya, e.g. `syn61624639`). Not an OpenSlide format. Worth a separate
  reader test with TRIDENT's `--reader_type image`.
* `HTAN Stanford` and `HTAN Vanderbilt`, whose organ and diagnosis fields are `Not Reported`.
