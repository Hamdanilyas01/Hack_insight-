"""HeatSafe website and read-only API. Run: python website/server.py"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / 'dist'
sys.path.insert(0, str(ROOT / '.web-deps'))
sys.path.insert(0, str(ROOT))

def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)

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

def read_dashboard():
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
    finally:
        connection.close()
    collected = freshness['collected_at_utc']
    age = (datetime.now(timezone.utc) - collected).total_seconds()
    valid = forecast['dq_status'] == 'PASS' and forecast['matches_latest_ingestion'] is True
    for row in weather:
        row['heat_index_c'] = round(heat_index(row['temperature_c'], row['humidity_pct']), 2)
    current = min(weather, key=lambda row: abs((row['time_utc'] - collected).total_seconds()))
    hours = [row for row in weather if row['time_utc'] >= forecast['issue_time_utc']][:25]
    return {
        'mode': 'warehouse', 'exported_at': datetime.now(timezone.utc),
        'fresh': valid and 0 <= age <= 10800, 'verified': valid,
        'forecast': forecast, 'freshness': freshness, 'current': current, 'hourly': hours,
        'metrics': json.loads((ROOT / 'modeling/evaluations/model_metrics_20261020T083434210943Z.json').read_text()),
        'risk_metrics': json.loads((ROOT / 'modeling/evaluations/risk_metrics_20261020T092105Z.json').read_text()),
        'sources': json.loads((ROOT / 'rag/source_registry.json').read_text())['sources'],
    }

@lru_cache(maxsize=1)
def get_agent():
    from rag.insight_agent import HeatSafeInsightAgent
    return HeatSafeInsightAgent()

def export():
    WEB.mkdir(parents=True, exist_ok=True)
    data = read_dashboard()
    data['mode'] = 'snapshot'
    (WEB / 'snapshot.json').write_text(json.dumps(data, default=json_default, indent=2), encoding='utf-8')
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
                self.send_json(200, read_dashboard())
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
            self.send_json(200, {'answer': get_agent().answer(question), 'mode': 'retrieval'})
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
