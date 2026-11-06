"""Owner-scoped pgvector image library with immutable source hashes."""
import hashlib
from pathlib import Path

from PIL import Image, ImageOps
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from rag.vector_store import connect_rag_database
from .encoder import MODEL, REVISION, VERSION, ROOT, get_encoder, vector_literal

UPLOAD_ROOT = ROOT / 'data/image_library'
DRIVE_ROOT = ROOT / 'data/drive_sync/images'


def ensure(connection):
    connection.execute('CREATE EXTENSION IF NOT EXISTS vector')
    connection.execute('CREATE SCHEMA IF NOT EXISTS rag')
    connection.execute('''CREATE TABLE IF NOT EXISTS rag.image_library (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner_id INTEGER NOT NULL,
        source_id TEXT NOT NULL, filename TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        path TEXT NOT NULL, metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        status TEXT NOT NULL DEFAULT 'pending', error_message TEXT,
        model_id TEXT, model_revision TEXT, preparation_version TEXT,
        embedding VECTOR(768), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(owner_id,source_id))''')
    connection.execute('''CREATE TABLE IF NOT EXISTS rag.image_library_jobs (
        owner_id INTEGER PRIMARY KEY, status TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 0, error_message TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    connection.commit()


def refresh_drive(connection, owner_id):
    if not connection.execute("SELECT to_regclass('rag.drive_sync_files')").fetchone()[0]:
        return
    connection.execute('''INSERT INTO rag.image_library(owner_id,source_id,filename,source_sha256,path,metadata)
        SELECT owner_id,'drive:'||file_id,name,content_sha256,cache_path,
               jsonb_build_object('source','drive','drive_file_id',file_id,'reference_role','scene')
        FROM rag.drive_sync_files WHERE owner_id=%s AND kind='images' AND status='cached'
        AND content_sha256 IS NOT NULL AND cache_path IS NOT NULL
        ON CONFLICT(owner_id,source_id) DO UPDATE SET
            filename=EXCLUDED.filename,path=EXCLUDED.path,
            status=CASE WHEN rag.image_library.source_sha256<>EXCLUDED.source_sha256
                         OR rag.image_library.status='missing' THEN 'pending' ELSE rag.image_library.status END,
            embedding=CASE WHEN rag.image_library.source_sha256<>EXCLUDED.source_sha256
                            THEN NULL ELSE rag.image_library.embedding END,
            source_sha256=EXCLUDED.source_sha256, updated_at=now()''', (owner_id,))
    connection.execute('''UPDATE rag.image_library l SET status='missing',embedding=NULL
        WHERE l.owner_id=%s AND l.source_id LIKE 'drive:%%' AND NOT EXISTS (
        SELECT 1 FROM rag.drive_sync_files d WHERE d.owner_id=l.owner_id AND 'drive:'||d.file_id=l.source_id
        AND d.status='cached' AND d.kind='images' AND d.content_sha256=l.source_sha256)''', (owner_id,))
    connection.commit()


def read_asset(asset):
    path = Path(asset['path']).resolve()
    root = DRIVE_ROOT if asset['source_id'].startswith('drive:') else UPLOAD_ROOT
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Library image file is unavailable')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != asset['source_sha256']:
        raise ValueError('Library image changed; index the library again')
    return content


def asset_rows(connection, owner_id, *, ready=False):
    with connection.cursor(row_factory=dict_row) as c:
        sql = 'SELECT * FROM rag.image_library WHERE owner_id=%s AND status<>\'missing\''
        params = [owner_id]
        if ready:
            sql += " AND status='ready' AND model_id=%s AND model_revision=%s AND preparation_version=%s"
            params.extend([MODEL, REVISION, VERSION])
        c.execute(sql + ' ORDER BY id', params)
        return c.fetchall()


def listing(owner_id):
    with connect_rag_database() as c:
        ensure(c)
        refresh_drive(c, owner_id)
        c.execute("""UPDATE rag.image_library_jobs SET status='failed',
            error_message='Indexing was interrupted. You can start it again.',updated_at=now()
            WHERE owner_id=%s AND status IN ('queued','processing')
            AND updated_at<now()-interval '10 minutes'""",(owner_id,))
        c.commit()
        rows = asset_rows(c, owner_id)
        for row in rows:
            if row['status']=='ready' and (row['model_id']!=MODEL or row['model_revision']!=REVISION or row['preparation_version']!=VERSION):
                row['status']='pending'
        with c.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM rag.image_library_jobs WHERE owner_id=%s', (owner_id,))
            job = cur.fetchone()
        return {'model': MODEL, 'total': len(rows), 'ready': sum(r['status']=='ready' for r in rows),
                'job': job, 'images': [{'id': r['id'], 'filename': r['filename'], 'status': r['status'],
                                       'error': r['error_message'], 'source': r['metadata'].get('source')}
                                      for r in rows[:200]]}


def queue_index(owner_id):
    with connect_rag_database() as c:
        ensure(c)
        refresh_drive(c,owner_id)
        result = c.execute('''INSERT INTO rag.image_library_jobs(owner_id,status) VALUES(%s,'queued')
            ON CONFLICT(owner_id) DO UPDATE SET status='queued',done=0,total=0,error_message=NULL,updated_at=now()
            WHERE rag.image_library_jobs.status NOT IN ('queued','processing')
            OR rag.image_library_jobs.updated_at<now()-interval '10 minutes' RETURNING owner_id''',(owner_id,)).fetchone()
        c.commit()
        return bool(result)


def index_library(owner_id):
    with connect_rag_database() as c:
        ensure(c)
        acquired=c.execute("SELECT pg_try_advisory_lock(hashtext('image-library'),%s)",(owner_id,)).fetchone()[0]
        c.commit()
        if not acquired:
            return
        try:
            refresh_drive(c,owner_id)
            rows=[r for r in asset_rows(c,owner_id) if r['status']!='ready'
                  or r['model_id']!=MODEL or r['model_revision']!=REVISION or r['preparation_version']!=VERSION]
            c.execute("UPDATE rag.image_library_jobs SET status='processing',total=%s,done=0,updated_at=now() WHERE owner_id=%s",(len(rows),owner_id))
            c.commit()
            encoder=get_encoder() if rows else None
            failed=0
            for i,row in enumerate(rows,1):
                try:
                    read_asset(row)
                    with Image.open(row['path']) as image:
                        pixels=ImageOps.exif_transpose(image).convert('RGB')
                        vector=encoder.images([pixels])[0]
                    read_asset(row)
                    c.execute('''UPDATE rag.image_library SET embedding=%s::vector,status='ready',error_message=NULL,
                        model_id=%s,model_revision=%s,preparation_version=%s,updated_at=now()
                        WHERE id=%s AND owner_id=%s AND source_sha256=%s''',
                        (vector_literal(vector),MODEL,REVISION,VERSION,row['id'],owner_id,row['source_sha256']))
                except Exception:
                    c.rollback()
                    failed+=1
                    c.execute("UPDATE rag.image_library SET status='failed',error_message=%s WHERE id=%s AND owner_id=%s",
                              ('Image indexing failed; check that the source is available and retry.',row['id'],owner_id))
                c.execute('UPDATE rag.image_library_jobs SET done=%s,updated_at=now() WHERE owner_id=%s',(i,owner_id))
                c.commit()
            c.execute("UPDATE rag.image_library_jobs SET status=%s,error_message=%s,updated_at=now() WHERE owner_id=%s",
                      ('failed' if failed else 'ready', f'{failed} image(s) failed to index.' if failed else None,owner_id))
            c.commit()
        except Exception:
            c.rollback()
            c.execute("UPDATE rag.image_library_jobs SET status='failed',error_message=%s,updated_at=now() WHERE owner_id=%s",
                      ('Image encoder could not start. Check local model dependencies and retry.',owner_id))
            c.commit()
            raise
        finally:
            c.execute("SELECT pg_advisory_unlock(hashtext('image-library'),%s)",(owner_id,))
            c.commit()


def search(owner_id, query, top_k=5):
    if not 1<=top_k<=5:
        raise ValueError('Choose between one and five candidate photos')
    vector=get_encoder().query(query)
    with connect_rag_database() as c:
        ensure(c)
        refresh_drive(c,owner_id)
        with c.cursor(row_factory=dict_row) as cur:
            cur.execute('''SELECT id,owner_id,source_id,filename,source_sha256,path,metadata,
                1-(embedding <=> %s::vector) AS similarity FROM rag.image_library
                WHERE owner_id=%s AND status='ready' AND model_id=%s AND model_revision=%s
                AND preparation_version=%s ORDER BY embedding <=> %s::vector,id LIMIT %s''',
                (vector_literal(vector),owner_id,MODEL,REVISION,VERSION,vector_literal(vector),top_k))
            rows=cur.fetchall()
        for row in rows:
            read_asset(row)
        return rows
