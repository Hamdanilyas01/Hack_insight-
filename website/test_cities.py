"""Ensure city switching never reuses another city's evidence or cache."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
import city_data
import environment_data as env
from test_environment import source_fixture


class CityTests(unittest.TestCase):
    def setUp(self):
        env._cache.clear()

    def test_all_existing_cities_have_separate_saved_data(self):
        catalog = city_data.cities()
        self.assertEqual({c['id'] for c in catalog}, {'karachi','lahore','islamabad','multan'})
        for city in catalog:
            data = server.read_dashboard(city=city['id'])
            self.assertEqual(data['city'], city)
            root = next(n for n in data['network']['nodes'] if n['group']=='location')
            self.assertEqual(root['id'], city['id'])
            self.assertEqual(root['label'], city['name'])
            if city['id']!='karachi':
                stored = next(c for c in city_data.city_feed()['cities'] if c['city']==city['name'])
                self.assertEqual(data['weather']['hourly'][0]['temperature_c'], stored['hours'][0]['temperature_2m_c'])
                self.assertEqual(data['air_history']['daily'], [])
                self.assertFalse(data['forecast']['available'])
                self.assertNotIn('forecast', [n['id'] for n in data['network']['nodes']])

    def test_city_specific_metrics_are_not_karachi_metrics(self):
        for name in ['lahore','islamabad','multan']:
            data=server.read_dashboard(city=name)
            self.assertEqual(data['metrics']['city'].casefold(),name)
            self.assertEqual(data['risk_metrics']['accuracy'],data['metrics']['risk_classification_accuracy'])
        with self.assertRaises(ValueError):server.read_dashboard(city='quetta')

    def test_coordinates_and_caches_are_city_specific(self):
        for kind in ['weather','air']:
            for index,name in enumerate(['karachi','lahore','multan']):
                city=city_data.get_city(name)
                url=env.request_url(kind,city)
                self.assertIn('latitude='+str(city['latitude']),url)
                raw=source_fixture(kind)
                field='temperature_2m' if kind=='weather' else 'us_aqi'
                raw['current'][field]=20+index
                with patch.object(env,'get_json',return_value=raw):
                    env.cached_feed(kind,server.heat_index,city)
            for index,name in enumerate(['karachi','lahore','multan']):
                with patch.object(env,'get_json') as fetch:
                    result=env.cached_feed(kind,server.heat_index,city_data.get_city(name))
                fetch.assert_not_called()
                self.assertEqual(result['current']['temperature_c' if kind=='weather' else 'us_aqi'],20+index)

    def test_unknown_city_rejected(self):
        for city in ['not-a-city','../../warehouse',None,{},'', 'quetta', 'rawalpindi']:
            with self.assertRaises(ValueError):server.read_dashboard(city=city)

    def test_assistant_uses_selected_city(self):
        with patch.object(env,'get_json',side_effect=lambda url:source_fixture('weather' if '/forecast?' in url else 'air')),patch.dict('os.environ',{'HEATSAFE_DISABLE_LIVE':'0'}):
            weather=server.answer_insight('What is the current temperature?',city='lahore')
            air=server.answer_insight('What is the current AQI?',city='multan')
        self.assertIn('for Lahore',weather)
        self.assertIn('Multan · US AQI',air)
        prediction=server.answer_insight('What is the predicted heat index tomorrow?',city='lahore')
        self.assertIn('No stored live HeatSafe ML prediction is bundled for Lahore',prediction)


if __name__=='__main__':unittest.main()
