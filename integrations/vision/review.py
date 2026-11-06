"""Build an offline HTML review of stored VLM attempts; never call a model."""
import argparse
from html import escape
import json
import re
from pathlib import Path

from PIL import Image
from psycopg.rows import dict_row
from pydantic import ValidationError

from app.llm.image_analysis import DEFAULT_ANALYSIS_MODEL, PROMPT_VERSION, ImageAnalysis, ProductObservation
from integrations.vision.analyse import ANALYSIS_DIRECTORY, render_boxes
from rag.vector_store import connect_rag_database


def box_iou(a, b):
    y1, x1, y2, x2 = a
    v1, u1, v2, u2 = b
    intersection = max(0, min(x2, u2) - max(x1, u1)) * max(0, min(y2, v2) - max(y1, v1))
    union = (y2-y1)*(x2-x1) + (v2-v1)*(u2-u1) - intersection
    return intersection / union if union else 0


def build_review(owner_id, *, model=DEFAULT_ANALYSIS_MODEL, prompt_version=PROMPT_VERSION, tag=None):
    if tag is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", tag):
        raise ValueError("Invalid review tag")
    suffix = f"_{tag}" if tag else ""
    with connect_rag_database() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('''SELECT a.*,f.name,f.content_sha256 AS current_sha
                FROM rag.image_analyses a JOIN rag.drive_sync_files f
                ON a.owner_id=f.owner_id AND a.file_id=f.file_id
                WHERE a.owner_id=%s AND a.model_id=%s AND a.prompt_version=%s AND a.status IN ('succeeded','failed') ORDER BY a.started_at''', (owner_id,model,prompt_version))
            rows = cursor.fetchall()
    if not rows:
        raise ValueError('No image-analysis attempts to review')
    cards, records = [], []
    root = ANALYSIS_DIRECTORY.resolve()
    for row in rows:
        folder = Path(row['artifact_directory']).resolve()
        if not folder.is_relative_to(root):
            raise ValueError('Analysis artifact is outside the analysis directory')
        response_path = folder / 'response.json'
        parsed = None
        if response_path.exists():
            body = json.loads(response_path.read_text())
            try:
                parsed = json.loads(body['choices'][0]['message']['content'])
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        valid, rejected, duplicates = [], [], []
        if isinstance(parsed, dict):
            for index, obj in enumerate(parsed.get('objects', []), 1):
                try:
                    valid.append((index, ProductObservation.model_validate(obj)))
                except ValidationError as exc:
                    rejected.append({'prediction': index, 'label': obj.get('label'), 'box_2d': obj.get('box_2d'),
                                     'reasons': [e['msg'] for e in exc.errors(include_input=False)]})
            for index, first in valid:
                for other, second in valid:
                    if index < other and first.label == second.label and box_iou(first.box_2d, second.box_2d) > 0.7:
                        duplicates.append([index, other])
        preview = folder / ('boxes.png' if row['status'] == 'succeeded' else 'prepared.png')
        if row['status'] != 'succeeded' and parsed and valid:
            # Diagnostic overlay only. Failed output remains excluded from accepted analyses.
            diagnostic = ImageAnalysis(scene_description=parsed['scene_description'], topics=parsed['topics'],
                                       objects=[o for _, o in valid], uncertainties=parsed['uncertainties'])
            with Image.open(folder / 'prepared.png') as image:
                render_boxes(image, diagnostic, folder / 'diagnostic_boxes.png')
            preview = folder / 'diagnostic_boxes.png'
        current = row['current_sha'] == row['content_sha256']
        record = {'file_id': row['file_id'], 'name': row['name'], 'status': row['status'], 'current_source': current,
                  'model_id': row['model_id'], 'prompt_version': row['prompt_version'],
                  'cost_usd': row['cost_usd'], 'returned_objects': len(parsed.get('objects', [])) if parsed else None,
                  'valid_box_count': len(valid), 'rejected_predictions': rejected,
                  'possible_duplicate_pairs': duplicates, 'artifact_directory': str(folder)}
        (folder / 'review_notes.json').write_text(json.dumps(record, indent=2))
        records.append(record)
        cost = f"${row['cost_usd']:.7f}" if row['cost_usd'] is not None else 'not reported (not assumed zero)'
        observations = ''.join(f"<tr><td>{index}</td><td>{escape(obj.label)}</td><td>{escape(obj.description)}</td>"
                               f"<td>{escape(str(obj.box_2d))}</td><td>{escape(obj.uncertainty)}</td></tr>" for index,obj in valid)
        warning = ('Schema-valid output; accuracy still needs human review.' if row['status'] == 'succeeded' else
                   'FAILED: not available for automatic selection. Any boxes below are diagnostic predictions only.')
        notes = escape(json.dumps({'rejected_predictions': rejected, 'possible_duplicate_pairs': duplicates}, indent=2))
        description = escape(parsed.get('scene_description', 'No model analysis returned.')) if parsed else 'No model analysis returned.'
        cards.append(f'''<section><h2>{escape(row['name'])}</h2>
<p><strong>{warning}</strong></p><p>Model: {escape(row['model_id'])} · Reported charge: {cost}</p>
<p>{description}</p><img src="{escape(preview.relative_to(root).as_posix(), quote=True)}" alt="Image with model-predicted boxes or original prepared image">
<details><summary>Product observations and boxes</summary><table><thead><tr><th>#</th><th>Label</th><th>Description</th><th>Box yxyx / 1000</th><th>Uncertainty</th></tr></thead><tbody>{observations}</tbody></table></details>
<details><summary>Rejected boxes and possible duplicates</summary><pre>{notes}</pre></details></section>''')
    report = {'owner_id': owner_id, 'attempt_count': len(rows),
              'schema_valid': sum(r['status']=='succeeded' for r in rows),
              'reported_cost_usd': sum(r['cost_usd'] or 0 for r in rows),
              'unknown_cost_attempts': sum(r['cost_usd'] is None for r in rows), 'attempts': records}
    (root / f'pilot_review_{owner_id}{suffix}.json').write_text(json.dumps(report, indent=2))
    html = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Image-analysis pilot review</title><style>
body{{font:16px/1.5 system-ui,sans-serif;background:#f2f4f7;color:#182330;margin:0;padding:24px;max-width:1120px;margin:auto}}
section{{background:white;padding:24px;margin:24px 0;border:1px solid #ccd4df;border-radius:12px}}img{{display:block;max-width:100%;height:auto;margin:16px auto}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:8px;border:1px solid #ccd4df;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}summary{{cursor:pointer;font-weight:600;padding:8px 0}}
</style><h1>Image-analysis pilot: three different scenes</h1>
<p>Image-only input. No campaign documents or expected labels were supplied. Boxes are model predictions, not ground truth.</p>
<p>{report['attempt_count']} attempts · {report['schema_valid']} schema-valid · Reported charges ${report['reported_cost_usd']:.7f} · {report['unknown_cost_attempts']} attempt(s) with unknown charge.</p>
{''.join(cards)}</html>'''
    target = root / f'pilot_review_{owner_id}{suffix}.html'
    target.write_text(html)
    print(target)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-id', type=int, required=True)
    parser.add_argument('--model', default=DEFAULT_ANALYSIS_MODEL)
    parser.add_argument('--prompt-version', default=PROMPT_VERSION)
    parser.add_argument('--tag')
    args = parser.parse_args()
    report = build_review(args.owner_id, model=args.model, prompt_version=args.prompt_version, tag=args.tag)
    print(json.dumps({k:v for k,v in report.items() if k != 'attempts'}, indent=2))
