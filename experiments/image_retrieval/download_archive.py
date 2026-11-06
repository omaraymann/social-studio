"""Download the official XM3600 image archive with resumable validated ranges."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import time
import urllib.request
from .prepare import DATA

URL = 'https://open-images-dataset.s3.amazonaws.com/crossmodal-3600/images.tgz'

def download():
    path = DATA / 'raw/images.tgz'
    with urllib.request.urlopen(urllib.request.Request(URL, method='HEAD'), timeout=30) as r:
        size = int(r.headers['Content-Length'])
    if path.exists() and path.stat().st_size == size:
        return
    chunk = 2 * 1024 * 1024
    parts = DATA / 'raw/image_archive_parts'
    parts.mkdir(exist_ok=True)
    def fetch(start):
        end = min(start + chunk, size) - 1
        part = parts / str(start)
        if part.exists() and part.stat().st_size == end-start+1:
            return
        for attempt in range(3):
            try:
                request = urllib.request.Request(URL, headers={'Range': f'bytes={start}-{end}'})
                with urllib.request.urlopen(request, timeout=90) as r:
                    if r.status != 206 or r.headers.get('Content-Range') != f'bytes {start}-{end}/{size}':
                        raise ValueError('Unexpected range response')
                    content = r.read(end-start+2)
                if len(content) != end-start+1:
                    raise ValueError('Incomplete range')
                part.write_bytes(content)
                return
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    starts = list(range(0,size,chunk))
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fetch, start) for start in starts]
        for i, future in enumerate(as_completed(futures),1):
            future.result()
            if i % 10 == 0:
                print(f'Archive ranges {i}/{len(starts)}',flush=True)
    temp = path.with_suffix('.partial')
    with temp.open('wb') as f:
        for start in starts:
            f.write((parts / str(start)).read_bytes())
    temp.replace(path)
    print('Archive downloaded:', size, 'bytes; SHA256:', hashlib.sha256(path.read_bytes()).hexdigest(),flush=True)
    for part in parts.iterdir():
        part.unlink()
    parts.rmdir()

if __name__ == '__main__':
    download()
