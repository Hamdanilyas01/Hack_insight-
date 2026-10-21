"""Read the existing city feed without changing pipelines or trained models."""
from functools import lru_cache
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def city_feed():
    return json.loads((ROOT / 'dashboard_data/multi_city_forecast.json').read_text(encoding='utf-8'))


def cities():
    # Preserve Karachi and add only cities with entries in the user's data CSV.
    with (ROOT / 'data/city_era5_manifest.csv').open(encoding='utf-8-sig', newline='') as source:
        allowed = {'karachi'} | {row['city'].strip().casefold() for row in csv.DictReader(source)}
    return [{'id':row['city'].casefold(), 'name':row['city'], 'region':row['region'],
             'latitude':row['latitude'], 'longitude':row['longitude']}
            for row in city_feed()['cities'] if row['city'].casefold() in allowed]


def get_city(value='karachi'):
    if isinstance(value, str):
        for city in cities():
            if city['id'] == value.strip().casefold():
                return city
    raise ValueError('Choose a supported city.')


def stored_city_dashboard(city, heat_index, timestamp):
    feed = city_feed()
    source = next(row for row in feed['cities'] if row['city'] == city['name'])
    rows = []
    for row in source['hours']:
        if row.get('dq_status') != 'PASS':
            continue
        t, rh = row.get('temperature_2m_c'), row.get('relative_humidity_pct')
        if not isinstance(t, (float, int)) or not isinstance(rh, (float, int)):
            continue
        rows.append({'time_utc':timestamp(row['forecast_time_utc']), 'temperature_c':t,
                     'humidity_pct':rh, 'heat_index_c':round(heat_index(t, rh), 2),
                     'wind_speed_ms':None, 'dew_point_c':None, 'weather_code':None, 'is_day':None})
    rows.sort(key=lambda row:row['time_utc'])
    collected = timestamp(feed['generated_at_utc'])
    current = rows[0] if rows else {'time_utc':collected, 'temperature_c':None,
                                  'humidity_pct':None, 'heat_index_c':None, 'wind_speed_ms':None}
    metrics_path = ROOT / f"modeling/evaluations/{city['id']}_model_metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else {}
    return {
        'mode':'warehouse', 'fresh':False, 'verified':False,
        'forecast':{'available':False, 'target_time_utc':None, 'model_prediction_c':None,
                    'interval_lower_c':None, 'interval_upper_c':None, 'model_risk_category':'Unavailable'},
        'freshness':{'collected_at_utc':collected}, 'current':current, 'hourly':rows,
        'metrics':metrics, 'risk_metrics':{'accuracy':metrics.get('risk_classification_accuracy')},
        'air_history':{'daily':[], 'hourly':[], 'monthly':[],
                       'source':f"No bundled air-quality archive for {city['name']}", 'pollutant_unit':'µg/m³'},
    }
