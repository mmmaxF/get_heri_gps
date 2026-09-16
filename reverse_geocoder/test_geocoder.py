import json
import unittest
from unittest.mock import patch

from pyproj import Transformer

from geocoder import AdminGeocoder


class OffshoreGeocoderTest(unittest.TestCase):
    def setUp(self):
        self.geocoder = AdminGeocoder(":memory:")
        self.addCleanup(self.geocoder.conn.close)
        self.geocoder.conn.execute(
            """CREATE TABLE areas (
                prefecture TEXT, city TEXT, ward TEXT, admin_code TEXT,
                min_lat REAL, max_lat REAL, min_lon REAL, max_lon REAL,
                geometry_json TEXT
            )"""
        )
        self.to_lonlat = Transformer.from_crs(
            "+proj=aeqd +lat_0=34.5 +lon_0=135 +datum=WGS84 +units=m",
            "EPSG:4326", always_xy=True,
        )

    def add_area(self, prefecture, left, right, bottom=-4000, top=4000):
        # Deliberately unclosed: the nearest edge can be the closing edge.
        ring = [self.to_lonlat.transform(x, y) for x, y in (
            (left, bottom), (right, bottom), (right, top), (left, top)
        )]
        lons, lats = zip(*ring)
        self.geocoder.conn.execute(
            "INSERT INTO areas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (prefecture, "テスト市", "", "12345", min(lats), max(lats),
             min(lons), max(lons), json.dumps([ring])),
        )

    def test_land_keeps_address_and_skips_distance(self):
        self.add_area("大阪府", -1000, 1000)
        with patch.object(self.geocoder, "_find_nearest_prefecture") as nearest:
            result = self.geocoder.reverse(34.5, 135)
        nearest.assert_not_called()
        self.assertEqual(result["address_label"], "大阪府テスト市")
        self.assertEqual(result["admin_code"], "12345")

    def test_nearest_prefecture_and_edge_interior(self):
        self.add_area("大阪府", 2600, 3600)
        self.add_area("兵庫県", 1800, 2200)
        self.assertEqual(self.geocoder.reverse(34.5, 135), {
            "ok": True, "prefecture": "兵庫県", "city": "", "ward": "",
            "address_label": "兵庫県沖", "admin_code": "",
        })

    def test_just_inside_threshold(self):
        self.add_area("兵庫県", 2999.99, 3500)
        self.assertTrue(self.geocoder.reverse(34.5, 135)["ok"])

    def test_just_outside_threshold(self):
        self.add_area("兵庫県", 3000.01, 3500)
        self.assert_unknown()

    def test_no_nearby_candidate(self):
        self.add_area("兵庫県", 10000, 11000)
        self.assert_unknown()

    def test_empty_database(self):
        self.assert_unknown()

    def assert_unknown(self):
        self.assertEqual(self.geocoder.reverse(34.5, 135), {
            "ok": False, "error": "area not found", "prefecture": "",
            "city": "", "ward": "", "address_label": "", "admin_code": "",
        })


if __name__ == "__main__":
    unittest.main()
