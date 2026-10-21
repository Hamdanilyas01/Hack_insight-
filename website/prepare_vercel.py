"""Create a self-contained Vercel upload folder and ZIP without copying the whole repo."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WEBSITE = ROOT / 'website'
TARGET = ROOT / 'vercel-upload'
ARCHIVE = ROOT / 'heatsafe-vercel.zip'
sys.path.insert(0, str(ROOT / '.web-deps'))
sys.path.insert(0, str(WEBSITE))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    import duckdb
    import server

    if TARGET.is_symlink():
        raise RuntimeError('The upload directory must not be a symbolic link.')
    if TARGET.exists():
        marker = TARGET / 'deployment-manifest.json'
        if not marker.is_file() or json.loads(marker.read_text())['package'] != 'heatsafe-vercel':
            raise RuntimeError('Existing vercel-upload is not a generated HeatSafe package. Preserve it and choose another location.')
    # Explicit allowlist: do not copy credentials, virtualenvs, raw datasets or .git.
    files = {
        'website/vercel/app.py': 'app.py',
        'website/vercel/requirements.txt': 'requirements.txt',
        'website/vercel/.python-version': '.python-version',
        'website/vercel/.vercelignore': '.vercelignore',
        'website/vercel/vercel.json': 'vercel.json',
        'website/vercel/README.md': 'README.md',
        'website/server.py': 'backend/server.py',
        'rag/insight_agent.py': 'rag/insight_agent.py',
        'rag/knowledge_chunks.jsonl': 'rag/knowledge_chunks.jsonl',
        'rag/knowledge_manifest.json': 'rag/knowledge_manifest.json',
        'rag/source_registry.json': 'rag/source_registry.json',
        'modeling/evaluations/model_metrics_20261020T083434210943Z.json': 'modeling/evaluations/model_metrics_20261020T083434210943Z.json',
        'modeling/evaluations/risk_metrics_20261020T092105Z.json': 'modeling/evaluations/risk_metrics_20261020T092105Z.json',
    }
    for name in ('index.html', 'style.css', 'app.js', 'favicon.svg', 'guidance.json'):
        files[f'website/dist/{name}'] = f'public/{name}'
    for source in files:
        if not (ROOT / source).is_file():
            raise FileNotFoundError(source)
    # Verify the source index before building a package that will serve its answers.
    server.get_agent()
    database = ROOT / 'warehouse/heatsafe_live.duckdb'
    if database.with_suffix('.duckdb.wal').exists():
        raise RuntimeError('Close the warehouse writer and checkpoint DuckDB before packaging.')

    # Stage in a fresh directory; do not leave a partial package after errors.
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix='heatsafe-vercel-', dir=ROOT) as staging_name:
        staging = Path(staging_name)
        for source, destination in files.items():
            out = staging / destination
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / source, out)
        for package in ('backend', 'rag'):
            (staging / package / '__init__.py').write_text('', encoding='utf-8')
        (staging / 'warehouse').mkdir()
        # Hold a read-only DuckDB connection while copying so other processes cannot
        # acquire a writer lock and change the source file mid-copy.
        connection = duckdb.connect(str(database), read_only=True)
        try:
            dashboard = server.read_dashboard()
            shutil.copy2(database, staging / 'warehouse/heatsafe_live.duckdb')
        finally:
            connection.close()
        with duckdb.connect(str(staging / 'warehouse/heatsafe_live.duckdb'), read_only=True) as bundled:
            snapshot_id = bundled.execute('SELECT snapshot_id FROM gold.latest_live_ml').fetchone()[0]
        if snapshot_id != dashboard['forecast']['snapshot_id']:
            raise RuntimeError('Database changed during packaging. Retry after the pipeline completes.')
        dashboard['mode'] = 'snapshot'
        (staging / 'public/snapshot.json').write_text(
            json.dumps(dashboard, default=server.json_default, indent=2), encoding='utf-8')
        entries = {p.relative_to(staging).as_posix(): digest(p) for p in staging.rglob('*') if p.is_file()}
        manifest = {
            'package': 'heatsafe-vercel', 'built_at_utc': datetime.now(timezone.utc).isoformat(),
            'database': 'warehouse/heatsafe_live.duckdb', 'database_access': 'read-only',
            'snapshot_id': snapshot_id,
            'collected_at_utc': dashboard['freshness']['collected_at_utc'].isoformat(),
            'automatic_ingestion': False, 'files_sha256': entries,
        }
        (staging / 'deployment-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        # Only replace a prior package bearing the verified marker, within this repo.
        if TARGET.exists():
            if TARGET.resolve().parent != ROOT.resolve():
                raise RuntimeError('Upload directory resolved outside the workspace.')
            shutil.rmtree(TARGET)
        shutil.copytree(staging, TARGET)
    with zipfile.ZipFile(ARCHIVE, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(TARGET.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(TARGET).as_posix())
    print(json.dumps({
        'upload_folder': str(TARGET), 'upload_zip': str(ARCHIVE),
        'zip_size_mb': round(ARCHIVE.stat().st_size / 1024**2, 2),
        'snapshot_id': snapshot_id, 'database_runs': 'Inside the Vercel Python function, read-only',
    }, indent=2))


if __name__ == '__main__':
    main()
