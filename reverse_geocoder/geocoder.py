#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import math
import os
import sqlite3
from pathlib import Path

from coastline import CoastlineIndex


def _positive_env_float(name, default):
    try:
        value = float(os.environ.get(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive finite number (metres)") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number (metres)")
    return value


OFFSHORE_DISTANCE_M = _positive_env_float("OFFSHORE_DISTANCE_M", "3000")
OFFSHORE_SEARCH_M = _positive_env_float("OFFSHORE_SEARCH_M", "4000")
if OFFSHORE_SEARCH_M <= OFFSHORE_DISTANCE_M:
    raise ValueError("OFFSHORE_SEARCH_M must be greater than OFFSHORE_DISTANCE_M")


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
    def __init__(self, db_path, coastline_path=None):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        coastline_path = coastline_path or os.environ.get(
            "GEOCODER_COASTLINE_DB_PATH", str(self.db_path.parent / "coastline.sqlite"))
        self.coastline = CoastlineIndex(coastline_path)

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
        offshore = self._find_nearest_coastal_city(lat, lon)
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

    def _find_nearest_coastal_city(self, lat, lon):
        nearest = self.coastline.nearest(lat, lon, OFFSHORE_DISTANCE_M, OFFSHORE_SEARCH_M)
        if nearest is None:
            return None
        prefecture, city, ward, admin_code = nearest
        return {
            "ok": True,
            "prefecture": prefecture,
            "city": city,
            "ward": ward or "",
            "address_label": f"{prefecture}{city}沖",
            "admin_code": admin_code or "",
        }
