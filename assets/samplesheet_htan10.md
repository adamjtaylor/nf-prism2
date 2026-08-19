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
* **Nothing here has a validated ground-truth label yet.** The diagnosis column comes from HTAN
  clinical metadata at the participant or specimen level, not from a per-slide expert read, so
  these are for smoke-testing the pipeline and for eyeballing plausibility, not for computing
  accuracy.

## Deliberately excluded

* `HTAN TNP SARDANA` OME-TIFF (`syn25074523`), 13 GB. Fine later, too slow for a first run.
* `HTAN BU` **qptiff** (Akoya, e.g. `syn61624639`). Not an OpenSlide format. Worth a separate
  reader test with TRIDENT's `--reader_type image`.
* `HTAN Stanford` and `HTAN Vanderbilt`, whose organ and diagnosis fields are `Not Reported`.
