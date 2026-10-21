"""Evidence and API contract checks for the new website."""
import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import server

class EvidenceTests(unittest.TestCase):
    def test_export_matches_existing_ml_evidence(self):
        snapshot = json.loads((server.WEB / 'snapshot.json').read_text())
        original = json.loads((server.ROOT / 'data/processed/live_ml/karachi_live_20261021T090009626947Z_ml_24h.json').read_text())
        self.assertAlmostEqual(snapshot['forecast']['model_prediction_c'], original['model_prediction_c'], places=3)
        self.assertEqual(snapshot['forecast']['snapshot_id'], original['snapshot_id'])
        self.assertEqual(snapshot['mode'], 'snapshot')
        self.assertEqual(len(snapshot['hourly']), 25)
        self.assertTrue(all(row['dq_status'] == 'PASS' for row in snapshot['hourly']))

    def test_formula_matches_project_test_vector(self):
        # NWS reference example: 90 F air temperature at 70% humidity.
        self.assertAlmostEqual(server.heat_index((90-32)/1.8, 70)*1.8+32, 105.92, places=1)
        self.assertLess(server.heat_index(20, 50), 22)

    def test_saved_guidance_is_cited(self):
        guidance = json.loads((server.WEB / 'guidance.json').read_text())
        self.assertIn('https://www.cdc.gov/niosh/', guidance['hydration'])
        self.assertIn('https://www.weather.gov/', guidance['index'])
        self.assertIn('not a substitute', guidance['safety'])

class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=10)
        conn.request(method, path, body=body, headers={'Content-Type':'application/json'})
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_api_reads_warehouse(self):
        code, body = self.request('GET', '/api/dashboard')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['mode'], 'warehouse')

    def test_question_validation(self):
        for value in ['', [], 'x'*1001]:
            code, _ = self.request('POST', '/api/insight', json.dumps({'question':value}))
            self.assertEqual(code, 400)
        code, _ = self.request('POST', '/api/insight', 'not JSON')
        self.assertEqual(code, 400)
        for body in ['[]', 'null', '42']:
            code, _ = self.request('POST', '/api/insight', body)
            self.assertEqual(code, 400)

    def test_warehouse_failure_is_explicit(self):
        with patch.object(server, 'read_dashboard', side_effect=RuntimeError('test unavailable')):
            code, body = self.request('GET', '/api/dashboard')
        self.assertEqual(code, 503)
        self.assertIn('unavailable', json.loads(body)['error'])

    def test_unsupported_agent_question(self):
        code, body = self.request('POST', '/api/insight', json.dumps({'question':'What is the current AQI?'}))
        self.assertEqual(code, 200)
        self.assertIn("I don't know", json.loads(body)['answer'])

    def test_static_server_does_not_expose_database(self):
        for path in ['/warehouse/heatsafe_live.duckdb', '/server.py', '/api/unknown']:
            code, _ = self.request('GET', path)
            self.assertEqual(code, 404)

if __name__ == '__main__':
    unittest.main()
