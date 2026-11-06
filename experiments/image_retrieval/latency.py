"""Measure sequential warm single-query searches against the frozen gallery."""
import json
import time
import numpy as np
import torch
from .benchmark import load_encoder, normalize, synchronize
from .prepare import DATA


def run():
    manifest=json.loads((DATA/'sample_200/manifest.json').read_text())
    captions=[c for r in manifest['records'] for c in r['captions']]
    queries=[captions[i] for i in range(0,len(captions),8)]
    for name in ['openclip','siglip2']:
        device='mps' if torch.backends.mps.is_available() else 'cpu'
        model,_,encode_text,metadata=load_encoder(name,device)
        vectors=np.load(DATA/'results'/name/'embeddings.npz')['images']
        samples=[]
        with torch.inference_mode():
            for text in queries[:5]:
                normalize(encode_text([text]))
            synchronize(device)
            for text in queries:
                synchronize(device)
                start=time.perf_counter()
                query=normalize(encode_text([text]))[0]
                synchronize(device)
                encoded=time.perf_counter()
                scores=vectors @ query
                indices=np.argsort(-scores,kind='stable')[:5]
                finished=time.perf_counter()
                samples.append({'query':text,'encode_ms':(encoded-start)*1000,
                                'rank_ms':(finished-encoded)*1000,'total_ms':(finished-start)*1000})
        report={'repo':metadata['repo'],'revision':metadata['revision'],'device':device,
                'queries':len(queries),'warmups':5,'gallery_size':len(vectors),'batch_size':1,
                'includes':'tokenization, text encoding, normalization, GPU synchronization, CPU transfer, ranking',
                'excludes':'model load, image encoding, network, API/frontend overhead, query rewriting',
                'percentiles':{key:{'p50':float(np.percentile([s[key] for s in samples],50)),
                                    'p95':float(np.percentile([s[key] for s in samples],95)),
                                    'mean':float(np.mean([s[key] for s in samples]))}
                               for key in ['encode_ms','rank_ms','total_ms']},'samples':samples}
        (DATA/'results'/name/'latency.json').write_text(json.dumps(report,indent=2))
        print(name,json.dumps(report['percentiles']),flush=True)
        del model,encode_text
        if device=='mps':torch.mps.empty_cache()

if __name__=='__main__':
    run()
