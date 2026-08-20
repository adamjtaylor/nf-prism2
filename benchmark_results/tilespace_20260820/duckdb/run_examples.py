#!/usr/bin/env python3
"""Execute every query in queries.sql against the prototype and record the actual output.

A query file nobody has run is a guess. This substitutes a real query tile and a real query slide,
runs each statement, times it, and writes the result tables to example_queries.md so the SQL and
its output can be read together.

The example is chosen deterministically rather than picked to look good: the Arm A carcinoma
in situ slide with the median tile count, and the tile of that slide closest to its own centroid,
i.e. the most typical tile of a typical slide of the class the ladder detects best.
"""
import os, re, sys, time
import numpy as np
import duckdb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as C

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(C.REPO, "analysis", "data", "tilesearch.duckdb")

con = duckdb.connect(DB, read_only=True)
con.execute("LOAD vss; SET hnsw_enable_experimental_persistence = true;")

slide = con.execute("""
    SELECT sample FROM slides
    WHERE arm = 'A' AND ttt = 'Premalignant - in situ'
    ORDER BY abs(n_tiles - (SELECT median(n_tiles) FROM slides
                            WHERE arm = 'A' AND ttt = 'Premalignant - in situ'))
    LIMIT 1""").fetchone()[0]
# `slides.meanpool` already is the slide's tile centroid, so the most typical tile is the one
# closest to it. Ordering on `emb` rather than `emb_n` keeps the index out of the way; the two
# give the same ranking because cosine ignores magnitude.
tile = con.execute("""
    SELECT t.tile_id FROM tiles t JOIN slides s USING (sample)
    WHERE t.sample = ?
    ORDER BY array_cosine_distance(t.emb, s.meanpool)
    LIMIT 1""", [slide]).fetchone()[0]
meta = con.execute("SELECT arm, patient, centre, ttt, organ, n_tiles FROM slides WHERE sample = ?",
                   [slide]).fetchone()
print(f"query slide {slide} {meta}, query tile {tile}")

sql_text = open(os.path.join(HERE, "queries.sql")).read()

def parse(text):
    """Split queries.sql into (title, note, sql) triples.

    Line-based, not a split on semicolons, because the file's header comment contains semicolons of
    its own (`LOAD vss;`). Each query is fenced by rule lines of dashes: the rule that FOLLOWS a
    comment closes that comment, and the next comment line after a closed comment starts a fresh
    one. Comment lines inside the SQL stay in the SQL, because they explain the clause they sit
    next to.
    """
    out, comment, sql, closed = [], [], [], False
    for line in text.splitlines():
        t = line.strip()
        if not sql and (t.startswith("--") or not t):
            c = t.lstrip("-").strip()
            if not c or set(c) <= {"=", "-"}:
                if comment:
                    closed = True         # the closing rule of a comment block
            elif closed:
                comment, closed = [c], False   # first line of the next block
            else:
                comment.append(c)
            continue
        sql.append(line)
        if t.endswith(";"):
            out.append((comment[0] if comment else "", " ".join(comment[1:]),
                        "\n".join(sql).strip().rstrip(";")))
            comment, sql, closed = [], [], False
    return out

blocks = parse(sql_text)
print(f"{len(blocks)} statements parsed")

def cell(v):
    # DuckDB's round() returns a FLOAT, which Python then prints to full binary precision, so
    # 0.9407 arrives as 0.9406999945640564. Formatting here keeps the tables readable.
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return str(v)

def fmt(cols, rows, maxrows=10):
    if not rows:
        return "_no rows returned_\n"
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows[:maxrows]:
        out.append("| " + " | ".join(cell(v) for v in r) + " |")
    return "\n".join(out) + "\n"

md = ["# Example query output",
      "",
      f"Every statement in `duckdb/queries.sql`, run against the {con.execute('SELECT count(*) FROM tiles').fetchone()[0]:,}-tile "
      f"prototype. Each statement is run twice and the second run timed, on a laptop, so read the timings as orders of magnitude.",
      "",
      f"**Query slide** `{slide}`: Arm {meta[0]}, patient `{meta[1]}`, {meta[2]}, {meta[3]}, "
      f"{meta[4]}, {meta[5]:,} tiles. Chosen as the Arm A carcinoma in situ slide with the median "
      f"tile count.",
      f"**Query tile** `{tile}`: the tile of that slide closest to the slide's own centroid.",
      ""]
qvec = con.execute("SELECT emb_n FROM tiles WHERE tile_id = ?", [tile]).fetchone()[0]
for title, note, stmt in blocks:
    q = stmt.replace("$QUERY_TILE", f"'{tile}'").replace("$QUERY_SLIDE", f"'{slide}'")
    nvec = q.count("$QUERY_VEC")
    q = q.replace("$QUERY_VEC", "?")
    params = [list(qvec)] * nvec
    def run():
        # duckdb's execute() returns None when handed an empty parameter list, so the two cases
        # are kept apart rather than papered over with a default argument
        return con.execute(q, params) if params else con.execute(q)
    run().fetchall()                                     # warm the page cache first
    t0 = time.perf_counter()
    cur = run()
    rows = cur.fetchall()
    ms = (time.perf_counter() - t0) * 1000
    cols = [d[0] for d in cur.description]
    md += [f"## {title}", ""]
    if note:
        md += [note, ""]
    md += ["```sql", stmt.strip(), "```", "",
           f"`{ms:.0f} ms`, {len(rows)} rows", "", fmt(cols, rows), ""]
    print(f"{title[:40]:42s} {ms:8.0f} ms  {len(rows)} rows")

open(os.path.join(C.HERE, "example_queries.md"), "w").write("\n".join(md))
print("wrote example_queries.md")
con.close()
