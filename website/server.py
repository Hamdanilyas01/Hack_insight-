"""HeatSafe website and read-only API. Run: python website/server.py"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

if __package__:
    from .environment_data import enrich, read_air_history, timestamp
    from .city_data import cities, get_city, stored_city_dashboard
    from . import assistant
else:
    from environment_data import enrich, read_air_history, timestamp
    from city_data import cities, get_city, stored_city_dashboard
    import assistant

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / 'dist'
sys.path.insert(0, str(ROOT / '.web-deps'))
sys.path.insert(0, str(ROOT))

def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def display_time_pk(value):
    return value.astimezone(ZoneInfo('Asia/Karachi')).strftime('%d %b %Y, %I:%M %p PKT') if value else 'unavailable'

def heat_index(t, rh):
    f = t * 1.8 + 32
    simple = (0.5 * (f + 61 + (f - 68) * 1.2 + rh * .094) + f) / 2
    if simple < 80:
        return (simple - 32) / 1.8
    hi = (-42.379 + 2.04901523*f + 10.14333127*rh - .22475541*f*rh
          - .00683783*f*f - .05481717*rh*rh + .00122874*f*f*rh
          + .00085282*f*rh*rh - .00000199*f*f*rh*rh)
    if rh < 13 and 80 <= f <= 112:
        hi -= ((13-rh)/4) * (max(0,(17-abs(f-95))/17)**.5)
    if rh > 85 and 80 <= f <= 87:
        hi += ((rh-85)/10)*((87-f)/5)
    return (hi-32)/1.8

def read_dashboard(live=False, city='karachi'):
    selected = get_city(city)
    if selected['id'] != 'karachi':
        data = stored_city_dashboard(selected, heat_index, timestamp)
        data.update(city=selected, cities=cities(), exported_at=datetime.now(timezone.utc),
                    sources=json.loads((ROOT / 'rag/source_registry.json').read_text())['sources'])
        return enrich(data, data.pop('air_history'), heat_index, live=live)
    import duckdb
    connection = duckdb.connect(str(ROOT / 'warehouse/heatsafe_live.duckdb'), read_only=True)
    def query(sql):
        cursor = connection.execute(sql)
        keys = [item[0] for item in cursor.description]
        return [dict(zip(keys, row)) for row in cursor.fetchall()]
    try:
        forecast = query('SELECT * FROM gold.latest_live_ml')
        if len(forecast) != 1:
            raise ValueError('A unique forecast is unavailable')
        forecast = forecast[0]
        freshness = query('SELECT * FROM gold.live_freshness')[0]
        weather = query('SELECT * FROM gold.latest_live_weather ORDER BY time_utc')
        historical_air = read_air_history(connection)
    finally:
        connection.close()
    collected = freshness['collected_at_utc']
    age = (datetime.now(timezone.utc) - collected).total_seconds()
    valid = forecast['dq_status'] == 'PASS' and forecast['matches_latest_ingestion'] is True
    for row in weather:
        row['heat_index_c'] = round(heat_index(row['temperature_c'], row['humidity_pct']), 2)
    current = min(weather, key=lambda row: abs((row['time_utc'] - collected).total_seconds()))
    hours = [row for row in weather if row['time_utc'] >= forecast['issue_time_utc']][:25]
    dashboard = {
        'city':selected, 'cities':cities(), 'mode': 'warehouse', 'exported_at': datetime.now(timezone.utc),
        'fresh': valid and 0 <= age <= 10800, 'verified': valid,
        'forecast': forecast, 'freshness': freshness, 'current': current, 'hourly': hours,
        'metrics': json.loads((ROOT / 'modeling/evaluations/model_metrics_20261020T083434210943Z.json').read_text()),
        'risk_metrics': json.loads((ROOT / 'modeling/evaluations/risk_metrics_20261020T092105Z.json').read_text()),
        'sources': json.loads((ROOT / 'rag/source_registry.json').read_text())['sources'],
    }
    return enrich(dashboard, historical_air, heat_index, live=live)

@lru_cache(maxsize=1)
def get_agent():
    from rag.insight_agent import HeatSafeInsightAgent
    return HeatSafeInsightAgent()


def answer_insight(question, city='karachi'):
    """Extend the existing cited safety agent with clearly sourced environmental data."""
    import re
    selected = get_city(city)
    q = question.casefold()
    if re.search(r'\b(?:death\w*|hospital\w*|admission\w*|injur\w*|patient\w*)\b', q):
        return get_agent().answer(question)
    if (selected['id'] != 'karachi'
            and re.search(r'\b(?:latest|current|forecast|predicted|prediction|today|tomorrow)\b', q)
            and any(term in q for term in ['heat index', 'heat forecast', 'heat risk', 'heat level'])
            and not re.search(r'\b(?:safe|safety|protect|prevent|precaution)\b', q)
            and re.search(r'\b(?:forecast|predicted|prediction|tomorrow|model)\b', q)):
        return (f"No stored live HeatSafe ML prediction is bundled for {selected['name']}. "
                'Open Forecast for the city-specific provider weather outlook and calculated heat index. '
                'Karachi’s stored ML result is not used for other cities.')
    if re.search(r'\b(?:pipeline|prefect|update frequency)\b', q) or 'how often' in q and 'update' in q:
        return ('The original Prefect pipeline is not running on Vercel. The open website checks its API every 5 minutes. '
                'Each Python instance caches weather and AQI source responses for 15 minutes; cold starts can fetch sooner. '
                'No requests means no background refresh. DuckDB is read-only and its stored ML result only changes when '
                'you rebuild and redeploy an updated database. This is request-driven API access, not scheduled ML inference.')
    if re.search(r'\b(?:aqi|air quality|pm2[._]?5|pm10|pollution|ozone|nitrogen dioxide)\b', q):
        air = (read_dashboard(live=True) if selected['id']=='karachi' else read_dashboard(live=True, city=selected['id']))['air_quality']
        current = air['current']
        if current.get('us_aqi') is None:
            return ('Current AQI is unavailable from the live source. The project contains historical pollutant concentrations, '
                    'which must not be presented as today’s AQI. Explore Air quality to see the dated history.\n\n'
                    'Source: https://open-meteo.com/en/docs/air-quality-api')
        label = 'US AQI' if air['status'] == 'live' else 'Last available US AQI (may be outdated)'
        return (f"{selected['name']} · {label}: {current['us_aqi']:.0f} — {current['aqi_category']}\n"
                f"PM2.5: {current.get('pm2_5') if current.get('pm2_5') is not None else 'unavailable'} µg/m³\n"
                f"PM10: {current.get('pm10') if current.get('pm10') is not None else 'unavailable'} µg/m³\n"
                f"Valid time: {display_time_pk(air['valid_at_utc'])}\n"
                f"Last source pull: {display_time_pk(air['fetched_at_utc'])}\n"
                f"Data status: {air['status']}. These are CAMS model estimates, not on-site sensor readings.\n\n"
                'Source: https://open-meteo.com/en/docs/air-quality-api')
    if (re.search(r'\b(?:current|now|today|latest)\b',q)
            and re.search(r'\b(?:weather|temperature|humidity|wind|heat index)\b',q)
            and not re.search(r'\b(?:safe|safety|protect|prevent|precaution)\b',q)
            and not re.search(r'\b(?:forecast|predicted|prediction|tomorrow)\b',q)):
        weather = (read_dashboard(live=True) if selected['id']=='karachi' else read_dashboard(live=True, city=selected['id']))['weather']
        row = weather['current']
        def display(key):
            value = row.get(key)
            return f'{value:.1f}' if isinstance(value,(int,float)) else 'unavailable'
        return (f"Latest available modeled weather for {selected['name']} ({weather['status']}):\n"
                f"Air temperature: {display('temperature_c')} °C\n"
                f"Calculated heat index: {display('heat_index_c')} °C\n"
                f"Relative humidity: {display('humidity_pct')} %\n"
                f"Wind speed: {display('wind_speed_ms')} m/s\n"
                f"Valid at: {display_time_pk(weather['valid_at_utc'])}\n"
                f"Last source pull: {display_time_pk(weather['fetched_at_utc'])}\n"
                'These are modeled conditions, not on-site sensor measurements.\n\n'
                'Source: https://open-meteo.com/en/docs')
    if (selected['id'] != 'karachi'
            and re.search(r'\b(?:latest|current|forecast|predicted|prediction|today|tomorrow)\b', q)
            and any(term in q for term in ['heat index', 'heat forecast', 'heat risk'])):
        return f"No stored live HeatSafe ML prediction is bundled for {selected['name']}. Explore this city’s provider outlook in Forecast."
    return get_agent().answer(question)

def answer_chat(question, city='karachi', history=None, date_from='', date_to=''):
    selected = get_city(city)
    return assistant.answer(question, selected['id'], history, date_from, date_to,
                            read_dashboard, get_agent, cities)


def export():
    WEB.mkdir(parents=True, exist_ok=True)
    data = read_dashboard(live=True)
    data['mode'] = 'snapshot'
    (WEB / 'snapshot.json').write_text(json.dumps(data, default=json_default, indent=2), encoding='utf-8')
    export_city_snapshots()
    questions = {
        'safety': 'How can outdoor workers stay safe in the heat?',
        'hydration': 'How should workers hydrate and drink water in heat?',
        'rest': 'How do rest breaks and shade help heat safety?',
        'index': 'What is heat index and how does humidity affect it?',
        'acclimatization': 'How should workers acclimatize to heat?',
    }
    answers = {key: get_agent().answer(question) for key, question in questions.items()}
    (WEB / 'guidance.json').write_text(json.dumps(answers, indent=2), encoding='utf-8')
    print('Exported verified warehouse snapshot and existing agent guidance.')

def export_city_snapshots():
    saved = {}
    for city in cities():
        saved[city['id']] = read_dashboard(live=False, city=city['id'])
        saved[city['id']]['mode'] = 'snapshot'
    (WEB / 'city-snapshots.json').write_text(json.dumps(saved, default=json_default), encoding='utf-8')


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def send_json(self, status, body):
        encoded = json.dumps(body, default=json_default, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/dashboard':
            try:
                city = parse_qs(urlparse(self.path).query).get('city', ['karachi'])[0]
                self.send_json(200, read_dashboard(live=True, city=city))
            except ValueError as error:
                self.send_json(400, {'error':str(error)})
            except Exception as error:
                print(f'Dashboard unavailable: {error}', file=sys.stderr)
                self.send_json(503, {'error': 'Warehouse unavailable. The saved snapshot can still be explored.'})
        elif path.startswith('/api/'):
            self.send_json(404, {'error': 'Unknown endpoint'})
        else:
            super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != '/api/insight':
            self.send_json(404, {'error': 'Unknown endpoint'})
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 8192:
                self.send_json(400, {'error': 'Question is too large or empty.'})
                return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                self.send_json(400, {'error': 'Expected a JSON object with a question.'})
                return
            question = body.get('question', '')
            if not isinstance(question, str) or not question.strip() or len(question) > 1000:
                self.send_json(400, {'error': 'Enter a question between 1 and 1,000 characters.'})
                return
            self.send_json(200, answer_chat(question, city=body.get('city','karachi'), history=body.get('history'), date_from=body.get('date_from',''), date_to=body.get('date_to','')))
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {'error': 'Invalid question JSON.'})
        except Exception as error:
            print(f'Agent unavailable: {error}', file=sys.stderr)
            self.send_json(503, {'error': 'The local retrieval assistant is unavailable. Explore the saved guidance topics.'})

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--export', action='store_true')
    args = parser.parse_args()
    if args.export:
        export()
        return
    print(f'HeatSafe Pakistan: http://localhost:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()

if __name__ == '__main__':
    main()
