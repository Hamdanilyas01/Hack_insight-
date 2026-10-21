"""Validated, request-driven weather/AQI feeds and API-driven evidence nodes.

The bundled DuckDB file is never written. API caches are per Python instance,
not a durable scheduler or replacement for the project's Prefect pipeline.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from threading import Lock
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CACHE_SECONDS = 900
POLL_SECONDS = 300
SOURCE_URLS = {
    'weather': 'https://api.open-meteo.com/v1/forecast',
    'air': 'https://air-quality-api.open-meteo.com/v1/air-quality',
}
_cache = {}
_locks = {'weather': Lock(), 'air': Lock()}


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value).replace('Z', '+00:00')
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def number(value, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        return None
    return float(value)


def aqi_category(value):
    if value is None:
        return 'Not available'
    for limit, label in [(50,'Good'), (100,'Moderate'), (150,'Unhealthy for sensitive groups'),
                         (200,'Unhealthy'), (300,'Very unhealthy')]:
        if value <= limit:
            return label
    return 'Hazardous'


def category(heat):
    if heat is None:
        return 'Not available'
    f = heat * 1.8 + 32
    for limit, label in [(80,'Below caution'), (90,'Caution'), (103,'Extreme caution'), (125,'Danger')]:
        if f < limit:
            return label
    return 'Extreme danger'


def get_json(url):
    request = Request(url, headers={'User-Agent':'HeatSafe-Pakistan/1.0', 'Accept':'application/json'})
    with urlopen(request, timeout=4) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Upstream response is too large')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('error'):
        raise ValueError('Invalid upstream response')
    return data


def request_url(kind):
    params = {'latitude':24.8607, 'longitude':67.0011, 'timezone':'GMT', 'timeformat':'unixtime', 'forecast_days':3}
    if kind == 'weather':
        params.update({
            'current':'temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m,weather_code,is_day',
            'hourly':'temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m,weather_code,is_day,precipitation_probability',
            'daily':'weather_code,temperature_2m_max,temperature_2m_min', 'wind_speed_unit':'ms', 'temperature_unit':'celsius',
        })
    else:
        params.update({'current':'us_aqi,pm2_5,pm10,nitrogen_dioxide,ozone',
                       'hourly':'us_aqi,pm2_5,pm10,nitrogen_dioxide,ozone', 'domains':'cams_global'})
    return SOURCE_URLS[kind] + '?' + urlencode(params)


def parse_feed(payload, kind, heat_index):
    if payload.get('utc_offset_seconds', 0) != 0:
        raise ValueError('Expected UTC source timestamps')
    current, hourly = payload.get('current', {}), payload.get('hourly', {})
    times = hourly.get('time', [])
    if not isinstance(times, list) or not times:
        raise ValueError('Missing hourly timestamps')
    keys = ['temperature_2m','relative_humidity_2m','dew_point_2m','wind_speed_10m','weather_code','is_day','precipitation_probability'] if kind == 'weather' else ['us_aqi','pm2_5','pm10','nitrogen_dioxide','ozone']
    required = keys[:4] if kind == 'weather' else keys
    if any(not isinstance(hourly.get(k), list) or len(hourly[k]) != len(times) for k in required):
        raise ValueError('Hourly column lengths do not match')
    if kind == 'weather':
        if any(payload.get(section, {}).get('wind_speed_10m') != 'm/s' for section in ['hourly_units','current_units']):
            raise ValueError('Unsupported wind unit')
        if any(payload.get(section, {}).get('temperature_2m') != '°C' for section in ['hourly_units','current_units']):
            raise ValueError('Unsupported temperature unit')
    else:
        for key in ['pm2_5','pm10','nitrogen_dioxide','ozone']:
            for section in ['hourly_units','current_units']:
                unit = payload.get(section, {}).get(key, '').replace('μ','µ')
                if unit != 'µg/m³':
                    raise ValueError('Unsupported pollutant unit')

    def row(raw):
        result = {'time_utc':timestamp(raw['time'])}
        if kind == 'weather':
            result.update({
                'temperature_c':number(raw.get('temperature_2m'), -90, 65),
                'humidity_pct':number(raw.get('relative_humidity_2m'), 0, 100),
                'dew_point_c':number(raw.get('dew_point_2m'), -100, 65),
                'wind_speed_ms':number(raw.get('wind_speed_10m'), 0, 150),
                'weather_code':number(raw.get('weather_code'), 0, 99),
                'is_day':number(raw.get('is_day'), 0, 1),
                'precipitation_probability':number(raw.get('precipitation_probability'), 0, 100),
            })
            t, rh = result['temperature_c'], result['humidity_pct']
            result['heat_index_c'] = round(heat_index(t, rh), 2) if t is not None and rh is not None else None
        else:
            result.update({key:number(raw.get(key), 0, 10000) for key in keys})
            result['aqi_category'] = aqi_category(result['us_aqi'])
        return result

    rows = [row({'time':t, **{k:hourly[k][i] for k in keys if isinstance(hourly.get(k), list) and len(hourly[k]) == len(times)}}) for i,t in enumerate(times)]
    if any(b['time_utc'] <= a['time_utc'] for a,b in zip(rows, rows[1:])):
        raise ValueError('Unsorted or duplicate source timestamps')
    current_row = row(current)
    if kind == 'weather' and (current_row['temperature_c'] is None or current_row['humidity_pct'] is None):
        raise ValueError('Current weather failed validation')
    if kind == 'air' and not any(current_row[k] is not None for k in keys):
        raise ValueError('Current air-quality fields unavailable')
    now = utcnow()
    rows = [r for r in rows if r['time_utc'] >= current_row['time_utc'].replace(minute=0, second=0, microsecond=0)][:49]
    if len(rows) < 2:
        raise ValueError('Insufficient future hourly data')
    valid_age = (now-current_row['time_utc']).total_seconds()
    result = {
        'status':'live' if -3600 <= valid_age <= 10800 else 'stale',
        'source':'Open-Meteo weather models' if kind == 'weather' else 'CAMS Global via Open-Meteo',
        'source_url':'https://open-meteo.com/en/docs' if kind == 'weather' else 'https://open-meteo.com/en/docs/air-quality-api',
        'measurement_type':'modeled', 'fetched_at_utc':now, 'valid_at_utc':current_row['time_utc'],
        'cache_hit':False, 'current':current_row, 'hourly':rows,
        'note':'Model-based conditions, not a reading from an on-site sensor.',
    }
    if kind == 'weather':
        daily = payload.get('daily', {})
        result['daily'] = []
        for i,t in enumerate(daily.get('time', [])):
            def daily_value(key):
                values = daily.get(key, [])
                return number(values[i]) if len(values) > i else None
            result['daily'].append({'time_utc':timestamp(t), 'weather_code':daily_value('weather_code'),
                                    'max_c':daily_value('temperature_2m_max'), 'min_c':daily_value('temperature_2m_min')})
    else:
        result['standard'] = 'US AQI'
        result['pollutant_unit'] = 'µg/m³'
    return result


def cached_feed(kind, heat_index):
    with _locks[kind]:
        now = time.monotonic()
        entry = _cache.get(kind)
        if entry and now < entry['retry_at']:
            result = deepcopy(entry['data']) if entry.get('data') else None
            if result:
                result['cache_hit'] = True
                if entry.get('failed'):
                    result['status'] = 'cached_stale'
                    result['note'] = 'Latest refresh failed. Showing the last successful API response.'
                elif not -3600 <= (utcnow()-result['valid_at_utc']).total_seconds() <= 10800:
                    result['status'] = 'stale'
            return result
        try:
            result = parse_feed(get_json(request_url(kind)), kind, heat_index)
            _cache[kind] = {'data':deepcopy(result), 'retry_at':time.monotonic()+CACHE_SECONDS, 'failed':False}
            return result
        except Exception:
            previous = entry.get('data') if entry else None
            _cache[kind] = {'data':previous, 'retry_at':time.monotonic()+60, 'failed':True}
            if previous:
                result = deepcopy(previous)
                result.update(status='cached_stale', cache_hit=True, note='Latest refresh failed. Showing the last successful API response.')
                return result
            return None


def read_air_history(connection):
    try:
        cursor = connection.execute('''
            SELECT time_utc, pm2_5, pm10, nitrogen_dioxide, ozone
            FROM cleaned.cams_air_quality WHERE dq_status = 'PASS'
            ORDER BY time_utc DESC LIMIT 48
        ''')
        columns = [c[0] for c in cursor.description]
        hourly = [dict(zip(columns, row)) for row in cursor.fetchall()][::-1]
        cursor = connection.execute('''
            SELECT date_trunc('month', timezone('UTC', time_utc)) AT TIME ZONE 'UTC' AS time_utc,
                   avg(pm2_5) AS pm2_5, avg(pm10) AS pm10,
                   avg(nitrogen_dioxide) AS nitrogen_dioxide, avg(ozone) AS ozone
            FROM cleaned.cams_air_quality WHERE dq_status = 'PASS'
            GROUP BY 1 ORDER BY 1 DESC LIMIT 12
        ''')
        monthly = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()][::-1]
        return {'hourly':hourly, 'monthly':monthly, 'source':'CAMS Global historical project data',
                'source_url':'https://open-meteo.com/en/docs/air-quality-api', 'pollutant_unit':'µg/m³'}
    except Exception:
        return {'hourly':[], 'monthly':[], 'source':'Historical air data unavailable', 'pollutant_unit':'µg/m³'}


def enrich(data, historical_air, heat_index, live=False):
    weather = {
        'status':'stored', 'source':'Stored Open-Meteo weather snapshot',
        'source_url':'https://open-meteo.com/en/docs', 'measurement_type':'modeled',
        'fetched_at_utc':data['freshness']['collected_at_utc'], 'valid_at_utc':data['current']['time_utc'],
        'current':data['current'], 'hourly':data['hourly'], 'daily':[], 'cache_hit':False,
        'note':'Live weather API unavailable or disabled. Showing dated warehouse evidence.',
    }
    history = historical_air['hourly']
    last = history[-1] if history else {}
    air = {
        'status':'historical' if history else 'unavailable', 'source':historical_air['source'],
        'source_url':'https://open-meteo.com/en/docs/air-quality-api', 'measurement_type':'modeled',
        'fetched_at_utc':None, 'valid_at_utc':last.get('time_utc'),
        'current':{**last, 'us_aqi':None, 'aqi_category':'No current AQI'}, 'hourly':[],
        'standard':'US AQI', 'pollutant_unit':'µg/m³', 'cache_hit':False,
        'note':'Current AQI unavailable. Historical pollutant concentrations are not a current AQI index.',
    }
    if live and os.getenv('HEATSAFE_DISABLE_LIVE') != '1':
        with ThreadPoolExecutor(max_workers=2) as pool:
            weather_future = pool.submit(cached_feed, 'weather', heat_index)
            air_future = pool.submit(cached_feed, 'air', heat_index)
            weather = weather_future.result() or weather
            air = air_future.result() or air
    data.update({
        'weather':weather, 'air_quality':air, 'air_history':historical_air,
        'server_response_at_utc':utcnow(),
        'pipeline':{
            'prefect_running_on_vercel':False, 'scheduled_cloud_job':False,
            'update_mode':'request-driven' if live else 'stored-snapshot',
            'browser_poll_seconds':POLL_SECONDS, 'upstream_cache_seconds':CACHE_SECONDS,
            'database_writable':False, 'database_updates':'Rebuild and redeploy to update the bundled DuckDB file.',
            'ml_updates':'The 24-hour ML result comes from the stored pipeline run; this API does not rerun the model.',
            'cache_note':'Cache is per Python instance. Cold starts may fetch sooner; no requests means no refresh.',
        },
    })
    data['network'] = build_network(data)
    return data


def build_network(data):
    """Node presence, values, links, timestamps and counts follow the actual payload."""
    nodes, links = [], []
    weather, air = data['weather'], data['air_quality']
    forecast, metrics = data['forecast'], data['metrics']
    def add(id, group, label, value=None, unit='', parent=None, source='', when=None, note='', status='available', view='overview'):
        nodes.append({'id':id, 'group':group, 'label':label, 'value':value, 'unit':unit,
                      'source':source, 'time_utc':when, 'description':note, 'status':status, 'view':view,
                      'parent':parent})
        if parent:
            links.append({'source':parent, 'target':id})
    add('karachi','location','Karachi', 'Pakistan', note='Weather, air quality and project evidence in one connected view.')
    add('weather','weather','Weather', parent='karachi', source=weather['source'], when=weather['valid_at_utc'], status=weather['status'], view='forecast')
    for id,key,label,unit in [('temperature','temperature_c','Temperature','°C'),('heatindex','heat_index_c','Feels like · heat index','°C'),('humidity','humidity_pct','Humidity','%'),('wind','wind_speed_ms','Wind speed','m/s')]:
        value = weather['current'].get(key)
        if value is not None:
            add(id,'weather',label,value,unit,'weather',weather['source'],weather['valid_at_utc'],
                'Heat index is calculated from air temperature and humidity.' if id=='heatindex' else weather['note'], weather['status'],'forecast')
    add('air','air','Air quality', parent='karachi', source=air['source'], when=air['valid_at_utc'], status=air['status'], view='air')
    for id,key,label,unit in [('aqi','us_aqi','US AQI',''),('pm25','pm2_5','PM2.5','µg/m³'),('pm10','pm10','PM10','µg/m³'),('no2','nitrogen_dioxide','Nitrogen dioxide','µg/m³'),('ozone','ozone','Ozone','µg/m³')]:
        value = air['current'].get(key)
        if value is not None:
            add(id,'air',label,value,unit,'air',air['source'],air['valid_at_utc'],air['current'].get('aqi_category') if id=='aqi' else air['note'],air['status'],'air')
    add('forecast','forecast','Stored ML forecast',forecast.get('model_prediction_c'),'°C','karachi','DuckDB · trained heat-index model',forecast['target_time_utc'],
        'A single 24-hour ML prediction from the stored pipeline. This is separate from the live formula forecast.', 'verified' if data['fresh'] else 'stale','forecast')
    for id,key,label in [('lower','interval_lower_c','Interval lower bound'),('upper','interval_upper_c','Interval upper bound')]:
        if forecast.get(key) is not None:
            add(id,'forecast',label,forecast[key],'°C','forecast','Historical interval calibration',forecast['target_time_utc'],'Historical calibration is not a guarantee of live coverage.','available','forecast')
    add('evidence','evidence','Model evidence',parent='karachi',source='Held-out test evaluation',view='evidence')
    for id,key,label,unit in [('mae','test_model_mae_c','Model test error','°C'),('r2','test_model_r2','Model test R²','')]:
        if metrics.get(key) is not None:
            add(id,'evidence',label,metrics[key],unit,'evidence','Project evaluation files',note='Historical test-set performance; not a live weather reading.',view='evidence')
    add('safety','safety','Safety guidance',len(data['sources']),'sources','karachi','Verified retrieval index',note='Select a source or ask a heat-safety question.',view='evidence')
    for source in data['sources']:
        label = source['publisher'].replace('UK Health and Safety Executive','UK HSE').replace('NOAA National Weather Service','NOAA NWS')
        add(source['source_id'],'safety',label,parent='safety',source=source['url'],note=source['title'],view='evidence')
    return {'nodes':nodes, 'links':links}
