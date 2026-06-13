#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download MLIT N03 administrative area data and build a compact SQLite DB."""

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import shapefile


DEFAULT_N03_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2026/N03-20260101_56_GML.zip"
DB_PATH = Path(os.environ.get("GEOCODER_DB_PATH", "/app/data/admin_area.sqlite"))
DATA_URL = os.environ.get("GEOCODER_DATA_URL", DEFAULT_N03_URL)
UPDATE_DAYS = int(float(os.environ.get("GEOCODER_UPDATE_DAYS", "30")))
FORCE_UPDATE = os.environ.get("GEOCODER_FORCE_UPDATE", "0") not in ("0", "false", "False", "no", "")


def db_is_fresh(path):
    if FORCE_UPDATE or not path.exists():
        return False
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM areas").fetchone()
            if not row or int(row[0]) <= 0:
                return False
    except sqlite3.Error:
        return False
    age_days = (time.time() - path.stat().st_mtime) / 86400
    return age_days < UPDATE_DAYS


def create_schema(conn):
    conn.executescript(
        """
        DROP TABLE IF EXISTS areas;
        DROP TABLE IF EXISTS metadata;

        CREATE TABLE areas (
          id INTEGER PRIMARY KEY,
          prefecture TEXT NOT NULL,
          city TEXT NOT NULL,
          ward TEXT,
          admin_code TEXT,
          min_lat REAL NOT NULL,
          max_lat REAL NOT NULL,
          min_lon REAL NOT NULL,
          max_lon REAL NOT NULL,
          geometry_json TEXT NOT NULL
        );

        CREATE INDEX idx_areas_bbox
        ON areas(min_lat, max_lat, min_lon, max_lon);

        CREATE TABLE metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )


def create_empty_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with sqlite3.connect(tmp) as conn:
        create_schema(conn)
        conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("source_url", "empty"))
        conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("created_at", str(int(time.time()))))
    tmp.replace(path)


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"download: {url}")
    with urllib.request.urlopen(url, timeout=120) as res, dest.open("wb") as f:
        shutil.copyfileobj(res, f)


def find_shapefile(root):
    candidates = sorted(root.rglob("*.shp"))
    if not candidates:
        raise RuntimeError("N03 shapefile not found in zip")
    for path in candidates:
        if "N03" in path.name:
            return path
    return candidates[0]


def field_map(reader):
    names = [f[0] for f in reader.fields[1:]]
    return {name: idx for idx, name in enumerate(names)}


def get_value(record, fmap, name):
    idx = fmap.get(name)
    if idx is None:
        return ""
    val = record[idx]
    return "" if val is None else str(val).strip()


def rings_from_shape(shape):
    points = shape.points
    parts = list(shape.parts) + [len(points)]
    rings = []
    for start, end in zip(parts, parts[1:]):
        ring = [[float(lon), float(lat)] for lon, lat in points[start:end]]
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def bbox_from_rings(rings):
    lons = [p[0] for ring in rings for p in ring]
    lats = [p[1] for ring in rings for p in ring]
    return min(lats), max(lats), min(lons), max(lons)


def build_db(zip_path, db_path, source_url):
    with tempfile.TemporaryDirectory() as td:
        extract_dir = Path(td) / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        shp_path = find_shapefile(extract_dir)
        print(f"read shapefile: {shp_path}")
        reader = None
        last_error = None
        for encoding in ("utf-8", "cp932"):
            try:
                candidate = shapefile.Reader(str(shp_path), encoding=encoding)
                # Force one record decode so an incompatible encoding fails here.
                next(candidate.iterRecords())
                reader = shapefile.Reader(str(shp_path), encoding=encoding)
                print(f"shapefile encoding: {encoding}")
                break
            except Exception as exc:
                last_error = exc
        if reader is None:
            raise RuntimeError(f"failed to read shapefile encoding: {last_error}")
        fmap = field_map(reader)

        db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = db_path.with_suffix(".tmp")
        if tmp.exists():
            tmp.unlink()
        with sqlite3.connect(tmp) as conn:
            create_schema(conn)
            count = 0
            for sr in reader.iterShapeRecords():
                rec = sr.record
                pref = get_value(rec, fmap, "N03_001")
                city = get_value(rec, fmap, "N03_004")
                ward = get_value(rec, fmap, "N03_005")
                code = get_value(rec, fmap, "N03_007")
                if not pref or not city:
                    continue
                rings = rings_from_shape(sr.shape)
                if not rings:
                    continue
                min_lat, max_lat, min_lon, max_lon = bbox_from_rings(rings)
                conn.execute(
                    """
                    INSERT INTO areas(prefecture, city, ward, admin_code, min_lat, max_lat, min_lon, max_lon, geometry_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (pref, city, ward, code, min_lat, max_lat, min_lon, max_lon, json.dumps(rings, separators=(",", ":"))),
                )
                count += 1
            conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("source_url", source_url))
            conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("created_at", str(int(time.time()))))
            conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("area_count", str(count)))
        tmp.replace(db_path)
        print(f"wrote {db_path} areas={count}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--empty", action="store_true")
    args = ap.parse_args()

    if args.empty:
        create_empty_db(DB_PATH)
        return
    if db_is_fresh(DB_PATH):
        print(f"admin area DB is fresh: {DB_PATH}")
        return
    cache_dir = DB_PATH.parent / "source"
    zip_path = cache_dir / Path(DATA_URL).name
    download(DATA_URL, zip_path)
    build_db(zip_path, DB_PATH, DATA_URL)


if __name__ == "__main__":
    main()
