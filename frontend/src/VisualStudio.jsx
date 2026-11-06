import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiFile, apiUpload } from "./api";

export const INITIAL_MODELS = {
  vision: [{ id: "google/gemini-2.5-flash", name: "Gemini 2.5 Flash" }, { id: "google/gemini-2.5-pro", name: "Gemini 2.5 Pro" }],
  image: [{ id: "google/gemini-2.5-flash-image", name: "Nano Banana · Gemini 2.5 Flash Image", max_reference_images: 3 },
    { id: "google/gemini-3.1-flash-image", name: "Nano Banana 2 · Gemini 3.1 Flash Image", max_reference_images: 4 }],
};

export function useImageUrl(path, token) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    let objectUrl;
    setUrl(""); setError("");
    apiFile(path, token).then((blob) => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch((reason) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [path, token]);
  return { url, error };
}

export function LocalImagePreview({ file }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    const next = URL.createObjectURL(file);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  return url ? <img src={url} alt={file.name} /> : null;
}

function ReferencePhoto({ image, postId, token }) {
  const { url, error } = useImageUrl(`/posts/${postId}/images/${image.id}/file`, token);
  return <>{url ? <img src={url} alt={image.filename} /> : <span>{error || "Loading photo…"}</span>}</>;
}

function CandidatePhoto({ candidate, token }) {
  const { url, error } = useImageUrl(`/image-library/images/${candidate.image_id}/file`, token);
  return url ? <img src={url} alt={candidate.filename} /> : <small>{error || 'Loading…'}</small>;
}

function SelectionDetails({ audit, token }) {
  if (!audit) return null;
  return <details className="image-selection-audit"><summary>Automatic photo selection · {audit.selected.length} selected from {audit.candidates.length}</summary>
    <p><strong>Search:</strong> {audit.query}</p><small>{audit.retrieval_model} · similarity scores rank candidates; they are not relevance probabilities.</small>
    {!audit.selected.length && <p>{audit.no_match_reason} · {audit.no_match_policy === 'stop' ? 'Image generation stopped.' : 'Using a general topic illustration.'}</p>}
    <div className="reference-grid">{audit.candidates.map(candidate => {
      const selected = audit.selected.find(item => item.image_id === candidate.image_id);
      return <div key={candidate.image_id} className={`reference-photo ${selected ? 'selected' : ''}`}><CandidatePhoto candidate={candidate} token={token} />
        <strong>{selected ? '✓ Selected' : 'Not selected'} · {candidate.filename}</strong><small>similarity {candidate.similarity.toFixed(3)}</small>{selected && <p>{selected.reason}</p>}{selected?.adaptation_notes && <p><strong>Changes for the generated image:</strong> {selected.adaptation_notes}</p>}</div>;
    })}</div>
  </details>;
}

function wrapText(context, text, width) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (line && context.measureText(candidate).width > width) { lines.push(line); line = word; }
    else line = candidate;
  }
  if (line) lines.push(line);
  return lines;
}

function VisualResult({ visual, postId, token, onSaved, notify, onSelect, selected, editable }) {
  const canvasRef = useRef(null);
  const { url, error } = useImageUrl(`/posts/${postId}/visuals/${visual.id}/file`, token);
  const [headline, setHeadline] = useState(visual.headline || "");
  const [cta, setCta] = useState(visual.call_to_action || "");
  const [saving, setSaving] = useState(false);
  const [ready, setReady] = useState(false);
  const unsaved = headline !== visual.headline || cta !== visual.call_to_action;

  useEffect(() => {
    setReady(false);
    if (!url) return undefined;
    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (cancelled || !canvasRef.current) return;
      const canvas = canvasRef.current;
      canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
      const context = canvas.getContext("2d");
      const { width, height } = canvas;
      context.drawImage(image, 0, 0);
      const gradient = context.createLinearGradient(0, height * 0.64, 0, height);
      gradient.addColorStop(0, "rgba(8,18,15,0)"); gradient.addColorStop(0.35, "rgba(8,18,15,0.8)"); gradient.addColorStop(1, "rgba(8,18,15,0.96)");
      context.fillStyle = gradient; context.fillRect(0, height * 0.64, width, height * 0.36);
      const margin = width * 0.045;
      const maxWidth = width - margin * 2;
      let fontSize = Math.min(width, height) * 0.05;
      let headlineLines, ctaLines, ctaSize, total;
      do {
        context.font = `bold ${fontSize}px Arial`;
        headlineLines = wrapText(context, headline, maxWidth);
        ctaSize = fontSize * 0.53;
        context.font = `${ctaSize}px Arial`;
        ctaLines = wrapText(context, cta, maxWidth);
        total = headlineLines.length * fontSize * 1.2 + ctaLines.length * ctaSize * 1.35 + fontSize * 0.4;
        if (total <= height * 0.27) break;
        fontSize *= 0.93;
      } while (fontSize > 8);
      context.textBaseline = "top";
      context.fillStyle = "white";
      let y = height - height * 0.04 - total;
      context.font = `bold ${fontSize}px Arial`;
      for (const line of headlineLines) { context.fillText(line, margin, y, maxWidth); y += fontSize * 1.2; }
      y += fontSize * 0.4;
      context.fillStyle = "#d6f4c6"; context.font = `${ctaSize}px Arial`;
      for (const line of ctaLines) { context.fillText(line, margin, y, maxWidth); y += ctaSize * 1.35; }
      setReady(true);
    };
    image.src = url;
    return () => { cancelled = true; };
  }, [url, headline, cta]);

  async function saveText() {
    setSaving(true);
    try {
      const saved = await api(`/posts/${postId}/visuals/${visual.id}`, { token, method: "PATCH", body: { headline, call_to_action: cta } });
      setHeadline(saved.headline); setCta(saved.call_to_action);
      await onSaved(); notify("Visual text saved");
    } catch (reason) { notify(reason.message, "error"); }
    finally { setSaving(false); }
  }

  function download() {
    canvasRef.current.toBlob((blob) => {
      if (!blob) return notify("Could not export this image", "error");
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a"); anchor.href = objectUrl;
      anchor.download = `campaign-${postId}-${visual.platform || "campaign"}-visual-${visual.id}.png`; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    }, "image/png");
  }

  const planningCost = visual.usage?.planning?.cost;
  const imageCost = visual.usage?.generation?.cost;
  return <article className="visual-result">
    <div className="visual-result-heading"><strong>{visual.platform === "linkedin" ? "LinkedIn" : visual.platform === "x" ? "X" : visual.platform === "discord" ? "Discord" : visual.platform === "instagram" ? "Instagram" : "Campaign"} image · Attempt #{visual.id}</strong><small>{visual.image_model} · {visual.width} × {visual.height}</small></div>
    {error && <p role="alert" className="fact-check-error">{error}</p>}
    <canvas className="campaign-canvas" ref={canvasRef} role="img" aria-label={`Campaign visual: ${headline}. ${cta}`} />
    {!ready && !error && <p>Preparing preview…</p>}
    <div className="field-grid two"><label>Headline<input maxLength={100} disabled={!editable} value={headline} onChange={(event) => setHeadline(event.target.value)} /></label>
      <label>CTA on image<input maxLength={150} disabled={!editable} value={cta} onChange={(event) => setCta(event.target.value)} /></label></div>
    <div className="visual-actions"><button type="button" className="button subtle" disabled={!editable || saving || !unsaved || !headline.trim() || !cta.trim()} onClick={saveText}>{saving ? "Saving…" : "Save image text"}</button>
      <button type="button" className="button approve" disabled={!editable || saving || unsaved || selected} onClick={() => onSelect(visual.id)}>{selected ? "Attached to this post ✓" : "Use this image in post"}</button>
      <button type="button" className="button primary" disabled={!ready || !headline.trim() || !cta.trim()} onClick={download}>Download preview PNG</button></div>
    <small>{unsaved ? "Text changes are previewed here; save to keep them after reload. " : ""}Headline and CTA are rendered separately from the generated image. Review product details before using it.</small>
    <p className="visual-usage">Planning: {visual.vision_model} · {visual.usage?.planning?.included_in_generation_run ? "included in campaign generation" : planningCost == null ? "cost unknown" : `$${planningCost.toFixed(4)}`} · Image: {imageCost == null ? "cost unknown" : `$${imageCost.toFixed(4)}`} · {visual.latency_ms == null ? "—" : `${(visual.latency_ms / 1000).toFixed(1)}s`}</p>
    <SelectionDetails audit={visual.context.image_retrieval} token={token} />
    <details><summary>View visual brief and source passages</summary><p>{visual.visual_brief}</p><small>References: {visual.reference_image_ids.join(", ")} · Campaign text snapshot from generation {visual.context.generation_run_id || "—"}</small>
      {(visual.context.retrieved_passages || []).map((chunk, index) => <blockquote key={index}><strong>{chunk.title}</strong><p>{chunk.text}</p></blockquote>)}
      {!visual.context.retrieved_passages?.length && <p>No RAG passages were used.</p>}
    </details>
  </article>;
}

const visualSteps = [["planning", "Read photos & plan", "◎"], ["generating", "Generate image", "✦"], ["saving", "Save preview", "↓"]];

function AttachedImage({ variant, token }) {
  const { url, error } = useImageUrl(`/variants/${variant.id}/image?revision=${variant.selected_image_sha256 || variant.selected_visual_id}`, token);
  return <figure className="attached-post-image">{url && <><img src={url} alt={`Image attached to this ${variant.platform} post`} /><figcaption>Post image · attempt #{variant.selected_visual_id} · <a href={url} download={`post-${variant.id}-${variant.platform}.png`}>Download exact post image</a></figcaption></>}
    {error && <p className="fact-check-error" role="alert">{error}</p>}{!url && !error && <p>Loading attached image…</p>}</figure>;
}

export default function VisualStudio({ postId, token, models, variant, notify, refreshPost }) {
  const [images, setImages] = useState([]);
  const [selected, setSelected] = useState([]);
  const [visuals, setVisuals] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [visionModel, setVisionModel] = useState("google/gemini-2.5-flash");
  const [imageModel, setImageModel] = useState("google/gemini-2.5-flash-image");
  const [aspectRatio, setAspectRatio] = useState(["linkedin", "instagram"].includes(variant.platform) ? "1:1" : "16:9");
  const [style, setStyle] = useState("studio product photography");
  const [instructions, setInstructions] = useState("");
  const [showOlder, setShowOlder] = useState(false);
  const platform = variant.platform;
  const editable = ["draft", "rejected"].includes(variant.status);
  const refreshPostRef = useRef(refreshPost);
  refreshPostRef.current = refreshPost;
  const filterRows = useCallback((rows) => rows.filter((row) => row.platform === variant.platform || row.platform === "campaign"), [variant.platform]);
  const referenceLimit = models.image.find((model) => model.id === imageModel)?.max_reference_images
    || (imageModel === "google/gemini-2.5-flash-image" ? 3 : 4);
  const tooManyReferences = selected.length > referenceLimit;

  const refresh = useCallback(async () => {
    const rows = await api(`/posts/${postId}/visuals`, { token }); setVisuals(filterRows(rows));
    await refreshPostRef.current();
  }, [postId, token, filterRows]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api(`/posts/${postId}/images`, { token }), api(`/posts/${postId}/visuals`, { token })]).then(([photos, rows]) => {
      if (!cancelled) { setImages(photos); setSelected(photos.slice(0, 1).map((image) => image.id)); setVisuals(filterRows(rows)); }
    }).catch((reason) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; };
  }, [postId, token, filterRows]);

  const active = visuals.find((row) => row.status === "processing") || visuals.find((row) => row.status === "queued");
  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try { const rows = filterRows(await api(`/posts/${postId}/visuals`, { token })); if (!cancelled) {
        const completedNow = rows.some((row) => ["succeeded", "failed"].includes(row.status) && visuals.some((old) => old.id === row.id && ["queued", "processing"].includes(old.status)));
        setVisuals(rows); setError("");
        if (completedNow) await refreshPostRef.current();
      } }
      catch (reason) { if (!cancelled) { setError(reason.message); setVisuals((rows) => [...rows]); } }
    }, 1500);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [visuals, active, postId, token, filterRows]);

  async function selectImage(id) {
    setBusy(true); setError("");
    try { await api(`/variants/${variant.id}/image`, { token, method: "POST", body: { visual_id: id } }); await refresh(); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }

  async function textOnly() {
    setBusy(true); setError("");
    try { await api(`/variants/${variant.id}/image`, { token, method: "DELETE" }); await refresh(); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }

  async function upload(files) {
    files = Array.from(files || []);
    if (!files.length) return;
    setBusy(true); setError("");
    try {
      for (const file of files) {
        const photo = await apiUpload(`/posts/${postId}/images`, { token, file });
        setImages((rows) => [...rows, photo]);
        setSelected((ids) => ids.length < referenceLimit ? [...ids, photo.id] : ids);
      }
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }

  async function generate() {
    if (tooManyReferences) return setError(`This model accepts at most ${referenceLimit} reference photos. Deselect extra photos.`);
    setBusy(true); setError("");
    try {
      const row = await api(`/posts/${postId}/visuals`, { token, method: "POST", body: {
        reference_image_ids: selected, vision_model: visionModel, image_model: imageModel,
        aspect_ratio: aspectRatio, style, instructions,
        platform,
      } });
      setVisuals((rows) => [row, ...rows]);
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }

  function toggle(id) {
    if (selected.includes(id)) setSelected(selected.filter((value) => value !== id));
    else if (selected.length < referenceLimit) setSelected([...selected, id]);
    else setError(`This model accepts at most ${referenceLimit} reference photos`);
  }

  const completed = visuals.filter((row) => row.status === "succeeded");
  const latestByPlatform = completed.filter((row, index) => completed.findIndex((other) => other.platform === row.platform) === index);
  return <section className="visual-studio post-media" aria-label={`${variant.platform} post image`}>
    {variant.image_attached && <AttachedImage variant={variant} token={token} />}
    {variant.image_required && !variant.image_attached && <p className="fact-check-hint">An image was requested for this post. {active ? "Generation is in progress." : "Select or regenerate an image before approval, or choose text only."}</p>}
    {variant.image_required && !variant.image_attached && !active && visuals.find((row) => row.status === "failed") && <p className="fact-check-error" role="alert">{visuals.find((row) => row.status === "failed").error_message}</p>}
    {editable && variant.platform !== "instagram" && (variant.image_attached || variant.image_required) && <button type="button" className="text-button" disabled={busy} onClick={textOnly}>Use text only</button>}
    <details className="post-image-options"><summary>{variant.image_attached ? "Change image or edit image text" : "Choose or generate a post image"}</summary>
    <details className="visual-manual-controls"><summary>Regenerate an image or try other settings</summary>
    <p className="panel-intro">Choose photos of the same product. Regeneration makes a new planning call using the saved campaign context and its RAG evidence.</p>
    <label className="upload-drop"><input type="file" multiple accept="image/jpeg,image/png,image/webp" disabled={!editable || busy || !!active} onChange={(event) => { upload(event.target.files); event.target.value = ""; }} />
      <strong>{busy ? "Working…" : "Attach product photos"}</strong><small>JPEG, PNG or WebP · up to 10 MB each · this model accepts up to {referenceLimit} references</small></label>
    <div className="reference-grid">{images.map((image) => <button type="button" key={image.id} className={`reference-photo ${selected.includes(image.id) ? "selected" : ""}`} disabled={!!active || busy} onClick={() => toggle(image.id)} aria-pressed={selected.includes(image.id)}>
      <ReferencePhoto image={image} postId={postId} token={token} /><strong>{selected.includes(image.id) ? "✓ " : ""}{image.filename}</strong><small>{image.reference_role === 'scene' ? 'Scene reference' : 'Product reference'} · {image.original_width} × {image.original_height} → {image.width} × {image.height}</small>
    </button>)}</div>
    <div className="field-grid two"><label>Vision model / visual planner<select value={visionModel} disabled={!!active} onChange={(event) => setVisionModel(event.target.value)}>{models.vision.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
      <label>Image-generation model<select value={imageModel} disabled={!!active} onChange={(event) => setImageModel(event.target.value)}>{models.image.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label></div>
    <div className="field-grid two"><label>Format<select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}><option value="1:1">Square · 1:1</option><option value="16:9">Landscape · 16:9</option><option value="4:5">Portrait · 4:5</option></select></label>
      <label>Visual style<select value={style} onChange={(event) => setStyle(event.target.value)}>{["studio product photography", "lifestyle scene", "minimal editorial"].map((value) => <option key={value}>{value}</option>)}</select></label></div>
    <p className="fact-check-hint">This image belongs to the {platform} post above.</p>
    <label>Design instructions (optional)<input maxLength={1000} value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="Warm reading corner, blue accents, product centered…" /></label>
    <div className="visual-actions"><button type="button" className="button primary" onClick={generate} disabled={!editable || busy || !!active || !selected.length || tooManyReferences}>{active ? "Creating visual…" : "Generate post image"}</button><small>{selected.length}/{referenceLimit} reference photos selected. Uses AI credits: one planning call and one image call. Select another model to create a comparison attempt.</small></div>
    {tooManyReferences && <p className="fact-check-error" role="alert">This model accepts at most {referenceLimit} reference photos. Deselect extra photos before generating.</p>}
    </details>
    {error && <p className="fact-check-error" role="alert">{error}</p>}
    {active && <><p aria-live="polite"><strong>{active.platform === "campaign" ? "Campaign" : active.platform} image: {active.step}</strong></p><div className="ingestion-steps visual-progress">{visualSteps.map(([key, label, icon], index) => {
      const current = Math.max(0, visualSteps.findIndex(([step]) => step === active.step));
      return <div className={`ingestion-step ${index < current ? "complete" : ""} ${index === current ? "active" : ""}`} key={key}><span className="ingestion-step-icon">{index < current ? "✓" : icon}</span><small>{label}</small></div>;
    })}</div><div className="visual-job-list">{visuals.filter((row) => ["queued", "processing"].includes(row.status)).map((row) => <p key={row.id}>{row.platform} · {row.status === "queued" ? "Waiting to generate" : row.step}</p>)}</div></>}
    {visuals.filter((row) => row.status === "failed").slice(0, 3).map((row) => <div key={row.id}><p className="fact-check-error">{row.platform} · Attempt #{row.id} · {row.image_model}: {row.error_message}</p><SelectionDetails audit={row.context.image_retrieval} token={token} /></div>)}
    {(showOlder ? completed : latestByPlatform).map((row) => <VisualResult key={row.id} visual={row} postId={postId} token={token} onSaved={refresh} notify={notify} onSelect={selectImage} selected={variant.selected_visual_id === row.id} editable={editable && !busy} />)}
    {completed.length > latestByPlatform.length && <button type="button" className="text-button" onClick={() => setShowOlder(!showOlder)}>{showOlder ? "Show latest per platform" : `Show all ${completed.length} attempts`}</button>}
    {!visuals.length && <p className="empty">Tick “Generate campaign images” in the composer for automatic platform visuals, or open the controls above.</p>}
    </details>
  </section>;
}
