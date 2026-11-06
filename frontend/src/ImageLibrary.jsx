import { useCallback, useEffect, useState } from 'react';
import { api, apiUpload } from './api';
import { useImageUrl } from './VisualStudio';

function Photo({ item, token }) {
  const { url, error } = useImageUrl(`/image-library/images/${item.id}/file`, token);
  return <div className="reference-photo">{url ? <img src={url} alt={item.filename} loading="lazy" /> : <small>{error || 'Loading…'}</small>}
    <strong>{item.filename}</strong><small>{item.source} · {item.status}</small>{item.error && <small className="fact-check-error">{item.error}</small>}</div>;
}

export default function ImageLibrary({ token, notify, onChange }) {
  const [library, setLibrary] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    try { const next = await api('/image-library', { token }); setLibrary(next); onChange(next); setError(''); }
    catch (e) { setError(e.message); }
  }, [token, onChange]);
  useEffect(() => { refresh(); }, [refresh]);
  const indexing = ['queued', 'processing'].includes(library?.job?.status);
  useEffect(() => { if (!indexing) return; const timer = setInterval(refresh, 2000); return () => clearInterval(timer); }, [indexing, refresh]);
  async function index() {
    setBusy(true);
    try { await api('/image-library/index', { token, method: 'POST' }); await refresh(); }
    catch (e) { notify(e.message, 'error'); }
    finally { setBusy(false); }
  }
  async function upload(files) {
    setBusy(true);
    try {
      for (const file of Array.from(files)) {
        if (file.size > 10 * 1024 * 1024) throw new Error('Each photo must be 10 MB or smaller');
        await apiUpload('/image-library/images', { token, file });
      }
      await refresh(); notify('Photos added. Index the library to make them searchable.');
    } catch (e) { notify(e.message, 'error'); }
    finally { setBusy(false); }
  }
  return <section className="panel image-library"><div className="section-heading compact"><div><p className="eyebrow">CAMPAIGN REFERENCES</p><h2>Photo library</h2></div><strong>{library?.ready || 0} / {library?.total || 0} searchable</strong></div>
    <p>Use your synced Drive photos or upload scene references. Automatic selection checks the five closest photos and chooses up to two for your campaign.</p>
    <div className="visual-actions"><label className="button subtle">Add library photos<input type="file" multiple accept="image/jpeg,image/png,image/webp" hidden disabled={busy || indexing} onChange={e => { upload(e.target.files); e.target.value = ''; }} /></label>
      <button type="button" className="button primary" disabled={busy || indexing || !library?.total} onClick={index}>{indexing ? 'Indexing photos…' : 'Index new or changed photos'}</button>
      <button type="button" className="text-button" disabled={busy} onClick={refresh}>Refresh</button></div>
    {indexing && <p role="status">Preparing searchable photos: {library.job.done} / {library.job.total || library.total}. Image indexing runs locally and uses no AI API credits.</p>}
    {(error || library?.job?.error_message) && <p className="fact-check-error" role="alert">{error || library.job.error_message}</p>}
    <details><summary>Browse {library?.total || 0} photos</summary><div className="reference-grid">{(library?.images || []).map(item => <Photo key={item.id} item={item} token={token} />)}</div></details>
  </section>;
}
