"""Download pinned model snapshots without loading either model."""
from .benchmark import download_model

if __name__ == '__main__':
    for name in ['openclip', 'siglip2']:
        snapshot, metadata = download_model(name)
        print(name, metadata, snapshot, flush=True)
