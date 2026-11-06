"""Analyse explicitly selected synced images once and save review artifacts.

This is a three-image pilot CLI, not an automatic full-library processor.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import time
import uuid

import httpx
from PIL import Image, ImageDraw, ImageFont
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.llm.image_analysis import (
    PROMPTS, DEFAULT_ANALYSIS_MODEL, PREPARATION_VERSION, PROMPT_VERSION,
    ImageAnalysis, analyse_image, parse_analysis,
)
from app.llm.base import LLMError
from app.services.image_storage import decode_image
from integrations.google_drive.sync import CACHE_DIRECTORY, SyncStore, cache_valid
from rag.vector_store import connect_rag_database

ANALYSIS_DIRECTORY = CACHE_DIRECTORY / 'analysis'
MAX_PILOT_CALLS = 3


def prepare_image(path: Path):
    """Orient and strip metadata; normalized boxes refer to this exact raster."""
    raw = path.read_bytes()
    image = decode_image(raw).convert('RGB')
    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    clean = Image.new('RGB', image.size)
    clean.paste(image)
    buffer = BytesIO()
    clean.save(buffer, format='PNG')
    prepared = buffer.getvalue()
    return clean, prepared, 'data:image/png;base64,' + base64.b64encode(prepared).decode('ascii')


def pixel_box(box_2d, width, height):
    ymin, xmin, ymax, xmax = box_2d
    # Half-open crop coordinates: right/bottom may equal width/height.
    return [max(0, math.floor(xmin * width / 1000)), max(0, math.floor(ymin * height / 1000)),
            min(width, math.ceil(xmax * width / 1000)), min(height, math.ceil(ymax * height / 1000))]


def render_boxes(image, analysis: ImageAnalysis, destination: Path):
    preview = image.copy()
    draw = ImageDraw.Draw(preview)
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 16)
    except OSError:
        font = ImageFont.load_default()
    colours = ['#e63946', '#0066ff', '#009955', '#a13dde', '#df7800', '#008899']
    for number, obj in enumerate(analysis.objects, 1):
        x1, y1, x2, y2 = pixel_box(obj.box_2d, *preview.size)
        colour = colours[(number - 1) % len(colours)]
        draw.rectangle((x1, y1, min(x2, preview.width - 1), min(y2, preview.height - 1)), outline=colour, width=3)
        label = f'{number}. {obj.label}'
        bounds = draw.textbbox((0, 0), label, font=font)
        w, h = bounds[2] - bounds[0] + 8, bounds[3] - bounds[1] + 8
        left = max(0, min(x1, preview.width - w))
        top = max(0, y1 - h)
        draw.rectangle((left, top, left + w, top + h), fill=colour)
        draw.text((left + 4, top + 2), label, fill='white', font=font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    preview.save(destination, format='PNG')


def ensure_analysis_table(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS rag.image_analyses (
        owner_id INTEGER NOT NULL, file_id TEXT NOT NULL, content_sha256 TEXT NOT NULL,
        model_id TEXT NOT NULL, prompt_version TEXT NOT NULL, preparation_version TEXT NOT NULL,
        status TEXT NOT NULL, analysis JSONB, usage JSONB NOT NULL DEFAULT '{}'::jsonb,
        response_id TEXT, cost_usd DOUBLE PRECISION, error_message TEXT,
        attempt_id TEXT NOT NULL, artifact_directory TEXT NOT NULL,
        width INTEGER NOT NULL, height INTEGER NOT NULL,
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ,
        PRIMARY KEY(owner_id,file_id,content_sha256,model_id,prompt_version,preparation_version)
    )''')
    connection.commit()


def analysis_key(owner_id, row, model, prompt_version=PROMPT_VERSION):
    return (owner_id, row['file_id'], row['content_sha256'], model, prompt_version, PREPARATION_VERSION)


KEY_WHERE = 'owner_id=%s AND file_id=%s AND content_sha256=%s AND model_id=%s AND prompt_version=%s AND preparation_version=%s'


async def model_price(model):
    """No authentication/billing: verify current model capability and price before calls."""
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get('https://openrouter.ai/api/v1/models')
        response.raise_for_status()
    selected = next((m for m in response.json()['data'] if m['id'] == model), None)
    if not selected or 'image' not in selected['architecture']['input_modalities']:
        raise ValueError('Selected image model is unavailable')
    if not {'structured_outputs', 'response_format'} <= set(selected['supported_parameters']):
        raise ValueError('Selected model does not advertise structured outputs')
    pricing = selected['pricing']
    # Explicit model comparison only; no provider/model fallback.
    limits = {DEFAULT_ANALYSIS_MODEL: (0.0000001, 0.0000004),
              'google/gemini-2.5-flash': (0.0000003, 0.0000025)}
    rates = (float(pricing['prompt']), float(pricing['completion']))
    if (model not in limits or any(not math.isfinite(rate) or rate < 0 for rate in rates)
            or rates[0] > limits[model][0] or rates[1] > limits[model][1]):
        raise ValueError('Pilot model/price guard failed; review pricing before changing the model')
    return {'model': model, 'pricing': pricing, 'checked_at': datetime.now(timezone.utc).isoformat()}


def usage_cost(usage):
    cost = usage.get('cost')
    if cost is None:
        return None
    value = float(cost)
    if not math.isfinite(value) or value < 0:
        raise ValueError('Invalid reported provider cost')
    return value


def error_diagnostic(exc):
    """Keep useful failure metadata without saving headers, keys or request images."""
    from app.config import settings
    diagnostic = {'error_type': type(exc).__name__}
    if isinstance(exc, LLMError):
        diagnostic['message'] = str(exc)[:700]
    cause = exc.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        diagnostic['http_status'] = cause.response.status_code
        try:
            data = cause.response.json()
            diagnostic['request_id'] = data.get('id') or data.get('request_id')
            error = data.get('error') or {}
            if isinstance(error, dict):
                diagnostic['provider_error_code'] = error.get('code')
                diagnostic['provider_message'] = error.get('message')
                metadata = error.get('metadata') or {}
                if isinstance(metadata, dict):
                    diagnostic['limit_source'] = metadata.get('limit_source')
            diagnostic['retry_after'] = cause.response.headers.get('retry-after')
        except (ValueError, AttributeError):
            pass
    # Only scalar metadata is retained, and defensive redaction covers unexpected IDs/messages.
    for key, value in list(diagnostic.items()):
        if value is not None and not isinstance(value, (str, int, float, bool)):
            diagnostic[key] = '[omitted]'
        if isinstance(value, str) and ('data:' in value or 'Bearer' in value
                                       or (settings.openrouter_api_key and settings.openrouter_api_key in value)):
            diagnostic[key] = '[redacted]'
    return diagnostic


async def process_image(connection, owner_id, row, model, *, requester=None, prompt_version=PROMPT_VERSION):
    key = analysis_key(owner_id, row, model, prompt_version)
    existing = connection.execute(f'SELECT status,artifact_directory FROM rag.image_analyses WHERE {KEY_WHERE}', key).fetchone()
    connection.commit()
    if existing:
        if existing[0] == 'succeeded':
            return {'file_id': row['file_id'], 'status': 'cached', 'artifact_directory': existing[1], 'paid_calls': 0}
        # A failed/timed-out attempt might already have been billed. Never silently repeat it.
        raise ValueError('An unsuccessful analysis attempt already exists; inspect it before explicitly retrying')
    if not cache_valid(row):
        raise ValueError('Image cache is missing or changed; run Drive sync first')
    image, prepared, data_url = prepare_image(Path(row['cache_path']))
    attempt_id = uuid.uuid4().hex
    folder = ANALYSIS_DIRECTORY / str(owner_id) / row['file_id'] / attempt_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'prepared.png').write_bytes(prepared)
    (folder / 'prompt.txt').write_text(PROMPTS[prompt_version])
    provenance = {'source_sha256': row['content_sha256'], 'prepared_sha256': hashlib.sha256(prepared).hexdigest(),
                  'model_id': model, 'prompt_version': prompt_version, 'preparation_version': PREPARATION_VERSION,
                  'width': image.width, 'height': image.height, 'input': 'image only; no documents or evaluation labels',
                  'coordinates': '[ymin,xmin,ymax,xmax], integer 0..1000, on EXIF-oriented prepared image'}
    (folder / 'provenance.json').write_text(json.dumps(provenance, indent=2))
    connection.execute('''INSERT INTO rag.image_analyses
        (owner_id,file_id,content_sha256,model_id,prompt_version,preparation_version,status,
         attempt_id,artifact_directory,width,height) VALUES (%s,%s,%s,%s,%s,%s,'processing',%s,%s,%s,%s)''',
        (*key, attempt_id, str(folder.resolve()), image.width, image.height))
    connection.commit()  # Durable claim before sending the potentially billable request.
    body, usage, cost = None, {}, None
    started = time.perf_counter()
    try:
        body = (await requester(data_url, model) if requester is not None else
                await analyse_image(data_url, model, prompt_version=prompt_version))
        # Preserve provider usage and raw output even if validation fails. No data URL/key stored.
        (folder / 'response.json').write_text(json.dumps(body, indent=2))
        usage = body.get('usage') or {}
        cost = usage_cost(usage)
        analysis = parse_analysis(body, prompt_version=prompt_version)
        (folder / 'analysis.json').write_text(analysis.model_dump_json(indent=2))
        render_boxes(image, analysis, folder / 'boxes.png')
        connection.execute(f'''UPDATE rag.image_analyses SET status='succeeded',analysis=%s,usage=%s,
            response_id=%s,cost_usd=%s,completed_at=now() WHERE {KEY_WHERE}''',
            (Jsonb(analysis.model_dump()), Jsonb(usage), body.get('id'), cost, *key))
        # Never mark a newer source version completed if sync changed it while inference ran.
        updated = connection.execute('''UPDATE rag.drive_sync_files SET vlm_status='completed',updated_at=now()
            WHERE owner_id=%s AND file_id=%s AND content_sha256=%s AND status='cached' ''',
            (owner_id, row['file_id'], row['content_sha256']))
        connection.commit()
        return {'file_id': row['file_id'], 'name': row['name'], 'status': 'succeeded',
                'current_source': updated.rowcount == 1, 'object_count': len(analysis.objects),
                'cost_usd': cost, 'paid_calls': 1, 'latency_seconds': round(time.perf_counter() - started, 2),
                'artifact_directory': str(folder.resolve())}
    except Exception as exc:
        connection.rollback()
        diagnostic = error_diagnostic(exc)
        (folder / 'error.json').write_text(json.dumps(diagnostic, indent=2))
        # Keep raw provider text in local private artifacts, not a displayed exception.
        connection.execute(f'''UPDATE rag.image_analyses SET status='failed',usage=%s,response_id=%s,
            cost_usd=%s,error_message=%s,completed_at=now() WHERE {KEY_WHERE}''',
            (Jsonb(usage), body.get('id') if body else None, cost, json.dumps(diagnostic)[:1000], *key))
        connection.execute('''UPDATE rag.drive_sync_files f SET vlm_status=CASE WHEN EXISTS (
                SELECT 1 FROM rag.image_analyses a WHERE a.owner_id=f.owner_id AND a.file_id=f.file_id
                AND a.content_sha256=f.content_sha256 AND a.status='succeeded') THEN 'completed' ELSE 'error' END,
            updated_at=now() WHERE owner_id=%s AND file_id=%s AND content_sha256=%s AND status='cached' ''',
            (owner_id, row['file_id'], row['content_sha256']))
        connection.commit()
        raise


async def run_pilot(owner_id, file_ids, *, model=DEFAULT_ANALYSIS_MODEL, prompt_version=PROMPT_VERSION):
    if prompt_version not in PROMPTS:
        raise ValueError("Unknown prompt version")
    if owner_id < 1 or not 1 <= len(file_ids) <= MAX_PILOT_CALLS or len(set(file_ids)) != len(file_ids):
        raise ValueError('Select one to three unique synced image IDs for an existing user')
    with connect_rag_database() as connection:
        SyncStore(connection, owner_id)
        ensure_analysis_table(connection)
        # Use the same owner lock as sync, preventing source changes by this workflow during the pilot.
        acquired = connection.execute("SELECT pg_try_advisory_lock(hashtext('drive-sync'),%s)", (owner_id,)).fetchone()[0]
        connection.commit()
        if not acquired:
            raise RuntimeError('Another sync or analysis is already running for this user')
        try:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute('''SELECT * FROM rag.drive_sync_files WHERE owner_id=%s
                    AND file_id=ANY(%s) AND kind='images' AND status='cached' ''', (owner_id, file_ids))
                rows = {r['file_id']: r for r in cursor.fetchall()}
            connection.commit()
            if len(rows) != len(file_ids):
                raise ValueError('Every selected ID must be a currently cached image owned by this user')
            pricing = await model_price(model)
            results = []
            for file_id in file_ids:
                key = analysis_key(owner_id, rows[file_id], model, prompt_version)
                prior = connection.execute(f'SELECT status,artifact_directory FROM rag.image_analyses WHERE {KEY_WHERE}', key).fetchone()
                connection.commit()
                try:
                    result = await process_image(connection, owner_id, rows[file_id], model, prompt_version=prompt_version)
                except Exception as exc:
                    connection.rollback()
                    failed = connection.execute(f'''SELECT status,artifact_directory,cost_usd
                        FROM rag.image_analyses WHERE {KEY_WHERE}''', key).fetchone()
                    connection.commit()
                    if not failed or failed[0] != 'failed':
                        raise
                    result = {'file_id': file_id, 'name': rows[file_id]['name'], 'status': 'failed',
                              'artifact_directory': failed[1], 'cost_usd': failed[2],
                              'paid_calls': 0 if prior else 1, 'error_type': type(exc).__name__,
                              'automatic_retry': False}
                results.append(result)
                print(json.dumps(result), flush=True)
                # A missing charge is not assumed free: stop further calls until reviewed.
                if result['paid_calls'] and (result['cost_usd'] is None or result['cost_usd'] > 0.01):
                    break
            known_costs = [r['cost_usd'] for r in results if r['paid_calls'] and r.get('cost_usd') is not None]
            report = {'pricing': pricing, 'prompt_version': prompt_version, 'results': results,
                      'paid_calls': sum(r['paid_calls'] for r in results),
                      'reported_cost_usd': sum(known_costs),
                      'all_new_costs_reported': all(r.get('cost_usd') is not None for r in results if r['paid_calls'])}
            report_path = ANALYSIS_DIRECTORY / f'pilot_{owner_id}_{uuid.uuid4().hex}.json'
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2))
            return report
        finally:
            connection.rollback()
            connection.execute("SELECT pg_advisory_unlock(hashtext('drive-sync'),%s)", (owner_id,))
            connection.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-id', type=int, required=True)
    parser.add_argument('--file-id', action='append', required=True, help='Repeat for at most three images')
    parser.add_argument('--model', default=DEFAULT_ANALYSIS_MODEL, choices=[DEFAULT_ANALYSIS_MODEL, 'google/gemini-2.5-flash'])
    parser.add_argument('--prompt-version', default=PROMPT_VERSION, choices=list(PROMPTS))
    args = parser.parse_args()
    report = asyncio.run(run_pilot(args.owner_id, args.file_id, model=args.model, prompt_version=args.prompt_version))
    print(json.dumps({'paid_calls': report['paid_calls'], 'reported_cost_usd': report['reported_cost_usd']}, indent=2))
    if any(result['status'] == 'failed' for result in report['results']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
