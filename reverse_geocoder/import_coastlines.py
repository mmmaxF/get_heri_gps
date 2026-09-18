#!/usr/bin/env python3
"""Build an atomic C23 coastline cache labelled with current N03 municipalities."""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import zipfile

import shapefile
import numpy as np
from pyproj import Geod, Transformer
from shapely.geometry import Point, Polygon, LineString
from shapely.ops import transform
from shapely import contains, get_coordinates, get_parts, linestrings, make_valid, prepare
from shapely.strtree import STRtree

from import_admin_areas import DB_PATH, download

# C23 is supplied per prefecture; only prefectures present in N03 are imported.
DEFAULT_URL_TEMPLATE = "https://nlftp.mlit.go.jp/ksj/gml/data/C23/C23-06/C23-06_{code}_GML.zip"
GEOD = Geod(ellps="WGS84")
TO_WGS84 = Transformer.from_crs("EPSG:4612", "EPSG:4326", always_xy=True)
TO_METRIC = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
FROM_METRIC = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def create_schema(conn):
    conn.executescript("""
        CREATE TABLE coastal_segments (
            id INTEGER PRIMARY KEY,
            prefecture TEXT NOT NULL, city TEXT NOT NULL, ward TEXT NOT NULL,
            admin_code TEXT NOT NULL,
            lon1 REAL NOT NULL, lat1 REAL NOT NULL,
            lon2 REAL NOT NULL, lat2 REAL NOT NULL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)


def split_edge(start, end):
    """Bound projection chord error and keep spatial index boxes small (<=500 m)."""
    length = GEOD.inv(*start, *end)[2]
    if length == 0:
        return
    pieces = max(1, math.ceil(length / 500))
    points = [start]
    if pieces > 1:
        points.extend(GEOD.npts(*start, *end, pieces - 1))
    points.append(end)
    yield from zip(points, points[1:])


def admin_index(conn):
    labels, polygons = [], []
    for row in conn.execute(
        "SELECT prefecture, city, ward, admin_code, geometry_json FROM areas "
        "ORDER BY prefecture, city, admin_code, id"
    ):
        polygon = Polygon()
        # Same even-odd interpretation as the existing land lookup.
        for ring in json.loads(row[4]):
            if len(ring) >= 3:
                polygon = polygon.symmetric_difference(make_valid(Polygon(ring)))
        if not polygon.is_empty:
            polygons.append(transform(TO_METRIC.transform, polygon))
            labels.append((row[0], row[1], row[2] or "", row[3] or ""))
    polygons = np.asarray(polygons, dtype=object)
    prepare(polygons)
    return labels, polygons, STRtree(polygons)


def boundary_index(polygons):
    """Index individual edges to avoid intersecting entire municipal polygons."""
    edges, parents = [], []
    for parent, polygon in enumerate(polygons):
        for part in get_parts(polygon.boundary):
            coordinates = get_coordinates(part)
            if len(coordinates) >= 2:
                segments = linestrings(np.stack((coordinates[:-1], coordinates[1:]), axis=1))
                edges.extend(segments)
                parents.extend([parent] * len(segments))
    return edges, STRtree(edges), np.asarray(parents, dtype=int)


def split_at_municipal_boundaries(start, end, boundaries, tree):
    """Cut coastal edges where they cross current municipality boundaries."""
    line = LineString([TO_METRIC.transform(*start), TO_METRIC.transform(*end)])
    cuts = {0.0, line.length}

    def add_cuts(geometry):
        if geometry.is_empty:
            return
        if geometry.geom_type == 'Point':
            cuts.add(line.project(geometry))
        elif geometry.geom_type in ('LineString', 'LinearRing'):
            cuts.add(line.project(Point(geometry.coords[0])))
            cuts.add(line.project(Point(geometry.coords[-1])))
        elif hasattr(geometry, 'geoms'):
            for part in geometry.geoms:
                add_cuts(part)

    for index in tree.query(line, predicate='intersects'):
        add_cuts(line.intersection(boundaries[int(index)]))
    positions = sorted(cuts)
    for left, right in zip(positions, positions[1:]):
        if right - left < 1e-6:
            continue
        a, b = line.interpolate(left), line.interpolate(right)
        yield FROM_METRIC.transform(a.x, a.y), FROM_METRIC.transform(b.x, b.y)


def current_label(start, end, old_code, labels, polygons, tree, boundaries, boundary_tree, parents):
    midpoint = GEOD.npts(*start, *end, 1)[0]
    point = Point(*TO_METRIC.transform(*midpoint))
    candidates = tree.query(point)
    containing = candidates[contains(polygons[candidates], point)]
    if len(containing):
        # Prepared polygons avoid repeatedly scanning long municipal coastlines.
        indices = containing
    else:
        # Outside all land polygons, nearest polygon == nearest boundary edge.
        # Indexing short edges makes this independent of polygon vertex counts.
        edges, distances = boundary_tree.query_nearest(point, all_matches=True, return_distance=True)
        if not len(edges) or min(distances) * math.cos(math.radians(midpoint[1])) > 2000:
            return None
        indices = np.unique(parents[edges])
    index = min((int(i) for i in indices), key=lambda i: (labels[i][3] != old_code, labels[i]))
    return labels[index]


def build_cache(admin_path, destination, url_template=DEFAULT_URL_TEMPLATE, force=False):
    with sqlite3.connect(f"{admin_path.resolve().as_uri()}?mode=ro", uri=True) as admin:
        codes = sorted({str(row[0])[:2] for row in admin.execute(
            "SELECT DISTINCT admin_code FROM areas WHERE admin_code IS NOT NULL"
        ) if str(row[0])[:2].isdigit()})
        # Eight prefectures have no sea coast.
        codes = [c for c in codes if c not in {'09','10','11','19','20','21','25','29'}]
        if not codes:
            print("No coastal prefectures in administrative database; coastline cache not built")
            return
        fingerprint = hashlib.sha256(json.dumps({
            'admin_size': admin_path.stat().st_size,
            'admin_mtime_ns': admin_path.stat().st_mtime_ns,
            'urls': [url_template.format(code=c) for c in codes],
            'schema': 4,
        }, sort_keys=True).encode()).hexdigest()
        if destination.exists() and not force:
            try:
                with sqlite3.connect(f"{destination.resolve().as_uri()}?mode=ro", uri=True) as cache:
                    row = cache.execute("SELECT value FROM metadata WHERE key='fingerprint'").fetchone()
                    if row and row[0] == fingerprint:
                        print("Coastline cache is current")
                        return
            except sqlite3.Error:
                pass
        labels, polygons, tree = admin_index(admin)
        boundaries, boundary_tree, parents = boundary_index(polygons)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.unlink(missing_ok=True)
    mapped = skipped = 0
    try:
        with sqlite3.connect(temporary) as cache:
            create_schema(cache)
            for code in codes:
                url = url_template.format(code=code)
                source_id = hashlib.sha256(url.encode()).hexdigest()[:12]
                archive = destination.parent / 'source' / f'C23-{code}-{source_id}.zip'
                if force or not archive.exists():
                    download(url, archive.with_suffix('.download'))
                    archive.with_suffix('.download').replace(archive)
                with zipfile.ZipFile(archive) as zf:
                    names = [n[:-4] for n in zf.namelist() if n.lower().endswith('.shp')]
                    if not names:
                        raise ValueError(f"No coastline shapefile in {archive}")
                    for name in names:
                        reader = shapefile.Reader(
                            shp=io.BytesIO(zf.read(name+'.shp')),
                            shx=io.BytesIO(zf.read(name+'.shx')),
                            dbf=io.BytesIO(zf.read(name+'.dbf')),
                            encoding='cp932',
                        )
                        fields = [f[0] for f in reader.fields[1:]]
                        if 'C23_001' not in fields:
                            raise ValueError(f"Missing C23 administrative code: {name}")
                        for record in reader.iterShapeRecords():
                            if record.shape.shapeType not in (3, 13, 23):
                                raise ValueError(f"Coastline must be a polyline: {name}")
                            old_code = str(record.record[fields.index('C23_001')])
                            points = [TO_WGS84.transform(*p[:2]) for p in record.shape.points]
                            parts = list(record.shape.parts) + [len(points)]
                            batch = []
                            for a, b in zip(parts, parts[1:]):
                                for p, q in zip(points[a:b], points[a+1:b]):
                                    for edge_start, edge_end in split_edge(p, q):
                                        for start, end in split_at_municipal_boundaries(edge_start, edge_end, boundaries, boundary_tree):
                                            label = current_label(start, end, old_code, labels, polygons, tree, boundaries, boundary_tree, parents)
                                            if label is None:
                                                skipped += 1
                                                continue
                                            batch.append((*label, *start, *end))
                                            mapped += 1
                            cache.executemany(
                                "INSERT INTO coastal_segments(prefecture,city,ward,admin_code,"
                                "lon1,lat1,lon2,lat2) VALUES (?,?,?,?,?,?,?,?)", batch)
                print(f"Imported coastlines for prefecture {code}: {mapped} segments, {skipped} unmatched", flush=True)
            if mapped == 0:
                raise ValueError("No coastline segments matched administrative data")
            cache.executemany("INSERT INTO metadata VALUES (?,?)", [
                ('fingerprint', fingerprint), ('source_urls', json.dumps([url_template.format(code=c) for c in codes])),
                ('segment_count', str(mapped)), ('unmatched_count', str(skipped)),
            ])
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    destination = Path(os.environ.get('GEOCODER_COASTLINE_DB_PATH', str(DB_PATH.parent / 'coastline.sqlite')))
    build_cache(DB_PATH, destination, os.environ.get('GEOCODER_COASTLINE_URL_TEMPLATE', DEFAULT_URL_TEMPLATE), args.force)


if __name__ == '__main__':
    main()
