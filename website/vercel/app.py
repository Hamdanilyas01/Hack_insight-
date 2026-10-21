"""Vercel FastAPI entrypoint. Copied to the upload folder by prepare_vercel.py."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse

from backend.server import get_agent, read_dashboard

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / 'public'
STATIC_FILES = frozenset({
    'index.html', 'style.css', 'app.js', 'favicon.svg', 'snapshot.json', 'guidance.json'
})
agent_lock = Lock()
logger = logging.getLogger('heatsafe')
app = FastAPI(title='HeatSafe Pakistan API', docs_url=None, redoc_url=None, openapi_url=None)


def storage_info():
    return {
        'engine': 'DuckDB',
        'location': 'vercel' if os.environ.get('VERCEL') else 'deployment',
        'access': 'read-only',
        'data_updates': 'Rebuild the upload package and redeploy to replace the database snapshot.',
        'automatic_ingestion': False,
    }


def response(body, status=200):
    return JSONResponse(jsonable_encoder(body), status_code=status, headers={'Cache-Control': 'no-store'})


@app.get('/api/health')
def health():
    """Verify the real database and knowledge index; never silently use JSON fallback."""
    try:
        data = read_dashboard()
        with agent_lock:
            chunks = len(get_agent().chunks)
        return response({
            'status': 'ok', 'backend': 'python-fastapi', 'storage': storage_info(),
            'forecast_collected_at': data['freshness']['collected_at_utc'],
            'forecast_fresh': data['fresh'], 'forecast_verified': data['verified'],
            'retrieval': {'status': 'ready', 'knowledge_chunks': chunks, 'generative_model': False},
        })
    except Exception:
        logger.exception('Health check failed')
        return response({'status': 'unavailable', 'error': 'Database or verified knowledge index unavailable.'}, 503)


@app.get('/api/dashboard')
def dashboard():
    try:
        data = read_dashboard()
        data['storage'] = storage_info()
        return response(data)
    except Exception:
        logger.exception('Dashboard evidence could not be read')
        return response({'error': 'Warehouse unavailable. The saved snapshot can still be explored.'}, 503)


@app.post('/api/insight')
async def insight(request: Request):
    # Bound both declared and streamed bodies, including requests without Content-Length.
    length = request.headers.get('content-length')
    if length is not None:
        try:
            if not 0 < int(length) <= 8192:
                return response({'error': 'Question body must be between 1 and 8,192 bytes.'}, 400)
        except ValueError:
            return response({'error': 'Invalid content length.'}, 400)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            return response({'error': 'Question body exceeds 8,192 bytes.'}, 400)
    try:
        import json
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return response({'error': 'Expected a JSON object with a question.'}, 400)
    if not isinstance(payload, dict):
        return response({'error': 'Expected a JSON object with a question.'}, 400)
    question = payload.get('question')
    if not isinstance(question, str) or not question.strip() or len(question) > 1000:
        return response({'error': 'Enter a question between 1 and 1,000 characters.'}, 400)
    try:
        # Scientific retrieval and DuckDB are synchronous. Keep them off the ASGI loop.
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(answer_question, question.strip())
        return response({'answer': result, 'mode': 'retrieval', 'storage': storage_info()})
    except Exception:
        logger.exception('Retrieval failed')
        return response({'error': 'The retrieval assistant is unavailable. Try again or read the official evidence sources.'}, 503)


def answer_question(question):
    with agent_lock:
        return get_agent().answer(question)


@app.get('/', include_in_schema=False)
def index():
    return FileResponse(PUBLIC / 'index.html')


@app.get('/{filename}', include_in_schema=False)
def static_file(filename: str):
    # Local preview fallback only. Vercel serves public/ from its CDN automatically.
    # No general file-serving route: the bundled database and Python stay private.
    if filename not in STATIC_FILES:
        raise HTTPException(status_code=404, detail='Not found')
    return FileResponse(PUBLIC / filename)
