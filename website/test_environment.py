"""Deterministic tests for upstream validation, cache behavior and data-driven nodes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import environment_data as env
import server


def source_fixture(kind):
    now = int(datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0).timestamp())
    times = [now+i*3600 for i in range(49)]
    values = ({'temperature_2m':32.0,'relative_humidity_2m':60.0,'dew_point_2m':23.0,
               'wind_speed_10m':3.1,'weather_code':0,'is_day':1,'precipitation_probability':0}
              if kind == 'weather' else {'us_aqi':125.0,'pm2_5':35.0,'pm10':67.0,'nitrogen_dioxide':20.0,'ozone':40.0})
    units = ({'temperature_2m':'°C','wind_speed_10m':'m/s'} if kind == 'weather' else {k:'µg/m³' for k in values if k!='us_aqi'})
    result = {'utc_offset_seconds':0,'current':{'time':now,**values},'hourly':{'time':times,**{k:[v]*49 for k,v in values.items()}},'current_units':units.copy(),'hourly_units':units.copy()}
    if kind == 'weather':
        result['daily']={'time':[now], 'temperature_2m_max':[34.0], 'temperature_2m_min':[25.0], 'weather_code':[0]}
    return result


class EnvironmentTests(unittest.TestCase):
    def setUp(self): env._cache.clear()

    def test_live_sources_are_distinct_and_timestamped(self):
        for kind in ['weather','air']:
            result=env.parse_feed(source_fixture(kind),kind,server.heat_index)
            self.assertEqual(result['status'],'live')
            self.assertEqual(result['measurement_type'],'modeled')
            self.assertEqual(len(result['hourly']),49)
            self.assertIsNotNone(result['fetched_at_utc'].tzinfo)
        air=env.parse_feed(source_fixture('air'),'air',server.heat_index)
        self.assertEqual(air['current']['aqi_category'],'Unhealthy for sensitive groups')

    def test_inconsistent_lengths_and_duplicate_times_rejected(self):
        for kind in ['weather','air']:
            raw=source_fixture(kind);raw['hourly']['time'][1]=raw['hourly']['time'][0]
            with self.assertRaisesRegex(ValueError,'duplicate'):env.parse_feed(raw,kind,server.heat_index)
        raw=source_fixture('air');raw['hourly']['us_aqi'].pop()
        with self.assertRaisesRegex(ValueError,'length'):env.parse_feed(raw,'air',server.heat_index)

    def test_incorrect_units_are_rejected(self):
        for section,key,unit,kind in [('current_units','wind_speed_10m','km/h','weather'),('hourly_units','pm2_5','ppm','air')]:
            raw=source_fixture(kind);raw[section][key]=unit
            with self.assertRaisesRegex(ValueError,'unit'):env.parse_feed(raw,kind,server.heat_index)

    def test_null_aqi_is_not_zero_and_invalid_pollutants_are_missing(self):
        raw=source_fixture('air');raw['current']['us_aqi']=None;raw['hourly']['us_aqi']=[None]*49
        raw['current']['pm2_5']=-1
        result=env.parse_feed(raw,'air',server.heat_index)
        self.assertIsNone(result['current']['us_aqi']);self.assertIsNone(result['current']['pm2_5'])
        self.assertEqual(result['current']['aqi_category'],'Not available')

    def test_cached_reads_preserve_actual_upstream_pull_time(self):
        with patch.object(env,'get_json',return_value=source_fixture('air')) as fetch:
            first=env.cached_feed('air',server.heat_index)
            second=env.cached_feed('air',server.heat_index)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(first['fetched_at_utc'],second['fetched_at_utc'])
        self.assertTrue(second['cache_hit'])

    def test_refresh_failure_marks_old_response_stale(self):
        with patch.object(env,'get_json',return_value=source_fixture('air')):
            first=env.cached_feed('air',server.heat_index)
        env._cache['air']['retry_at']=0
        with patch.object(env,'get_json',side_effect=OSError('offline')):
            result=env.cached_feed('air',server.heat_index)
        self.assertEqual(result['status'],'cached_stale')
        self.assertEqual(result['fetched_at_utc'],first['fetched_at_utc'])

    def test_cold_source_failure_has_no_fake_current_value(self):
        with patch.object(env,'get_json',side_effect=OSError('offline')):
            self.assertIsNone(env.cached_feed('air',server.heat_index))

    def test_cached_valid_time_can_age_into_stale_status(self):
        with patch.object(env,'get_json',return_value=source_fixture('air')):
            env.cached_feed('air',server.heat_index)
        env._cache['air']['data']['valid_at_utc']=datetime.now(timezone.utc)-timedelta(hours=4)
        with patch.object(env,'get_json') as fetch:
            self.assertEqual(env.cached_feed('air',server.heat_index)['status'],'stale')
            fetch.assert_not_called()

    def test_historical_data_and_dynamic_nodes(self):
        data=server.read_dashboard(live=False)
        self.assertEqual(data['air_quality']['status'],'historical')
        self.assertIsNone(data['air_quality']['current']['us_aqi'])
        self.assertEqual(len(data['air_history']['monthly']),12)
        self.assertEqual(data['air_history']['monthly'][-1]['time_utc'].astimezone(timezone.utc).month,12)
        self.assertNotIn('aqi',[n['id'] for n in data['network']['nodes']])
        data['air_quality']['current']['us_aqi']=0.0
        data['air_quality']['current']['aqi_category']='Good'
        graph=env.build_network(data)
        self.assertEqual(next(n for n in graph['nodes'] if n['id']=='aqi')['value'],0.0)
        data['air_quality']['current']['us_aqi']=177.0
        self.assertEqual(next(n for n in env.build_network(data)['nodes'] if n['id']=='aqi')['value'],177.0)
        data['weather']['current']['wind_speed_ms']=None
        self.assertNotIn('wind',[n['id'] for n in env.build_network(data)['nodes']])
        ids={n['id'] for n in graph['nodes']}
        self.assertTrue(all(l['source'] in ids and l['target'] in ids for l in graph['links']))

    def test_source_calls_and_pipeline_state(self):
        def fetch(url):return source_fixture('weather' if 'api.open-meteo.com/v1/forecast' in url else 'air')
        with patch.object(env,'get_json',side_effect=fetch),patch.dict('os.environ',{'HEATSAFE_DISABLE_LIVE':'0'}):
            data=server.read_dashboard(live=True)
        self.assertEqual(data['weather']['status'],'live');self.assertEqual(data['air_quality']['status'],'live')
        self.assertFalse(data['pipeline']['database_writable'])
        self.assertFalse(data['pipeline']['prefect_running_on_vercel'])
        self.assertEqual(data['pipeline']['upstream_cache_seconds'],900)
        self.assertEqual(data['pipeline']['browser_poll_seconds'],300)

    def test_aqi_answer_uses_validated_data_with_citation(self):
        air=env.parse_feed(source_fixture('air'),'air',server.heat_index)
        with patch.object(server,'read_dashboard',return_value={'air_quality':air}):
            answer=server.answer_insight('What is the current AQI?')
        self.assertIn('125',answer);self.assertIn('CAMS model estimates',answer)
        self.assertIn('https://open-meteo.com/en/docs/air-quality-api',answer)
        self.assertIn('Last source pull',answer)

    def test_aqi_bands(self):
        self.assertEqual(env.aqi_category(50),'Good');self.assertEqual(env.aqi_category(51),'Moderate')
        self.assertEqual(env.aqi_category(151),'Unhealthy');self.assertEqual(env.aqi_category(None),'Not available')

    def test_current_weather_answer_and_pipeline_explanation(self):
        weather=env.parse_feed(source_fixture('weather'),'weather',server.heat_index)
        with patch.object(server,'read_dashboard',return_value={'weather':weather}):
            answer=server.answer_insight('What is the current temperature?')
        self.assertIn('32.0',answer);self.assertIn('not on-site',answer)
        self.assertIn('not running on Vercel',server.answer_insight('Is the pipeline running?'))


if __name__=='__main__':unittest.main(verbosity=2)
