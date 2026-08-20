#!/usr/bin/env python3
"""Exact cosine top-k with group exclusions, chunked so it fits in memory.

Everything here is EXACT. The approximate-index question is answered separately in the DuckDB
prototype, where recall against these exact answers is the thing being measured, so the reference
must not itself be approximate.

Cosine similarity on L2-normalised vectors is a dot product, so top-k is a chunked matmul plus an
argpartition. Queries are processed in blocks that share the same exclusion mask (all queries from
one slide, or one patient), which lets the mask be applied to whole columns once per block instead
of per query.
"""
import numpy as np


def l2(A):
    A = np.asarray(A, dtype=np.float32)
    return A / np.maximum(np.linalg.norm(A, axis=1, keepdims=True), 1e-12)


def topk_blocks(Xq, Xi, k, blocks, chunk=512):
    """Top-k index-space neighbours for each query.

    Xq      (Q, D) L2-normalised queries
    Xi      (N, D) L2-normalised index
    blocks  list of (query_rows, excluded_index_cols) — query_rows are positions in Xq,
            excluded_index_cols are positions in Xi to mask out for those queries
    returns (Q, k) int32 index positions and (Q, k) float32 similarities, ordered best first
    """
    Q = Xq.shape[0]
    I = np.full((Q, k), -1, dtype=np.int32)
    S = np.full((Q, k), -np.inf, dtype=np.float32)
    for qrows, excl in blocks:
        qrows = np.asarray(qrows)
        for a in range(0, len(qrows), chunk):
            sel = qrows[a:a + chunk]
            sim = Xq[sel] @ Xi.T                      # (c, N)
            if excl is not None and len(excl):
                sim[:, excl] = -np.inf
            kk = min(k, sim.shape[1])
            part = np.argpartition(-sim, kk - 1, axis=1)[:, :kk]
            vals = np.take_along_axis(sim, part, axis=1)
            order = np.argsort(-vals, axis=1)
            I[sel, :kk] = np.take_along_axis(part, order, axis=1)
            S[sel, :kk] = np.take_along_axis(vals, order, axis=1)
            del sim
    return I, S


def blocks_by_group(group_of_query, group_of_index, policy):
    """Build the (query_rows, excluded_cols) blocks for an exclusion policy.

    policy 'none'   -> nothing excluded except the query's own row, handled by the caller
           'group'  -> every index row sharing the query's group value is excluded
    """
    groups = {}
    for i, g in enumerate(group_of_query):
        groups.setdefault(g, []).append(i)
    out = []
    for g, rows in groups.items():
        excl = np.where(group_of_index == g)[0] if policy == "group" else None
        out.append((np.array(rows), excl))
    return out
