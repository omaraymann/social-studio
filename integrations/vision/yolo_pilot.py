"""Local YOLOv8n Open Images pilot on the three saved VLM inputs.

No training, full dataset download, Google Drive requests, or OpenRouter calls.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time

from PIL import Image

from app.llm.image_analysis import ProductObservation
from integrations.vision.analyse import ANALYSIS_DIRECTORY, render_boxes
from integrations.vision.evaluate import evaluate, render_evaluation

CHECKPOINT_NAME = 'yolov8n-oiv7.pt'
RUN_VERSION = 'yolov8n_oiv7_img640_conf025_nms070_cpu_v1'
PARAMETERS = {'imgsz':640, 'conf':0.25, 'iou':0.7, 'device':'cpu',
              'max_det':300, 'agnostic_nms':False, 'augment':False, 'seed':0}


@dataclass
class DetectorOverlay:
    objects: list[ProductObservation]


def normalized_detection(label, xyxy, width, height):
    """Convert image-coordinate XYXY to normalized integer YXYX without stretching."""
    if width <= 0 or height <= 0 or len(xyxy) != 4:
        raise ValueError('Invalid image dimensions or XYXY box')
    x1,y1,x2,y2 = map(float,xyxy)
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError('Detector box lies outside its source image or has no area')
    observation = {'label':label, 'description':f'Pretrained detector category: {label}.',
                   'attributes':[], 'uncertainty':'',
                   'box_2d':[round(y1/height*1000),round(x1/width*1000),
                             round(y2/height*1000),round(x2/width*1000)]}
    ProductObservation.model_validate(observation)
    return observation


def run_yolo_pilot(owner_id=1):
    directory=ANALYSIS_DIRECTORY/'detector'/RUN_VERSION
    directory.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('YOLO_CONFIG_DIR',str((ANALYSIS_DIRECTORY/'detector/settings').resolve()))
    from ultralytics import YOLO, __version__ as ultralytics_version, settings
    import torch
    settings.update({'sync':False})
    checkpoint=ANALYSIS_DIRECTORY/'detector/models'/CHECKPOINT_NAME
    checkpoint.parent.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    model=YOLO(str(checkpoint))
    if len(model.names)<600 or not {'Microphone','Desk','Computer monitor','Bowl'} <= set(model.names.values()):
        raise ValueError('Checkpoint is not the expected Open Images category model')
    load_seconds=time.perf_counter()-started
    checkpoint_sha=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    training_metadata={key:model.ckpt.get(key) for key in ('version','date','license')}
    training_metadata['dataset']=model.ckpt.get('train_args',{}).get('data')
    baseline=json.loads((ANALYSIS_DIRECTORY/f'pilot_review_{owner_id}.json').read_text())
    if len(baseline['attempts'])!=3:
        raise ValueError('This pilot requires exactly the original three saved inputs')
    attempts=[]
    for original in baseline['attempts']:
        source=Path(original['artifact_directory'])
        provenance=json.loads((source/'provenance.json').read_text())
        prepared_path=source/'prepared.png'
        prepared_bytes=prepared_path.read_bytes()
        if hashlib.sha256(prepared_bytes).hexdigest()!=provenance['prepared_sha256']:
            raise ValueError('Original prepared VLM image hash changed')
        folder=directory/Path(original['name']).stem
        folder.mkdir(exist_ok=True)
        expected={'source_sha256':provenance['source_sha256'], 'prepared_sha256':provenance['prepared_sha256'],
                  'width':provenance['width'],'height':provenance['height'],
                  'checkpoint_sha256':checkpoint_sha,'ultralytics_version':ultralytics_version,
                  'checkpoint_training_metadata':training_metadata,
                  'torch_version':torch.__version__,'parameters':PARAMETERS,
                  'model_id':'ultralytics/yolov8n-oiv7','preparation_version':provenance['preparation_version'],
                  'inference_version':RUN_VERSION, 'input':'exact prepared raster used by the VLM; no gold labels'}
        reuse=(folder/'detections.json').exists() and (folder/'provenance.json').exists() and json.loads((folder/'provenance.json').read_text())==expected
        if not reuse:
            (folder/'prepared.png').write_bytes(prepared_bytes)
            with Image.open(prepared_path) as image:
                image=image.convert('RGB')
                started=time.perf_counter()
                result=model.predict(source=image,verbose=False,save=False,**PARAMETERS)[0]
                latency=time.perf_counter()-started
                if tuple(result.orig_shape)!=(image.height,image.width):
                    raise ValueError('Detector results are not mapped to the prepared raster')
                objects,metadata=[],[]
                for index,box in enumerate(result.boxes,1):
                    class_id=int(box.cls.item())
                    label=result.names[class_id]
                    xyxy=box.xyxy[0].cpu().tolist()
                    objects.append(normalized_detection(label,xyxy,image.width,image.height))
                    metadata.append({'prediction':index,'class_id':class_id,'source_label':label,
                                     'confidence':float(box.conf.item()),'xyxy_pixels':xyxy})
                render_boxes(image,DetectorOverlay([ProductObservation.model_validate(o) for o in objects]),folder/'boxes.png')
            detection={'objects':objects,'confidence_and_pixel_boxes':metadata,'latency_seconds':latency,
                       'speed_ms':result.speed,'class_count':len(result.names),'model_load_seconds':load_seconds}
            (folder/'detections.json').write_text(json.dumps(detection,indent=2))
            (folder/'provenance.json').write_text(json.dumps(expected,indent=2))
        else:
            detection=json.loads((folder/'detections.json').read_text())
        attempts.append({**{k:original[k] for k in ('file_id','name','current_source')},
                         'status':'succeeded','model_id':expected['model_id'],'prompt_version':'not-applicable',
                         'cost_usd':0,'artifact_directory':str(folder.resolve()),
                         'detector_output_path':str((folder/'detections.json').resolve()),
                         'object_count':len(detection['objects']),'inference_seconds':detection['latency_seconds'],
                         'cache_hit':reuse})
        print(json.dumps(attempts[-1]),flush=True)
    review={'owner_id':owner_id,'attempts':attempts,'run_version':RUN_VERSION,
            'parameters':PARAMETERS,'paid_model_calls':0,'api_cost_usd':0}
    review_path=directory/'review.json'
    review_path.write_text(json.dumps(review,indent=2))
    output=directory/'evaluation'
    metrics=evaluate(owner_id,review_path,output_directory=output)
    render_evaluation(metrics,output_directory=output)
    return metrics


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-id',type=int,default=1)
    args=parser.parse_args()
    run_yolo_pilot(args.owner_id)
