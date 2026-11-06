"""Compare the saved baseline, prompt revision and model trial without API calls."""
import csv
import json
from pathlib import Path

from integrations.vision.analyse import ANALYSIS_DIRECTORY


def compare_pilot():
    variants = [('lite_v1',ANALYSIS_DIRECTORY/'evaluation/pilot_evaluation.json'),
                ('lite_v2',ANALYSIS_DIRECTORY/'evaluation/lite_v2/pilot_evaluation.json'),
                ('flash_v2',ANALYSIS_DIRECTORY/'evaluation/flash_v2/pilot_evaluation.json')]
    data={name:json.loads(path.read_text()) for name,path in variants}
    expected={r['image_id'] for r in data['lite_v1']['images']}
    rows=[]
    for name, result in data.items():
        if result['source_classes']!=data['lite_v1']['source_classes'] or result['prediction_aliases']!=data['lite_v1']['prediction_aliases']:
            raise ValueError('Evaluation rules differ across variants')
        by_image={r['image_id']:r for r in result['images']}
        for image_id in sorted(expected):
            r=by_image.get(image_id)
            s=r['raw_diagnostic'] if r else None
            rows.append({'variant':name,'model':result['model_id'],'prompt_version':result['prompt_version'],
                         'image_id':image_id,'status':r['pipeline_status'] if r else 'not_attempted',
                         'gold_boxes':r['gold_box_count'] if r else None,
                         'tp':s['tp'] if s else None,'fp':s['fp'] if s else None,'fn':s['fn'] if s else None,
                         'precision':s['precision'] if s else None,'recall':s['recall'] if s else None,
                         'mean_matched_iou':s['mean_matched_iou'] if s else None,
                         'unscored_predictions':len(s['unscored_predictions']) if s else None,
                         'group_ignored_predictions':len(s['group_ignored_predictions']) if s else None,
                         'cost_usd':r['cost_usd'] if r else None})
    # Compare detection quality only on identical images with returned output in both arms.
    a={r['image_id']:r for r in data['lite_v1']['images'] if r['raw_diagnostic'] is not None}
    b={r['image_id']:r for r in data['flash_v2']['images'] if r['raw_diagnostic'] is not None}
    shared=sorted(a.keys() & b.keys())
    paired={}
    for name, collection in [('lite_v1',a),('flash_v2',b)]:
        tp,fp,fn=(sum(collection[k]['raw_diagnostic'][metric] for k in shared) for metric in ('tp','fp','fn'))
        paired[name]={'images':shared,'tp':tp,'fp':fp,'fn':fn,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None}
    output=ANALYSIS_DIRECTORY/'experiments/prompt_model_v2'
    output.mkdir(parents=True,exist_ok=True)
    report={'rows':rows,'same_returned_images_comparison':paired,
            'known_new_cost_usd':sum(r['cost_usd'] or 0 for r in data['flash_v2']['images'])+sum(r['cost_usd'] or 0 for r in data['lite_v2']['images']),
            'new_attempts_with_unknown_cost':sum(r['cost_usd'] is None for k in ('lite_v2','flash_v2') for r in data[k]['images']),
            'notes':['Same frozen class aliases and IoU threshold across arms.',
                     'Flash-Lite v2 returned no outputs due to provider rate limits; prompt-only effect cannot be measured.',
                     'Not a holdout test; these are the same three development images.',
                     'No automatic duplicate suppression or coordinate repair was applied.']}
    (output/'comparison.json').write_text(json.dumps(report,indent=2))
    with (output/'per_image.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))
    return report


if __name__=='__main__':
    compare_pilot()
