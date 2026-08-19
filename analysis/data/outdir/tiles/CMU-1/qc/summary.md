# TRIDENT job summary

This file is updated once per run and summarizes what TRIDENT has done in this `job_dir`.

- Per-slide machine-readable state lives in `wsi_states/*.json`.
- Per-run manifests live in `runs/*.json`.

## Run 2026-08-19T12:39:53+0000 (trident 0.3.2) — run_id=23223b8d7f37
- Tool: `run_batch_of_slides`
- Status: **completed**
- Finished: `2026-08-19T12:40:30+0000`
- Slides with state: 1
- Args: `{"batch_size": 32, "cache_batch_size": 32, "clear_dead_locks": false, "coords_dir": null, "custom_list_of_wsis": "slide_list.csv", "custom_mpp_keys": null, "dead_lock_max_age_hours": 24.0, "device": "cuda:0", "dump_patches": false, "dump_patches_format": "png", "dump_patches_jpeg_quality": 90, "dump_patches_max": 0, "feat_batch_size": null, "gpu": 0, "job_dir": "trident", "mag": 20.0, "max_workers": null, "min_tissue_proportion": 0.0, "overlap": 0, "patch_encoder": "virchow2-cls", "patch_encoder_ckpt_path": null, "patch_encoder_img_size": null, "patch_size": 224, "reader_type": null, "remove_artifacts": false, "remove_holes": false, "remove_penmarks": false, "search_nested": false, "seg_batch_size": null, "seg_conf_thresh": 0.5, "segmenter": "otsu", "skip_errors": false, "slide_encoder": null, "task": "all", "wsi_cache": null, "wsi_dir": "wsi", "wsi_ext": null}`
- coords: completed: 1
- segmentation: completed: 1
- Patch features:
  - virchow2-cls: completed: 1
