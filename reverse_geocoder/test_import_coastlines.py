import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import shapefile
from pyproj import Geod

from import_admin_areas import create_schema as create_admin_schema
from import_coastlines import build_cache, split_edge


class CoastlineImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.admin = self.root / 'admin.sqlite'
        self.cache = self.root / 'coast.sqlite'
        with sqlite3.connect(self.admin) as conn:
            create_admin_schema(conn)
            for city, code, left, right in [('西市','27101',135.0,135.005),('東市','27102',135.005,135.01)]:
                ring = [[left,34.49],[right,34.49],[right,34.51],[left,34.51],[left,34.49]]
                conn.execute('''INSERT INTO areas(prefecture,city,ward,admin_code,
                    min_lat,max_lat,min_lon,max_lon,geometry_json) VALUES (?,?,?,?,?,?,?,?,?)''',
                    ('大阪府',city,'',code,34.49,34.51,left,right,json.dumps([ring])))
        base = self.root/'C23'
        with shapefile.Writer(str(base),shapeType=shapefile.POLYLINE,encoding='cp932') as writer:
            writer.field('C23_001','C',size=5)
            # Both municipalities within one old-code coast; multipart must not connect.
            writer.line([[[135.001,34.5],[135.009,34.5]],[[135.001,34.505],[135.002,34.505]]])
            writer.record('27999')
        self.archive = self.root/'input.zip'
        with zipfile.ZipFile(self.archive,'w') as archive:
            for suffix in ['.shp','.shx','.dbf']:
                archive.write(base.with_suffix(suffix),arcname='C23'+suffix)

    def test_current_municipality_split_and_open_multipart_lines(self):
        build_cache(self.admin,self.cache,self.archive.as_uri())
        with sqlite3.connect(self.cache) as conn:
            rows = conn.execute('SELECT city,admin_code,lon1,lat1,lon2,lat2 FROM coastal_segments').fetchall()
        self.assertEqual({r[0] for r in rows},{'西市','東市'})
        for city,code,x1,y1,x2,y2 in rows:
            self.assertEqual(code,'27101' if city=='西市' else '27102')
            self.assertAlmostEqual(y1,y2,places=5)
            self.assertTrue(max(x1,x2)<=135.0050001 if city=='西市' else min(x1,x2)>=135.0049999)

    def test_unchanged_cache_is_reused(self):
        build_cache(self.admin,self.cache,self.archive.as_uri())
        before = self.cache.stat().st_mtime_ns
        with patch('import_coastlines.download',side_effect=AssertionError('should not download')):
            build_cache(self.admin,self.cache,self.archive.as_uri())
        self.assertEqual(before,self.cache.stat().st_mtime_ns)

    def test_failed_rebuild_keeps_existing_cache(self):
        build_cache(self.admin,self.cache,self.archive.as_uri())
        before = self.cache.read_bytes()
        with self.assertRaises(Exception):
            build_cache(self.admin,self.cache,(self.root/'missing.zip').as_uri(),force=True)
        self.assertEqual(before,self.cache.read_bytes())
        self.assertFalse(self.cache.with_suffix('.tmp').exists())

    def test_changed_source_does_not_reuse_previous_zip(self):
        build_cache(self.admin,self.cache,self.archive.as_uri())
        before = self.cache.read_bytes()
        empty = self.root/'empty.zip'
        with zipfile.ZipFile(empty,'w'):
            pass
        with self.assertRaisesRegex(ValueError,'No coastline shapefile'):
            build_cache(self.admin,self.cache,empty.as_uri())
        self.assertEqual(before,self.cache.read_bytes())

    def test_current_names_refresh_after_admin_update(self):
        build_cache(self.admin,self.cache,self.archive.as_uri())
        with sqlite3.connect(self.admin) as conn:
            conn.execute("UPDATE areas SET city='新西市' WHERE city='西市'")
        with patch('import_coastlines.download',side_effect=AssertionError('ZIP should be cached')):
            build_cache(self.admin,self.cache,self.archive.as_uri())
        with sqlite3.connect(self.cache) as conn:
            cities = {row[0] for row in conn.execute('SELECT city FROM coastal_segments')}
        self.assertEqual(cities,{'新西市','東市'})

    def test_fast_labels_match_exhaustive_polygon_distances(self):
        from shapely.geometry import Point
        from import_coastlines import admin_index, boundary_index, current_label, GEOD, TO_METRIC
        with sqlite3.connect(self.admin) as conn:
            labels, polygons, tree = admin_index(conn)
        boundaries, boundary_tree, parents = boundary_index(polygons)
        for lon, lat in [(135.001,34.5),(135.008,34.5),(134.999,34.5),
                         (135.011,34.5),(135.005,34.49),(134.9,34.5)]:
            start, end = (lon,lat-0.00001),(lon,lat+0.00001)
            midpoint = GEOD.npts(*start,*end,1)[0]
            point = Point(*TO_METRIC.transform(*midpoint))
            distances = [polygon.distance(point) for polygon in polygons]
            best = min(distances)
            if best * math.cos(math.radians(midpoint[1])) > 2000:
                expected = None
            else:
                tied = [i for i,distance in enumerate(distances) if distance == best]
                index = min(tied,key=lambda i:(labels[i][3]!='27102',labels[i]))
                expected = labels[index]
            self.assertEqual(current_label(start,end,'27102',labels,polygons,tree,
                             boundaries,boundary_tree,parents),expected)

    def test_admin_freshness_checks_configured_source(self):
        from import_admin_areas import db_is_fresh
        with sqlite3.connect(self.admin) as conn:
            conn.execute("INSERT INTO metadata VALUES ('source_url','source-one')")
        with patch('import_admin_areas.DATA_URL','source-one'), patch('import_admin_areas.FORCE_UPDATE',False):
            self.assertTrue(db_is_fresh(self.admin))
        with patch('import_admin_areas.DATA_URL','source-two'), patch('import_admin_areas.FORCE_UPDATE',False):
            self.assertFalse(db_is_fresh(self.admin))

    def test_segment_length_is_bounded(self):
        edges = list(split_edge((135,34.5),(135.1,34.5)))
        self.assertGreater(len(edges),1)
        geod = Geod(ellps='WGS84')
        self.assertTrue(all(geod.inv(*a,*b)[2]<=500.00001 for a,b in edges))


if __name__ == '__main__':
    unittest.main()
