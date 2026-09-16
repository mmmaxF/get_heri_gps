#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import math
import sqlite3
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point


OFFSHORE_DISTANCE_M = 3000
OFFSHORE_SEARCH_M = 4000


def point_in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i, pi in enumerate(ring):
        xi, yi = pi
        xj, yj = ring[j]
        intersects = ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi)
        if intersects:
            inside = not inside
        j = i
    return inside


def point_in_polygon(lon, lat, rings):
    # N03 shapefile parts are treated with even-odd rule. This handles islands and holes well enough for admin lookup.
    inside = False
    for ring in rings:
        if point_in_ring(lon, lat, ring):
            inside = not inside
    return inside


class AdminGeocoder:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def area_count(self):
        row = self.conn.execute("SELECT COUNT(*) AS c FROM areas").fetchone()
        return int(row["c"])

    def reverse(self, lat, lon):
        rows = self.conn.execute(
            """
            SELECT *
            FROM areas
            WHERE min_lat <= ?
              AND max_lat >= ?
              AND min_lon <= ?
              AND max_lon >= ?
            """,
            (lat, lat, lon, lon),
        ).fetchall()
        for row in rows:
            rings = json.loads(row["geometry_json"])
            if point_in_polygon(lon, lat, rings):
                address = f"{row['prefecture']}{row['city']}"
                return {
                    "ok": True,
                    "prefecture": row["prefecture"],
                    "city": row["city"],
                    "ward": row["ward"] or "",
                    "address_label": address,
                    "admin_code": row["admin_code"] or "",
                }
        offshore = self._find_nearest_prefecture(lat, lon)
        if offshore is not None:
            return offshore
        return {
            "ok": False,
            "error": "area not found",
            "prefecture": "",
            "city": "",
            "ward": "",
            "address_label": "",
            "admin_code": "",
        }

    def _find_nearest_prefecture(self, lat, lon):
        # Conservative candidate window for Japan; final distances are in metres.
        lat_delta = OFFSHORE_SEARCH_M / 111_000
        lon_delta = OFFSHORE_SEARCH_M / (111_000 * math.cos(math.radians(lat)))
        rows = self.conn.execute(
            """
            SELECT prefecture, geometry_json
            FROM areas
            WHERE min_lat <= ? AND max_lat >= ?
              AND min_lon <= ? AND max_lon >= ?
            ORDER BY prefecture
            """,
            (lat + lat_delta, lat - lat_delta, lon + lon_delta, lon - lon_delta),
        ).fetchall()
        if not rows:
            return None

        local_crs = CRS.from_proj4(
            f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m"
        )
        transformer = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
        origin = Point(0, 0)
        nearest_distance = float("inf")
        nearest_prefecture = None
        for row in rows:
            for ring in json.loads(row["geometry_json"]):
                if len(ring) < 2:
                    continue
                # Imported rings are normally closed, but close them explicitly.
                coordinates = ring if ring[0] == ring[-1] else ring + [ring[0]]
                xs, ys = transformer.transform(*zip(*coordinates))
                distance = origin.distance(LineString(zip(xs, ys)))
                if distance < nearest_distance:
                    nearest_distance = distance
                    nearest_prefecture = row["prefecture"]

        if nearest_distance > OFFSHORE_DISTANCE_M:
            return None
        return {
            "ok": True,
            "prefecture": nearest_prefecture,
            "city": "",
            "ward": "",
            "address_label": f"{nearest_prefecture}沖",
            "admin_code": "",
        }
