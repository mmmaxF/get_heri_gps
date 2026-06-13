#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sqlite3
from pathlib import Path


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
        return {
            "ok": False,
            "error": "area not found",
            "prefecture": "",
            "city": "",
            "ward": "",
            "address_label": "",
            "admin_code": "",
        }
