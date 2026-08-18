#!/usr/bin/env python3
"""Run PRISM2 slide-level inference on Virchow2 class-token tile embeddings.

Input : HDF5 of tile embeddings from TRIDENT `--patch_encoder virchow2-cls`, shape (N, 1280).
Output: JSON with yes/no scores, open-ended answers, multiple-choice answers and a
        generated report; optionally an .npz with the base (2560-d) and diagnostic
        (3072-d) slide embeddings.

PRISM2 (paige-ai/Prism2) is CC-BY-NC-ND 4.0: non-commercial research only, not for
clinical or diagnostic use.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import h5py
import numpy as np
import torch
import yaml
from transformers import AutoModel, AutoProcessor

MODEL_ID = "paige-ai/Prism2"
VIRCHOW2_CLS_DIM = 1280  # PRISM2 takes the class token only, NOT the 2560-d class+mean concat

log = logging.getLogger("prism2_infer")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sample", required=True)
    p.add_argument("--features", required=True, type=Path, help="TRIDENT virchow2-cls features .h5")
    p.add_argument("--questions", required=True, type=Path, help="questions YAML")
    p.add_argument("--out-json", required=True, type=Path)
    p.add_argument("--out-report", required=True, type=Path)
    p.add_argument("--out-npz", required=True, type=Path)
    p.add_argument("--max-tiles", type=int, default=50000)
    p.add_argument("--max-new-tokens", type=int, default=100)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--save-embeddings", action="store_true")
    p.add_argument("--model-id", default=MODEL_ID)
    return p.parse_args()


# --------------------------------------------------------------------------- inputs


def load_tile_embeddings(path: Path, max_tiles: int, seed: int) -> tuple[torch.Tensor, int]:
    """Read (N, 1280) tile embeddings, seeded-subsampling down to max_tiles."""
    with h5py.File(path, "r") as h5:
        keys = list(h5.keys())
        if "features" in keys:
            key = "features"
        else:
            # TRIDENT names this 'features'; fall back to the only 2-D dataset if that changes
            candidates = [k for k in keys if getattr(h5[k], "ndim", 0) == 2]
            if len(candidates) != 1:
                raise ValueError(
                    f"{path}: cannot identify the feature dataset. Datasets present: {keys}"
                )
            key = candidates[0]
            log.warning("No 'features' dataset in %s; using '%s'", path, key)
        feats = np.asarray(h5[key][:], dtype=np.float32)

    if feats.ndim != 2:
        raise ValueError(f"{path}: expected a 2-D feature matrix, got shape {feats.shape}")
    if feats.shape[1] != VIRCHOW2_CLS_DIM:
        raise ValueError(
            f"{path}: tile embeddings are {feats.shape[1]}-d but PRISM2 requires "
            f"{VIRCHOW2_CLS_DIM}-d Virchow2 class-token embeddings. "
            "Re-run TRIDENT with `--patch_encoder virchow2-cls` (plain `virchow2` gives the "
            "2560-d class+mean concat, which this model does not accept)."
        )
    if feats.shape[0] == 0:
        raise ValueError(f"{path}: zero tiles - tissue segmentation probably found no foreground")

    n_total = feats.shape[0]
    if max_tiles > 0 and n_total > max_tiles:
        rng = np.random.default_rng(seed)
        idx = np.sort(rng.choice(n_total, size=max_tiles, replace=False))
        feats = feats[idx]
        log.info("Subsampled %d/%d tiles (seed=%d)", max_tiles, n_total, seed)

    return torch.from_numpy(feats), n_total


def load_questions(path: Path) -> dict:
    spec = yaml.safe_load(path.read_text()) or {}
    if not isinstance(spec, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")

    seen: set[str] = set()
    for block in ("yes_no", "open_ended", "multiple_choice"):
        entries = spec.get(block) or []
        if not isinstance(entries, list):
            raise ValueError(f"{path}: '{block}' must be a list")
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or "id" not in entry:
                raise ValueError(f"{path}: {block}[{i}] needs an 'id'")
            text_key = "question" if block == "yes_no" else "prompt"
            if not entry.get(text_key):
                raise ValueError(f"{path}: {block}[{i}] ('{entry['id']}') needs a '{text_key}'")
            if entry["id"] in seen:
                raise ValueError(f"{path}: duplicate question id '{entry['id']}'")
            seen.add(entry["id"])

    if not seen and not spec.get("report"):
        raise ValueError(f"{path}: no questions and no report prompt - nothing to do")
    return spec


# --------------------------------------------------------------------- model helpers


def unwrap(value):
    """PRISM2 returns batched results; we always run a batch of one."""
    if isinstance(value, torch.Tensor):
        value = value.detach().float().cpu()
        return value.reshape(-1)[0].item() if value.numel() == 1 else value.tolist()
    if isinstance(value, (list, tuple)):
        return unwrap(value[0]) if len(value) == 1 else [unwrap(v) for v in value]
    return value


def respond(model, batch, prompt: str, max_new_tokens: int) -> str:
    with torch.inference_mode(), torch.autocast("cuda", torch.bfloat16):
        out = model.get_response(**batch, prompt=prompt, max_new_tokens=max_new_tokens)
    text = unwrap(out)
    return text.strip() if isinstance(text, str) else str(text)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    if not torch.cuda.is_available():
        log.error("PRISM2 requires a CUDA GPU (Perceiver + Phi-3-mini, 4.4B params in bf16).")
        return 1

    torch.manual_seed(args.seed)

    spec = load_questions(args.questions)
    tiles, n_total = load_tile_embeddings(args.features, args.max_tiles, args.seed)
    log.info("%s: %d tile embeddings of dim %d", args.sample, tiles.shape[0], tiles.shape[1])

    model = (
        AutoModel.from_pretrained(args.model_id, trust_remote_code=True, torch_dtype="auto")
        .cuda()
        .eval()
    )
    processor = AutoProcessor.from_pretrained(args.model_id, trust_remote_code=True)
    batch = processor(tile_embeddings=[tiles]).to("cuda")

    result = {
        "sample": args.sample,
        "model_id": args.model_id,
        "n_tiles_total": int(n_total),
        "n_tiles_used": int(tiles.shape[0]),
        "tile_embedding_dim": int(tiles.shape[1]),
        "yes_no": {},
        "open_ended": {},
        "multiple_choice": {},
        "report": "",
    }

    # --- slide representations ---------------------------------------------
    with torch.inference_mode(), torch.autocast("cuda", torch.bfloat16):
        base = model.get_base_embedding(**batch)
        diag = model.get_diagnostic_embedding(**batch)
    result["base_embedding_dim"] = int(base.shape[-1])
    result["diagnostic_embedding_dim"] = int(diag.shape[-1])
    log.info("base=%s diagnostic=%s", tuple(base.shape), tuple(diag.shape))

    if args.save_embeddings:
        np.savez_compressed(
            args.out_npz,
            sample=np.array(args.sample),
            base=base.detach().float().cpu().numpy(),
            diagnostic=diag.detach().float().cpu().numpy(),
        )

    # --- zero-shot yes/no scoring ------------------------------------------
    for entry in spec.get("yes_no") or []:
        with torch.inference_mode(), torch.autocast("cuda", torch.bfloat16):
            score = model.yes_no_score(
                tile_embeddings=batch["tile_embeddings"],
                attention_mask=batch["attention_mask"],
                question=entry["question"],
            )
        result["yes_no"][entry["id"]] = {
            "question": entry["question"],
            "score": float(unwrap(score)),
        }
        log.info("yes_no %-24s %.4f", entry["id"], result["yes_no"][entry["id"]]["score"])

    # --- open-ended and multiple-choice ------------------------------------
    for block in ("open_ended", "multiple_choice"):
        for entry in spec.get(block) or []:
            answer = respond(
                model, batch, entry["prompt"], int(entry.get("max_new_tokens", args.max_new_tokens))
            )
            result[block][entry["id"]] = {"prompt": entry["prompt"], "answer": answer}
            log.info("%s %s -> %s", block, entry["id"], answer[:120].replace("\n", " "))

    # --- report -------------------------------------------------------------
    report_spec = spec.get("report")
    if report_spec:
        if isinstance(report_spec, str):
            report_spec = {"prompt": report_spec}
        prompt = report_spec.get("prompt", "Write a report")
        result["report_prompt"] = prompt
        result["report"] = respond(
            model, batch, prompt, int(report_spec.get("max_new_tokens", 300))
        )

    args.out_json.write_text(json.dumps(result, indent=2) + "\n")
    args.out_report.write_text((result["report"] or "") + "\n")
    log.info("Wrote %s", args.out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
