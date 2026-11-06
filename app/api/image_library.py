"""Authenticated, reusable Drive/uploaded photo library."""
import hashlib
from pathlib import PurePath
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.security import current_user
from app.models import User
from app.services.image_storage import MAX_UPLOAD_BYTES, decode_image
from integrations.image_retrieval import library
from rag.vector_store import connect_rag_database

router=APIRouter(prefix='/image-library',tags=['image library'])


@router.get('')
def image_library(user: User=Depends(current_user)):
    return library.listing(user.id)


@router.post('/index',status_code=202)
def index_images(tasks: BackgroundTasks,user: User=Depends(current_user)):
    if not library.queue_index(user.id):
        raise HTTPException(409,'Your image library is already being indexed')
    tasks.add_task(library.index_library,user.id)
    return {'status':'queued'}


@router.post('/images',status_code=201)
async def upload(file: UploadFile=File(...),user: User=Depends(current_user)):
    raw=await file.read(MAX_UPLOAD_BYTES+1)
    if len(raw)>MAX_UPLOAD_BYTES:
        raise HTTPException(413,'Each image must be 10 MB or smaller')
    try:
        decode_image(raw)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    digest=hashlib.sha256(raw).hexdigest()
    source_id='upload:'+digest
    directory=library.UPLOAD_ROOT/str(user.id)
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/(digest+'.image')
    if not path.exists():
        temporary=directory/(uuid4().hex+'.partial')
        temporary.write_bytes(raw)
        temporary.replace(path)
    with connect_rag_database() as c:
        library.ensure(c)
        row=c.execute('''INSERT INTO rag.image_library(owner_id,source_id,filename,source_sha256,path,metadata)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(owner_id,source_id) DO UPDATE SET filename=EXCLUDED.filename
            RETURNING id''',(user.id,source_id,PurePath(file.filename or 'photo').name[:300],digest,str(path),
                            Jsonb({'source':'upload','reference_role':'scene'}))).fetchone()
        c.commit()
    return {'id':row[0],'status':'pending'}


@router.get('/images/{image_id}/file')
def photo(image_id: int,user: User=Depends(current_user)):
    with connect_rag_database() as c:
        library.ensure(c)
        library.refresh_drive(c,user.id)
        with c.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM rag.image_library WHERE id=%s AND owner_id=%s AND status<>'missing'",(image_id,user.id))
            row=cur.fetchone()
    if row is None:
        raise HTTPException(404,'Library image not found')
    try:
        raw=library.read_asset(row)
        import io
        pixels=decode_image(raw).convert('RGB')
        pixels.thumbnail((640,640))
        out=io.BytesIO();pixels.save(out,'JPEG')
    except ValueError as exc:
        raise HTTPException(404,str(exc)) from exc
    return Response(out.getvalue(),media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})


class SearchRequest(BaseModel):
    query: str=Field(min_length=1,max_length=1000)


@router.post('/search')
def search_images(payload: SearchRequest,user: User=Depends(current_user)):
    try:
        rows=library.search(user.id,payload.query)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    return {'query':payload.query,'images':[{'id':r['id'],'filename':r['filename'],
             'similarity':r['similarity']} for r in rows]}
