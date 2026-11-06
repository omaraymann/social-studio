"""One cached, thread-safe SigLIP 2 encoder shared by indexing and search."""
from functools import lru_cache
from pathlib import Path
from threading import RLock

import numpy as np

MODEL = 'google/siglip2-base-patch32-256'
REVISION = '94dffa8cb1179de3e03f091dbc3917e5d5a9ae84'
VERSION = 'siglip2-p32-256-exif-rgb-v1'
DIMENSIONS = 768
ROOT = Path(__file__).resolve().parents[2]


class Encoder:
    def __init__(self):
        import torch
        from transformers import AutoModel, AutoProcessor
        cache = ROOT / 'data/image_retrieval/models/hub'
        local = cache / ('models--' + MODEL.replace('/', '--')) / 'snapshots' / REVISION
        source = str(local) if (local / 'model.safetensors').exists() else MODEL
        kwargs = {'local_files_only': True} if source != MODEL else {'revision': REVISION, 'cache_dir': str(cache)}
        self.torch = torch
        self.device = 'mps' if torch.backends.mps.is_available() else 'cpu'
        self.model = AutoModel.from_pretrained(source, **kwargs).to(self.device).eval()
        self.processor = AutoProcessor.from_pretrained(source, **kwargs)
        self.lock = RLock()

    def _vectors(self, output):
        values = output.pooler_output if hasattr(output, 'pooler_output') else output
        values = values.float()
        norms = values.norm(dim=-1, keepdim=True)
        if not self.torch.isfinite(values).all() or (norms == 0).any():
            raise ValueError('Image encoder produced invalid vectors')
        return (values / norms).cpu().numpy()

    def images(self, images):
        with self.lock, self.torch.inference_mode():
            inputs = self.processor(images=images, return_tensors='pt').to(self.device)
            return self._vectors(self.model.get_image_features(**inputs))

    def query(self, text):
        text = text.strip().lower()
        if not text:
            raise ValueError('Image search query cannot be empty')
        if len(self.processor.tokenizer(text, add_special_tokens=True)['input_ids']) > 64:
            raise ValueError('Image search query exceeds the 64-token budget; shorten it')
        with self.lock, self.torch.inference_mode():
            inputs = self.processor(text=[text], padding='max_length', max_length=64,
                                    truncation=False, return_tensors='pt').to(self.device)
            return self._vectors(self.model.get_text_features(**inputs))[0]


@lru_cache(maxsize=1)
def _cached_encoder():
    return Encoder()


_initialization_lock = RLock()


def get_encoder():
    with _initialization_lock:
        return _cached_encoder()


def vector_literal(vector):
    values = np.asarray(vector, dtype=np.float32)
    if values.shape != (DIMENSIONS,) or not np.isfinite(values).all():
        raise ValueError('Expected a finite 768-dimensional image vector')
    return '[' + ','.join(str(float(v)) for v in values) + ']'
