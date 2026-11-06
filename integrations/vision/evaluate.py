"""Offline, annotation-scoped detection diagnostics for saved VLM attempts.

This is not official Open Images mAP: no calibrated confidence ranking, full
class hierarchy expansion, or group-of weighting is claimed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from html import escape
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image
from pydantic import ValidationError
from scipy.optimize import linear_sum_assignment

from app.llm.image_analysis import ProductObservation
from integrations.vision.analyse import ANALYSIS_DIRECTORY
from integrations.vision.review import box_iou

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANNOTATIONS = PROJECT_ROOT / 'data/vision/matched_campaigns/reference_annotations.json'

# Fixed product scope, defined independently of which predictions matched.
# Broad environment/parent labels (Furniture, Building, Plant, Tableware, etc.)
# are outside this diagnostic scope. Prefer Couch to its Studio couch subtype
# and Houseplant to Plant so the same physical item is not counted twice.
SOURCE_CLASSES = {
    'Computer monitor': 'monitor', 'Laptop': 'laptop', 'Tablet computer': 'tablet',
    'Computer keyboard': 'keyboard', 'Computer mouse': 'mouse', 'Microphone': 'microphone',
    'Desk': 'desk', 'Lamp': 'lamp', 'Bottle': 'bottle', 'Wine glass': 'wine_glass',
    'Chair': 'chair', 'Couch': 'sofa', 'Coffee table': 'coffee_table',
    'Houseplant': 'houseplant', 'Flowerpot': 'flowerpot', 'Teapot': 'teapot',
    'Kettle': 'kettle', 'Bowl': 'bowl', 'Mug': 'mug', 'Coffee cup': 'cup',
    'Knife': 'knife', 'Fork': 'fork', 'Spoon': 'spoon', 'Vase': 'vase',
}
ALIASES = {
    'monitor': 'monitor', 'computer monitor': 'monitor', 'desktop computer monitor': 'monitor',
    'laptop': 'laptop', 'laptop computer': 'laptop', 'tablet': 'tablet', 'tablet computer': 'tablet',
    'keyboard': 'keyboard', 'computer keyboard': 'keyboard',
    'mouse': 'mouse', 'computer mouse': 'mouse', 'microphone': 'microphone',
    'desk': 'desk', 'office desk': 'desk', 'desk lamp': 'lamp', 'table lamp': 'lamp', 'lamp': 'lamp',
    'bottle': 'bottle', 'wine bottle': 'bottle', 'wine glass': 'wine_glass',
    'chair': 'chair', 'armchair': 'chair', 'sofa': 'sofa', 'couch': 'sofa', 'studio couch': 'sofa',
    'coffee table': 'coffee_table', 'houseplant': 'houseplant', 'potted plant': 'houseplant',
    'flowerpot': 'flowerpot', 'flower pot': 'flowerpot', 'plant pot': 'flowerpot',
    'teapot': 'teapot', 'kettle': 'kettle', 'electric kettle': 'kettle', 'bowl': 'bowl',
    'mug': 'mug', 'coffee mug': 'mug', 'cup': 'cup', 'coffee cup': 'cup',
    'knife': 'knife', 'fork': 'fork', 'spoon': 'spoon', 'vase': 'vase',
}


def canonical_label(label):
    return ALIASES.get(re.sub(r'\s+', ' ', label.strip().lower()))


def source_box(annotation):
    return [float(annotation[k]) * 1000 for k in ('YMin', 'XMin', 'YMax', 'XMax')]


def ground_truth(record):
    boxes, groups, excluded = [], [], []
    for number, ann in enumerate(record['source_annotations'], 1):
        label = ann['label']
        reason = None
        if float(ann.get('Confidence', 1)) != 1:
            reason = 'not verified positive'
        elif ann.get('IsDepiction') == '1' or ann.get('IsInside') == '1':
            reason = 'depiction or inside-object annotation'
        elif label not in SOURCE_CLASSES:
            reason = 'outside fixed product scope or redundant broad/subtype class'
        if reason:
            excluded.append({'annotation': number, 'label': label, 'reason': reason})
            continue
        item = {'id': number, 'label': label, 'canonical': SOURCE_CLASSES[label], 'box_2d': source_box(ann)}
        (groups if ann.get('IsGroupOf') == '1' else boxes).append(item)
    # Teapot and Kettle sometimes annotate the same physical object. Prefer
    # the more specific teapot only when their boxes substantially coincide.
    retained = []
    for box in boxes:
        duplicate = (box['canonical'] == 'kettle' and any(
            other['canonical'] == 'teapot' and box_iou(box['box_2d'], other['box_2d']) >= 0.9
            for other in boxes))
        if duplicate:
            excluded.append({'annotation': box['id'], 'label': box['label'], 'reason': 'overlapping Teapot/Kettle duplicate; Teapot preferred'})
        else:
            retained.append(box)
    return retained, groups, excluded


def intersection_over_prediction(prediction, group):
    y1,x1,y2,x2 = prediction
    v1,u1,v2,u2 = group
    area = (y2-y1)*(x2-x1)
    intersection = max(0,min(y2,v2)-max(y1,v1))*max(0,min(x2,u2)-max(x1,u1))
    return intersection/area if area>0 else 0


def score_predictions(predictions, gold, groups=(), threshold=0.5):
    """Maximum-cardinality one-to-one matching, then maximum IoU.

    Only source-positive classes are scored. Other detections remain explicitly
    unscored because verified negative image labels are not loaded here.
    Invalid boxes in an annotated class count as false positives, never repaired.
    """
    if not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError('IoU threshold must be finite and in (0,1]')
    classes = {g['canonical'] for g in gold} | {g['canonical'] for g in groups}
    considered, unscored = [], []
    for number, raw in enumerate(predictions, 1):
        label = canonical_label(raw.get('label', ''))
        item = {'prediction': number, 'label': raw.get('label'), 'canonical': label, 'box_2d': raw.get('box_2d')}
        if label not in classes:
            item['reason'] = 'class not positively annotated in this image, or no defined alias'
            unscored.append(item)
            continue
        try:
            ProductObservation.model_validate(raw)
            item['valid'] = True
        except ValidationError:
            item['valid'] = False
        considered.append(item)
    valid = [p for p in considered if p['valid']]
    matched = []
    if valid and gold:
        overlaps = np.array([[box_iou(p['box_2d'], g['box_2d']) if p['canonical']==g['canonical'] else 0
                              for g in gold] for p in valid])
        eligible = overlaps >= threshold
        # Reward a valid edge by more than any possible sum of IoUs.
        utility = np.where(eligible, len(valid)+len(gold)+1+overlaps, 0)
        pi, gi = linear_sum_assignment(-utility)
        for p_index,g_index in zip(pi,gi):
            if eligible[p_index,g_index]:
                matched.append({'prediction':valid[p_index]['prediction'], 'annotation':gold[g_index]['id'],
                                'label':gold[g_index]['canonical'], 'iou':float(overlaps[p_index,g_index])})
    matched_p = {m['prediction'] for m in matched}
    matched_g = {m['annotation'] for m in matched}
    ignored_groups, false_positives = [], []
    for pred in considered:
        if pred['prediction'] in matched_p:
            continue
        group_match = pred['valid'] and any(g['canonical']==pred['canonical'] and
            intersection_over_prediction(pred['box_2d'],g['box_2d'])>=0.5 for g in groups)
        if group_match:
            ignored_groups.append(pred)
        else:
            best = max((box_iou(pred['box_2d'],g['box_2d']) for g in gold
                        if g['canonical']==pred['canonical']), default=0) if pred['valid'] else None
            pred['best_same_class_iou'] = best
            pred['reason'] = ('invalid box' if not pred['valid'] else
                              'duplicate: qualifying gold already matched' if best >= threshold else
                              'no same-class gold box with IoU >= threshold')
            false_positives.append(pred)
    false_negatives = [g for g in gold if g['id'] not in matched_g]
    tp,fp,fn = len(matched),len(false_positives),len(false_negatives)
    return {'tp':tp,'fp':fp,'fn':fn,'precision':tp/(tp+fp) if tp+fp else None,
            'recall':tp/(tp+fn) if tp+fn else None,
            'mean_matched_iou':sum(m['iou'] for m in matched)/tp if tp else None,
            'matches':matched,'false_positives':false_positives,'false_negatives':false_negatives,
            'unscored_predictions':unscored,'group_ignored_predictions':ignored_groups}


def evaluate(owner_id, review_path=None, *, output_directory=None):
    review_path = review_path or ANALYSIS_DIRECTORY / f'pilot_review_{owner_id}.json'
    review = json.loads(review_path.read_text())
    references = {r['image_id']:r for r in json.loads(ANNOTATIONS.read_text())}
    reports = []
    for attempt in review['attempts']:
        if attempt['status'] not in {'succeeded','failed'}:
            raise ValueError('Only completed attempts can be evaluated')
        if not attempt['current_source']:
            raise ValueError('Review has stale source hashes; rebuild before evaluation')
        image_id = Path(attempt['name']).stem
        reference = references[image_id]
        folder = Path(attempt['artifact_directory'])
        provenance = json.loads((folder/'provenance.json').read_text())
        # Labels refer to the original upright Open Images image. Check that
        # EXIF did not rotate or swap axes rather than assuming compatibility.
        original = PROJECT_ROOT / 'data/vision/matched_campaigns/images' / attempt['name']
        if hashlib.sha256(original.read_bytes()).hexdigest()!=provenance['source_sha256']:
            raise ValueError('Evaluation image differs from the source actually analysed')
        with Image.open(original) as im:
            if im.getexif().get(274,1) not in (None,1):
                raise ValueError('EXIF transform requires transforming gold boxes before scoring')
            original_ratio = im.width/im.height
        if abs(provenance['width']/provenance['height']-original_ratio)>0.003:
            raise ValueError('Prepared/source aspect ratio mismatch')
        gold, groups, excluded = ground_truth(reference)
        output = {'image_id':image_id,'name':attempt['name'],'pipeline_status':attempt['status'],
                  'artifact_directory':str(folder),
                  'gold_box_count':len(gold),'gold_boxes':gold,'group_boxes':groups,'excluded_annotations':excluded,
                  'iou_threshold':0.5,'cost_usd':attempt['cost_usd']}
        response_path = folder/'response.json'
        predictions = None
        if attempt.get('detector_output_path'):
            detector_path=Path(attempt['detector_output_path']).resolve()
            if not detector_path.is_relative_to(folder.resolve()):
                raise ValueError('Detector output must be inside its artifact directory')
            predictions=json.loads(detector_path.read_text())['objects']
            if not isinstance(predictions,list):
                raise ValueError('Detector objects must be a list')
        elif response_path.exists():
            body = json.loads(response_path.read_text())
            try:
                parsed = json.loads(body['choices'][0]['message']['content'])
                predictions = parsed['objects']
                if not isinstance(predictions,list):
                    raise ValueError('Objects must be a list')
            except (KeyError,IndexError,TypeError,ValueError):
                predictions = None
        output['raw_output_available'] = predictions is not None
        output['raw_diagnostic'] = score_predictions(predictions,gold,groups) if predictions is not None else None
        # Failed output is not accepted into the pipeline, even when a raw subset matches.
        output['accepted_pipeline'] = score_predictions(predictions if attempt['status']=='succeeded' and predictions is not None else [], gold,groups)
        reports.append(output)
    scored = [r['raw_diagnostic'] for r in reports if r['raw_diagnostic'] is not None]
    tp,fp,fn = (sum(s[key] for s in scored) for key in ('tp','fp','fn'))
    result = {'scope':'Annotation-positive product subset; not official Open Images mAP or full-scene precision',
              'model_id':review['attempts'][0]['model_id'],'prompt_version':review['attempts'][0]['prompt_version'],
              'source_classes':SOURCE_CLASSES,'prediction_aliases':ALIASES,
              'raw_available_images':len(scored),'total_attempts':len(reports),
              'raw_micro':{'tp':tp,'fp':fp,'fn':fn,'precision':tp/(tp+fp) if tp+fp else None,
                           'recall':tp/(tp+fn) if tp+fn else None},
              'images':reports,'paid_model_calls':0}
    output_dir = Path(output_directory) if output_directory else ANALYSIS_DIRECTORY/'evaluation'
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/'pilot_evaluation.json').write_text(json.dumps(result,indent=2))
    with (output_dir/'per_image.csv').open('w',newline='') as stream:
        fields=['image_id','pipeline_status','gold_boxes','raw_available','tp','fp','fn','precision','recall','mean_matched_iou','unscored','ignored_group_predictions']
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        for r in reports:
            s=r['raw_diagnostic']
            writer.writerow({'image_id':r['image_id'],'pipeline_status':r['pipeline_status'],'gold_boxes':r['gold_box_count'],
                'raw_available':r['raw_output_available'],**({k:s[k] for k in ('tp','fp','fn','precision','recall','mean_matched_iou')} if s else {}),
                'unscored':len(s['unscored_predictions']) if s else '',
                'ignored_group_predictions':len(s['group_ignored_predictions']) if s else ''})
    print(json.dumps({'raw_micro':result['raw_micro'],'images':[
        {'image_id':r['image_id'],'gold':r['gold_box_count'],'raw':({k:r['raw_diagnostic'][k] for k in ('tp','fp','fn','precision','recall','mean_matched_iou')} if r['raw_diagnostic'] else None)} for r in reports]},indent=2))
    return result


def render_evaluation(result, *, output_directory=None):
    """Save source-label versus prediction comparisons; no model calls."""
    from app.llm.image_analysis import ImageAnalysis
    from integrations.vision.analyse import render_boxes
    directory=Path(output_directory) if output_directory else ANALYSIS_DIRECTORY/'evaluation'
    sections=[]
    def percentage(value):
        return 'N/A' if value is None else f'{100*value:.1f}%'
    for row in result['images']:
        folder=Path(row['artifact_directory'])
        with Image.open(folder/'prepared.png') as image:
            gold_analysis=ImageAnalysis(scene_description='Source Open Images product annotations.',
                topics=['source annotations'],objects=[ProductObservation(label=g['label'],
                    description='Source annotation.',attributes=[],uncertainty='',
                    box_2d=[round(v) for v in g['box_2d']]) for g in row['gold_boxes']],uncertainties=[])
            gold_path=directory/f"{row['image_id']}_gold.png"
            render_boxes(image,gold_analysis,gold_path)
        prediction_path=folder/('boxes.png' if row['pipeline_status']=='succeeded' else 'diagnostic_boxes.png')
        if not prediction_path.exists():
            prediction_path=folder/'prepared.png'
        import os
        pred_relative=Path(os.path.relpath(prediction_path,directory))
        score=row['raw_diagnostic']
        mean_iou=f"{score['mean_matched_iou']:.3f}" if score and score['mean_matched_iou'] is not None else 'N/A'
        summary=(f"TP {score['tp']} · FP {score['fp']} · FN {score['fn']} · "
                 f"Precision {percentage(score['precision'])} · Recall {percentage(score['recall'])} · "
                 f"Mean matched IoU {mean_iou}" if score is not None else
                 'No predictions returned: detection precision and localization IoU cannot be calculated.')
        detail=json.dumps({'raw_diagnostic':score,'accepted_pipeline':row['accepted_pipeline'],
                           'excluded_annotations':row['excluded_annotations'],'group_annotations':row['group_boxes']},indent=2)
        sections.append(f'''<section><h2>{escape(row['name'])}</h2>
<p>Pipeline status: <strong>{escape(row['pipeline_status'])}</strong> · {row['gold_box_count']} individual product labels.</p>
<p>{summary}</p><div class="comparison"><figure><figcaption>Open Images labels in the fixed product scope</figcaption><img src="{gold_path.name}" alt="Source product boxes"></figure>
<figure><figcaption>{'Model-predicted boxes' if row['pipeline_status']=='succeeded' else 'Diagnostic predictions only; failed result is not accepted. Invalid boxes are omitted from this overlay.'}</figcaption><img src="{escape(pred_relative.as_posix(),quote=True)}" alt="Predictions or original image when no output exists"></figure></div>
<details><summary>Matches, misses, false positives and unscored predictions</summary><pre>{escape(detail)}</pre></details></section>''')
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VLM label evaluation</title>
<style>body{font:16px/1.5 system-ui,sans-serif;background:#f2f4f7;color:#182330;max-width:1500px;margin:auto;padding:24px}section{background:white;border:1px solid #ccd4df;border-radius:12px;padding:20px;margin:24px 0}.comparison{display:grid;grid-template-columns:1fr 1fr;gap:16px}figure{margin:0}img{width:100%;height:auto}figcaption{font-weight:600;min-height:3em}summary{cursor:pointer;font-weight:600;margin-top:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:850px){.comparison{grid-template-columns:1fr}}</style>
<h1>VLM predictions versus existing Open Images labels</h1><p>IoU threshold ≥ 0.5, one-to-one same-class matching. Diagnostic product subset, not official Open Images mAP. Unannotated classes are unscored; group-box matches are ignored. No new model calls.</p>
<p>Invalid lounge predictions are scored only as raw diagnostics. The pipeline rejects that entire result. Tea API failure is reported separately from detection quality.</p>'''+''.join(sections)+'</html>'
    target=directory/'comparison.html'
    target.write_text(html)
    print(target)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-id',type=int,required=True)
    parser.add_argument('--review-path', type=Path)
    parser.add_argument('--output-dir', type=Path)
    args=parser.parse_args()
    render_evaluation(evaluate(args.owner_id,args.review_path,output_directory=args.output_dir),output_directory=args.output_dir)
