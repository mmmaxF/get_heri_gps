"""Read-only coastal segment index; coordinates in the cache are WGS84."""
import logging
import math
import sqlite3
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import distance, linestrings
from shapely.geometry import Point, box
from shapely.strtree import STRtree


class CoastlineIndex:
    def __init__(self, path):
        self.coordinates = np.empty((0, 2, 2))
        self.labels = []
        self.tree = None
        path = Path(path)
        if not path.exists():
            logging.getLogger(__name__).warning(
                "Coastline cache missing: %s; offshore addresses are disabled", path)
            return
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM coastal_segments").fetchone()[0]
            self.coordinates = np.empty((count, 2, 2), dtype=float)
            cursor = conn.execute(
                "SELECT prefecture, city, ward, admin_code, lon1, lat1, lon2, lat2 "
                "FROM coastal_segments ORDER BY prefecture, city, admin_code, id"
            )
            labels = {}
            offset = 0
            # Nationwide data has millions of edges. Keep only one small batch of
            # SQLite/Python rows, and share municipality labels between all edges.
            while batch := cursor.fetchmany(8192):
                end = offset + len(batch)
                self.labels.extend(labels.setdefault(row[:4], row[:4]) for row in batch)
                self.coordinates[offset:end] = np.asarray(
                    [row[4:] for row in batch], dtype=float).reshape(-1, 2, 2)
                offset = end
            if offset != count:
                raise ValueError("Coastline cache changed while reading")
        if count:
            self.tree = STRtree(linestrings(self.coordinates))
        logging.getLogger(__name__).info("Loaded %d coastal segments", count)

    def nearest(self, lat, lon, distance_m, search_m):
        if self.tree is None:
            return None
        transformer = Transformer.from_crs(
            "EPSG:4326",
            f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m",
            always_xy=True,
        )
        # Degree-space nearest is only a seed, never the final municipality.
        # Its metric distance is an upper bound on the true nearest distance.
        seed = int(self.tree.nearest(Point(lon, lat)))
        coordinates = self.coordinates[seed:seed + 1]
        xs, ys = transformer.transform(coordinates[..., 0], coordinates[..., 1])
        seed_distance = float(distance(
            linestrings(np.stack((xs, ys), axis=-1)), Point(0, 0))[0])
        radius = min(search_m, max(2000.0, seed_distance * 1.01 + 1))
        # The window contains every potentially closer coastal segment.
        while True:
            dy = radius / 110_000
            extreme_lat = min(89.9, abs(lat) + dy)
            dx = radius / (110_000 * math.cos(math.radians(extreme_lat)))
            indices = self.tree.query(box(lon - dx, lat - dy, lon + dx, lat + dy))
            if len(indices):
                # Vectorized transform and distance: no Python loop per coastal edge.
                coordinates = self.coordinates[indices]
                xs, ys = transformer.transform(coordinates[..., 0], coordinates[..., 1])
                projected = linestrings(np.stack((xs, ys), axis=-1))
                distances = distance(projected, Point(0, 0))
                # Cache order resolves exact ties consistently, independently of tree order.
                order = np.lexsort((indices, distances))
                best = int(order[0])
                closest = float(distances[best])
                if closest <= radius or radius == search_m:
                    if closest > distance_m:
                        return None
                    return self.labels[int(indices[best])]
            if radius == search_m:
                return None
            radius = min(radius * 2, search_m)
