"""Test the actual upload package, without importing the original backend or RAG."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'vercel-upload'
sys.path.insert(0, str(BUNDLE))

import duckdb
from fastapi.testclient import TestClient
import app as deployment
from backend import server
from rag import insight_agent


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(deployment.app)
        cls.db = BUNDLE / 'warehouse/heatsafe_live.duckdb'
        cls.database_hash = hashlib.sha256(cls.db.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        if hashlib.sha256(cls.db.read_bytes()).hexdigest() != cls.database_hash:
            raise AssertionError('An API request changed the deployment database.')

    def test_package_uses_its_own_database_and_rag(self):
        self.assertEqual(server.ROOT, BUNDLE)
        self.assertEqual(insight_agent.DATABASE, self.db)
        self.assertEqual(insight_agent.KNOWLEDGE, BUNDLE / 'rag/knowledge_chunks.jsonl')
        self.assertEqual(Path(deployment.__file__).parent, BUNDLE)

    def test_bundle_and_zip_integrity(self):
        manifest = json.loads((BUNDLE / 'deployment-manifest.json').read_text())
        with zipfile.ZipFile(ROOT / 'heatsafe-vercel.zip') as archive:
            for name, expected in manifest['files_sha256'].items():
                self.assertEqual(hashlib.sha256((BUNDLE / name).read_bytes()).hexdigest(), expected, name)
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected, name)
            self.assertIn('app.py', archive.namelist())
            self.assertIn('warehouse/heatsafe_live.duckdb', archive.namelist())
            self.assertFalse(any('/.git/' in n or '.venv/' in n or '__pycache__' in n or n.endswith(('.env','.key','.pem')) for n in archive.namelist()))

    def test_health_checks_real_retrieval_and_warehouse(self):
        with patch.dict(os.environ, {'VERCEL':'1'}):
            result = self.client.get('/api/health')
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual(data['storage']['location'], 'vercel')
        self.assertEqual(data['storage']['access'], 'read-only')
        self.assertFalse(data['storage']['automatic_ingestion'])
        self.assertEqual(data['retrieval']['knowledge_chunks'], 17)
        self.assertFalse(data['retrieval']['generative_model'])

    def test_dashboard_matches_bundled_gold_data(self):
        real_connect = duckdb.connect
        def readonly_connect(*args, **kwargs):
            self.assertIs(kwargs.get('read_only'), True)
            return real_connect(*args, **kwargs)
        with patch.object(duckdb, 'connect', side_effect=readonly_connect):
            result = self.client.get('/api/dashboard')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers['cache-control'], 'no-store')
        data = result.json()
        self.assertEqual(data['mode'], 'warehouse')
        with real_connect(str(self.db), read_only=True) as db:
            predicted = db.execute('SELECT model_prediction_c FROM gold.latest_live_ml').fetchone()[0]
        self.assertEqual(data['forecast']['model_prediction_c'], predicted)
        self.assertEqual(len(data['hourly']), 25)

    def test_real_guidance_retrieval(self):
        for question, citation in [
            ('How should outdoor workers stay safe in the heat?', 'https://www.cdc.gov/niosh/'),
            ('What is heat index and how does humidity affect it?', 'https://www.weather.gov/'),
        ]:
            result = self.client.post('/api/insight', json={'question':question})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['mode'], 'retrieval')
            self.assertIn(citation, result.json()['answer'])

    def test_forecast_freshness_guard_is_preserved(self):
        from datetime import datetime, timedelta, timezone
        import pandas as pd
        # Keep this test meaningful even if the local pipeline updates after packaging.
        with duckdb.connect(str(self.db), read_only=True) as db:
            frame = db.execute('SELECT * FROM gold.latest_live_ml').df()
        frame['collected_at_utc'] = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=2))
        class Result:
            def df(self): return frame
        class Connection:
            def execute(self, sql): return Result()
            def close(self): pass
        with patch.object(insight_agent.duckdb, 'connect', return_value=Connection()):
            result = self.client.post('/api/insight', json={'question':'What is the latest predicted heat index?'})
        self.assertEqual(result.status_code, 200)
        self.assertIn('stale', result.json()['answer'])

    def test_unsupported_questions_stay_unsupported(self):
        for question in ['What is the current AQI?', 'How many hospital admissions were there?', 'Who won the football match?']:
            result = self.client.post('/api/insight', json={'question':question})
            self.assertEqual(result.status_code, 200)
            self.assertIn("I don't know", result.json()['answer'])

    def test_invalid_and_oversized_questions(self):
        for value in ['', None, [], 4, 'x'*1001]:
            self.assertEqual(self.client.post('/api/insight', json={'question':value}).status_code, 400)
        for raw in ['[]', 'null', 'not json', '42']:
            self.assertEqual(self.client.post('/api/insight', content=raw).status_code, 400)
        self.assertEqual(self.client.post('/api/insight', content=b'x'*8200).status_code, 400)

    def test_static_files_and_private_resource_boundaries(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertIn('HeatSafe Pakistan', self.client.get('/').text)
        for path in ['app.js','style.css','snapshot.json','guidance.json','favicon.svg']:
            self.assertEqual(self.client.get('/'+path).status_code, 200)
        for path in ['warehouse/heatsafe_live.duckdb','rag/knowledge_chunks.jsonl','backend/server.py','app.py','.env','requirements.txt','deployment-manifest.json','api/unknown']:
            self.assertEqual(self.client.get('/'+path).status_code, 404, path)

    def test_knowledge_tamper_check_is_preserved(self):
        with TemporaryDirectory() as directory:
            bad_index = Path(directory) / 'knowledge.jsonl'
            bad_index.write_bytes(insight_agent.KNOWLEDGE.read_bytes()+b'\n')
            with patch.object(insight_agent, 'KNOWLEDGE', bad_index):
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    insight_agent.load_knowledge()

    def test_backend_failure_does_not_masquerade_as_live_data(self):
        with patch.object(deployment, 'read_dashboard', side_effect=RuntimeError('unavailable')):
            result = self.client.get('/api/dashboard')
            self.assertEqual(result.status_code, 503)
            self.assertIn('error', result.json())
            self.assertEqual(self.client.get('/api/health').status_code, 503)


if __name__ == '__main__':
    unittest.main(verbosity=2)
