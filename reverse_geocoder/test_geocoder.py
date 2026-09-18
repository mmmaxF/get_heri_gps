import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pyproj import Transformer
from shapely.geometry import LineString, Point

from coastline import CoastlineIndex
from geocoder import AdminGeocoder
from import_coastlines import create_schema


class OffshoreGeocoderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.coast_path = Path(self.temp.name) / 'coastline.sqlite'
        with sqlite3.connect(self.coast_path) as conn:
            create_schema(conn)
        self.geocoder = AdminGeocoder(':memory:', self.coast_path)
        self.addCleanup(self.geocoder.conn.close)
        self.geocoder.conn.execute('''CREATE TABLE areas (
            prefecture TEXT, city TEXT, ward TEXT, admin_code TEXT,
            min_lat REAL, max_lat REAL, min_lon REAL, max_lon REAL,
            geometry_json TEXT
        )''')
        self.to_lonlat = Transformer.from_crs(
            '+proj=aeqd +lat_0=34.5 +lon_0=135 +datum=WGS84 +units=m',
            'EPSG:4326', always_xy=True)
        for name, value in [('OFFSHORE_DISTANCE_M',3000),('OFFSHORE_SEARCH_M',4000)]:
            context = patch('geocoder.' + name, value)
            context.start()
            self.addCleanup(context.stop)

    def add_area(self, prefecture, left, right, bottom=-4000, top=4000):
        ring = [self.to_lonlat.transform(x,y) for x,y in (
            (left,bottom),(right,bottom),(right,top),(left,top))]
        lons,lats = zip(*ring)
        self.geocoder.conn.execute('INSERT INTO areas VALUES (?,?,?,?,?,?,?,?,?)',
            (prefecture,'テスト市','','12345',min(lats),max(lats),min(lons),max(lons),json.dumps([ring])))

    def add_coast(self, prefecture, x, city='テスト市', bottom=-200, top=200):
        start = self.to_lonlat.transform(x,bottom)
        end = self.to_lonlat.transform(x,top)
        with sqlite3.connect(self.coast_path) as conn:
            conn.execute('''INSERT INTO coastal_segments
                (prefecture,city,ward,admin_code,lon1,lat1,lon2,lat2)
                VALUES (?,?,?,?,?,?,?,?)''', (prefecture,city,'','12345',*start,*end))
        self.geocoder.coastline = CoastlineIndex(self.coast_path)

    def test_land_keeps_address_and_skips_coastline(self):
        self.add_area('大阪府',-1000,1000)
        with patch.object(self.geocoder,'_find_nearest_coastal_city') as nearest:
            result = self.geocoder.reverse(34.5,135)
        nearest.assert_not_called()
        self.assertEqual(result['address_label'],'大阪府テスト市')
        self.assertEqual(result['admin_code'],'12345')

    def test_nearest_coast_returns_city(self):
        self.add_coast('大阪府',2600,city='大阪市')
        self.add_coast('兵庫県',1800,city='神戸市')
        self.assertEqual(self.geocoder.reverse(34.5,135), {
            'ok':True,'prefecture':'兵庫県','city':'神戸市','ward':'',
            'address_label':'兵庫県神戸市沖','admin_code':'12345'})

    def test_administrative_boundary_is_not_coastline(self):
        self.add_area('大阪府',100,500)
        self.add_coast('兵庫県',1800,city='神戸市')
        self.assertEqual(self.geocoder.reverse(34.5,135)['address_label'],'兵庫県神戸市沖')

    def test_just_inside_threshold(self):
        self.add_coast('兵庫県',2999.99)
        self.assertTrue(self.geocoder.reverse(34.5,135)['ok'])

    def test_just_outside_threshold(self):
        self.add_coast('兵庫県',3000.01)
        self.assert_unknown()

    def test_exact_threshold_is_included(self):
        self.add_coast('兵庫県',3000,bottom=0,top=100)
        # Test the configured inclusive bound using the actual projected distance.
        coords = self.geocoder.coastline.coordinates[0]
        projector = Transformer.from_crs('EPSG:4326',
            '+proj=aeqd +lat_0=34.5 +lon_0=135 +datum=WGS84 +units=m',always_xy=True)
        xs,ys = projector.transform(coords[:,0],coords[:,1])
        bound = Point(0,0).distance(LineString(zip(xs,ys)))
        with patch('geocoder.OFFSHORE_DISTANCE_M',bound):
            self.assertTrue(self.geocoder.reverse(34.5,135)['ok'])

    def test_search_expands_to_fifty_km(self):
        self.add_coast('兵庫県',49999)
        with patch('geocoder.OFFSHORE_DISTANCE_M',50000), patch('geocoder.OFFSHORE_SEARCH_M',60000):
            self.assertTrue(self.geocoder.reverse(34.5,135)['ok'])

    def test_corner_candidate_does_not_hide_closer_coast(self):
        # A candidate in the first square can be farther than its radius;
        # another outside that square can still be closer.
        self.add_coast('大阪府',1900,bottom=1900,top=2000)
        self.add_coast('兵庫県',2200)
        self.assertEqual(self.geocoder.reverse(34.5,135)['prefecture'],'兵庫県')

    def test_high_latitude_search_matches_exhaustive_metric_distance(self):
        import numpy as np
        from shapely import distance, linestrings
        rng = np.random.default_rng(123)
        inverse = Transformer.from_crs(
            '+proj=aeqd +lat_0=45 +lon_0=145 +datum=WGS84 +units=m',
            'EPSG:4326', always_xy=True)
        with sqlite3.connect(self.coast_path) as conn:
            for index, (x, y) in enumerate(rng.uniform(-55000,55000,(200,2))):
                start, end = inverse.transform(x,y), inverse.transform(x+200,y+300)
                conn.execute('INSERT INTO coastal_segments VALUES (?,?,?,?,?,?,?,?,?)',
                    (index+1,'北海道',str(index),'',str(index),*start,*end))
        coast = CoastlineIndex(self.coast_path)
        for x, y in [(0,0),(20000,20000),(-30000,-20000)]:
            lon, lat = inverse.transform(x,y)
            projector = Transformer.from_crs('EPSG:4326',
                f'+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m',always_xy=True)
            coords = coast.coordinates
            xs, ys = projector.transform(coords[...,0],coords[...,1])
            distances = distance(linestrings(np.stack((xs,ys),axis=-1)),Point(0,0))
            expected = coast.labels[int(np.argmin(distances))]
            self.assertEqual(coast.nearest(lat,lon,50000,60000),expected)

    def test_degree_nearest_seed_is_not_used_as_final_city(self):
        inverse = Transformer.from_crs(
            '+proj=aeqd +lat_0=45 +lon_0=145 +datum=WGS84 +units=m',
            'EPSG:4326',always_xy=True)
        with sqlite3.connect(self.coast_path) as conn:
            for city,code,a,b in [('東市','01001',(3000,-50),(3000,50)),
                                  ('北市','01002',(-50,3400),(50,3400))]:
                conn.execute('''INSERT INTO coastal_segments(prefecture,city,ward,admin_code,lon1,lat1,lon2,lat2) VALUES (?,?,?,?,?,?,?,?)''',
                    ('北海道',city,'',code,*inverse.transform(*a),*inverse.transform(*b)))
        coast = CoastlineIndex(self.coast_path)
        degree_seed = int(coast.tree.nearest(Point(145,45)))
        self.assertEqual(coast.labels[degree_seed][1],'北市')
        self.assertEqual(coast.nearest(45,145,5000,6000)[1],'東市')

    def test_tie_is_stable(self):
        self.add_coast('大阪府',1800)
        self.add_coast('兵庫県',1800)
        self.assertEqual(self.geocoder.reverse(34.5,135)['prefecture'],'兵庫県')

    def test_empty_coastline(self):
        self.assert_unknown()

    def test_missing_coastline_keeps_land_working(self):
        with self.assertLogs('coastline',level='WARNING'):
            self.geocoder.coastline = CoastlineIndex(Path(self.temp.name)/'missing.sqlite')
        self.add_area('大阪府',-1000,1000)
        self.assertTrue(self.geocoder.reverse(34.5,135)['ok'])

    def test_no_per_request_coastline_sql_or_json(self):
        self.add_coast('兵庫県',1800)
        with patch('coastline.sqlite3.connect',side_effect=AssertionError('unexpected DB read')):
            self.assertTrue(self.geocoder.reverse(34.5,135)['ok'])

    def assert_unknown(self):
        self.assertEqual(self.geocoder.reverse(34.5,135), {
            'ok':False,'error':'area not found','prefecture':'','city':'',
            'ward':'','address_label':'','admin_code':''})


if __name__ == '__main__':
    unittest.main()
