import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiUpload } from "./api";
import VisualStudio, { INITIAL_MODELS, LocalImagePreview } from "./VisualStudio";
import InstagramConnection from "./InstagramConnection";
import ImageLibrary from './ImageLibrary';

const initialCampaign = {
  title: "",
  markdown: "",
  platforms: ["x", "linkedin"],
  generation_mode: "llm",
  audience: "data engineers",
  goal: "traffic",
  tone: "educational",
  call_to_action: "Read the complete article",
  use_rag: false,
  rag_document_ids: [],
  rag_top_k: 5,
  rag_min_similarity: 0.70,
  vision_model: "google/gemini-2.5-flash",
  generate_images: false,
  auto_select_images: false,
  image_no_match_policy: 'stop',
  image_style: "studio product photography",
  image_instructions: "",
};

const workspacePages = [
  { id: 'documents', label: 'Add documents', icon: '▤', description: 'Upload sources and browse your document library.' },
  { id: 'images', label: 'Add images', icon: '▧', description: 'Add reference photos and prepare your image library.' },
  { id: 'generate', label: 'Generate campaign', icon: '✦', description: 'Turn your source and library into a campaign for each platform.' },
  { id: 'review', label: 'Review campaigns', icon: '☑', description: 'Check your drafts and images, then approve and schedule.' },
  { id: 'activity', label: 'Activity', icon: '↗', description: 'Inspect generation runs and publishing results.' },
  { id: 'overview', label: 'Overview', icon: '◫', description: 'Track campaigns, usage, and delivery across your workspace.' },
  { id: 'accounts', label: 'Connected accounts', icon: '◎', description: 'Manage the accounts you publish to.' },
];

function StatusPill({ status }) {
  return <span className={`pill pill-${status}`}>{status}</span>;
}

function AuthScreen({ onAuthenticated, message }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("demo@example.com");
  const [password, setPassword] = useState("demo-password-2026");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "signup") await api("/auth/signup", { method: "POST", body: { email, password } });
      const result = await api("/auth/login", { method: "POST", body: { email, password } });
      onAuthenticated(result.access_token, email);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-story">
        <div className="brand"><span className="brand-mark">S</span> Social Media Studio</div>
        <p className="eyebrow">AI CAMPAIGN OPERATIONS</p>
        <h1>One article.<br /><em>Every channel.</em></h1>
        <p className="hero-copy">Generate, review, schedule, and observe platform-ready campaigns from one calm workspace.</p>
        <div className="flow-strip"><span>Source</span><b>→</b><span>Generate</span><b>→</b><span>Approve</span><b>→</b><span>Publish</span></div>
      </section>
      <form className="auth-card" onSubmit={submit}>
        <p className="eyebrow">WELCOME</p>
        <h2>{mode === "login" ? "Open your studio" : "Create your studio"}</h2>
        {message && <p className="alert" role="status">{message}</p>}
        <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength="8" required /></label>
        {error && <div className="alert error">{error}</div>}
        <button className="button primary full" disabled={busy}>{busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}</button>
        <button type="button" className="text-button" onClick={() => { setMode(mode === "login" ? "signup" : "login"); setError(""); }}>
          {mode === "login" ? "Need an account? Sign up" : "Already registered? Sign in"}
        </button>
      </form>
    </main>
  );
}

const ingestionSteps = [
  ["uploaded", "Upload", "↑"], ["extracting", "Extract text", "▤"],
  ["chunking", "Chunk", "◫"], ["embedding", "Embed", "◎"], ["indexing", "Index in pgvector", "⌘"],
];

function KnowledgeBasePanel({ token, onDocuments, notify }) {
  const [documents, setDocuments] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [reuseNotes, setReuseNotes] = useState("");
  const [documentTitle, setDocumentTitle] = useState("");

  const refreshDocuments = useCallback(async () => {
    const rows = await api("/rag/documents", { token });
    setDocuments(rows);
    onDocuments(rows);
  }, [token, onDocuments]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api("/rag/documents", { token }), api("/rag/jobs", { token })]).then(([rows, recentJobs]) => {
      if (!cancelled) { setDocuments(rows); onDocuments(rows); setJobs(recentJobs.filter(job => job.status !== 'succeeded')); }
    }).catch((reason) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; };
  }, [token, onDocuments]);

  useEffect(() => {
    if (!jobs.some((job) => job.status === "queued" || job.status === "processing")) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const updated = await Promise.all(jobs.map((job) =>
          job.status === "queued" || job.status === "processing"
            ? api(`/rag/jobs/${job.id}`, { token }) : Promise.resolve(job)));
        if (!cancelled) {
          const completedNow = updated.filter((job, index) => jobs[index]?.status !== "succeeded" && job.status === "succeeded");
          setJobs((current) => {
            const fresh = new Map(updated.map((job) => [job.id, job]));
            const merged = current.map((job) => fresh.get(job.id) || job);
            const existing = new Set(merged.map((job) => job.id));
            return [...merged, ...updated.filter((job) => !existing.has(job.id))].filter(job => job.status !== 'succeeded');
          });
          if (completedNow.length) {
            notify(completedNow.length === 1
              ? `${completedNow[0].filename} was successfully added to your document library.`
              : `${completedNow.length} documents were successfully added to your library.`);
            await refreshDocuments();
          }
        }
      } catch (reason) { if (!cancelled) setError(reason.message); }
    }, 900);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [jobs, token, refreshDocuments, notify]);

  async function uploadFiles(files) {
    files = Array.from(files || []);
    if (!files.length) return;
    setBusy(true); setError("");
    try {
      for (const file of files) {
        const job = await apiUpload("/rag/documents", { token, file, fields: { source_url: sourceUrl, reuse_notes: reuseNotes, document_title: documentTitle } });
        if (job.status === 'succeeded') {
          notify(`${job.filename} was successfully added to your document library.`);
          await refreshDocuments();
        } else {
          setJobs((current) => [job, ...current]);
        }
      }
      setDocumentTitle(''); setSourceUrl(''); setReuseNotes('');
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }

  return <section id="knowledge" className="panel knowledge-panel">
    <div className="section-heading"><div><p className="eyebrow">CAMPAIGN KNOWLEDGE</p><h2>Add source documents</h2></div><span className="step-number">RAG</span></div>
    <p className="panel-intro">Upload sources for your campaigns. We’ll notify you when they’re ready. You can find saved documents in your library below.</p>
    <label className={`upload-drop ${busy ? "upload-busy" : ""}`} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); if (!busy) uploadFiles(event.dataTransfer.files); }}>
      <input type="file" multiple accept=".pdf,.txt,.md,.markdown" disabled={busy} onChange={(event) => { uploadFiles(event.target.files); event.target.value = ""; }} />
      <span className="upload-glyph">＋</span><strong>{busy ? "Adding document…" : "Choose a document or drop it here"}</strong>
      <small>PDF, TXT, or Markdown · up to 10 MB per file</small>
    </label>
    <div className="field-grid three upload-metadata"><label>Document title (optional)<input value={documentTitle} onChange={(event) => setDocumentTitle(event.target.value)} placeholder="Defaults to file name" /></label><label>Source URL (optional)<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://example.org/article" /></label><label>Reuse / license note (optional)<input value={reuseNotes} onChange={(event) => setReuseNotes(event.target.value)} placeholder="Attribution or reuse terms" /></label></div>
    {error && <p className="fact-check-error" role="alert">{error}</p>}
    {!!jobs.length && <div className="ingestion-jobs"><h3>Processing activity</h3>{jobs.slice(0, 4).map((job) => {
      const stepIndex = job.status === "succeeded" ? ingestionSteps.length - 1 : ingestionSteps.findIndex(([key]) => key === job.step);
      return <article className="ingestion-job" key={job.id}>
        <div className="ingestion-job-head"><div><strong>{job.filename}</strong><span className={`job-status ${job.status}`}>{job.status === "succeeded" ? "Ready" : job.status}</span></div>
          <small>{job.chunk_count || 0} chunks · {job.embedding_count || 0} vectors</small></div>
        <div className="ingestion-steps">{ingestionSteps.map(([key, label, icon], index) => {
          const complete = job.status === "succeeded" || index < stepIndex;
          const active = job.status !== "succeeded" && job.status !== "failed" && index === stepIndex;
          return <div className={`ingestion-step ${complete ? "complete" : ""} ${active ? "active" : ""}`} key={key}>
            <span className="ingestion-step-icon" key={`${job.id}-${job.step}-${key}`}>{complete ? "✓" : icon}</span><small>{label}</small>
          </div>;
        })}</div>
        {job.error_message && <p className="fact-check-error">{job.error_message}</p>}
        <div className="memory-line"><span>Worker RAM</span><b>{job.memory_mb == null ? "Measuring…" : `${job.memory_mb} MB`}</b><span>Peak process</span><b>{job.peak_memory_mb == null ? "—" : `${job.peak_memory_mb} MB`}</b></div>
      </article>;
    })}</div>}
    <details className="library-browser"><summary>Browse document library · {documents.length} document{documents.length === 1 ? '' : 's'}</summary>
    <div className="document-list"><div className="document-list-heading"><h3>Your documents</h3><span>{documents.length}</span></div>
      {documents.map((document) => <div className="document-row" key={document.document_id}><span className="document-mark">▤</span><div><strong>{document.title}</strong><small>{document.metadata?.original_filename || "Source document"} · {document.indexed_characters.toLocaleString()} passage characters (including overlap){document.metadata?.reuse_notes ? ` · ${document.metadata.reuse_notes}` : ""}{document.metadata?.source_url ? <> · <a href={document.metadata.source_url} target="_blank" rel="noreferrer">source ↗</a></> : ""}</small></div><span className="document-counts">{document.chunk_count} chunks<br />{document.embedding_count} vectors</span></div>)}
      {!documents.length && <p className="empty">Your source library will appear here after the first document is indexed.</p>}
    </div></details>
  </section>;
}

function Composer({ token, onCreated, notify, documents, models, imageLibrary }) {
  const [form, setForm] = useState(initialCampaign);
  const [busy, setBusy] = useState(false);
  const [retrieval, setRetrieval] = useState(null);
  const [imageFiles, setImageFiles] = useState([]);
  const [phase, setPhase] = useState("");
  const [addPictures, setAddPictures] = useState(false);
  const update = (field, value) => {
    setForm((current) => ({ ...current, [field]: value }));
    if (["title", "markdown", "rag_document_ids", "rag_top_k", "rag_min_similarity"].includes(field)) setRetrieval(null);
  };

  function togglePlatform(platform) {
    update("platforms", form.platforms.includes(platform)
      ? form.platforms.filter((item) => item !== platform)
      : [...form.platforms, platform]);
  }

  async function generate(event) {
    event.preventDefault();
    if (!form.platforms.length) return notify("Choose at least one platform", "error");
    if (form.generate_images && imageFiles.length > 3) return notify("Nano Banana accepts at most three reference photos. Remove an extra photo first.", "error");
    if (form.auto_select_images && !imageLibrary?.ready) return notify('Index your photo library before enabling automatic image selection.', 'error');
    setBusy(true);
    try {
      if (form.use_rag && !documents.length) throw new Error("Add at least one source document before enabling RAG.");
      let retrieved = null;
      if (form.use_rag) {
        retrieved = await api("/rag/retrieve", { token, method: "POST", body: {
          query: `${form.title}\n${form.markdown.slice(0, 8000)}`,
          document_ids: form.rag_document_ids,
          top_k: form.rag_top_k,
          min_similarity: form.rag_min_similarity,
        } });
        setRetrieval({ ...retrieved, used: false });
      }
      const post = await api("/posts", { token, method: "POST", body: { title: form.title, markdown: form.markdown } });
      const referenceIds = [];
      for (const file of imageFiles) {
        setPhase("Preparing product photos…");
        const photo = await apiUpload(`/posts/${post.id}/images`, { token, file });
        referenceIds.push(photo.id);
      }
      setPhase("Generating drafts…");
      await api(`/posts/${post.id}/variants`, {
        token,
        method: "POST",
        body: {
          platforms: form.platforms,
          generation_mode: form.generation_mode,
          audience: form.audience,
          goal: form.goal,
          tone: form.tone,
          call_to_action: form.call_to_action,
          use_rag: form.use_rag,
          rag_document_ids: form.rag_document_ids,
          rag_top_k: form.rag_top_k,
          rag_min_similarity: form.rag_min_similarity,
          vision_model: form.generation_mode === "llm" ? form.vision_model : null,
          reference_image_ids: referenceIds,
          generate_images: form.generate_images,
          auto_select_images: form.auto_select_images,
          image_no_match_policy: form.image_no_match_policy,
          image_model: "google/gemini-2.5-flash-image",
          image_style: form.image_style,
          image_instructions: form.image_instructions,
        },
      });
      if (retrieved) setRetrieval({ ...retrieved, used: true, campaignTitle: form.title });
      notify(form.generate_images ? `Drafts ready. Creating ${form.platforms.length} platform image${form.platforms.length === 1 ? "" : "s"}…` : `${form.generation_mode === "llm" ? "AI" : "Template"} drafts created`);
      setImageFiles([]);
      setAddPictures(false);
      setForm((current) => ({ ...initialCampaign, generation_mode: current.generation_mode, vision_model: current.vision_model }));
      onCreated(post.id);
    } catch (reason) {
      notify(reason.message, "error");
    } finally {
      setBusy(false);
      setPhase("");
    }
  }

  async function previewRetrieval() {
    if (!form.title.trim() || form.markdown.trim().length < 3) return notify("Add a title and source text to preview retrieval", "error");
    setBusy(true);
    try {
      const result = await api("/rag/retrieve", { token, method: "POST", body: {
        query: `${form.title}\n${form.markdown.slice(0, 8000)}`,
        document_ids: form.rag_document_ids,
        top_k: form.rag_top_k,
        min_similarity: form.rag_min_similarity,
      } });
      setRetrieval({ ...result, used: false });
      if (!result.chunks.length) notify("No passages met the cutoff. Generation will use your supplied source.");
    } catch (reason) { notify(reason.message, "error"); }
    finally { setBusy(false); }
  }

  return (
    <form id="compose" className="panel composer" onSubmit={generate}>
      <div className="section-heading"><div><p className="eyebrow">NEW CAMPAIGN</p><h2>Turn a story into a campaign</h2></div><span className="step-number">01</span></div>
      <div className="field-grid two">
        <label>Article title<input value={form.title} onChange={(e) => update("title", e.target.value)} placeholder="What is your article about?" required /></label>
        <label>Audience<input value={form.audience} onChange={(e) => update("audience", e.target.value)} required /></label>
      </div>
      <label>Article content<textarea className="source-input" value={form.markdown} onChange={(e) => update("markdown", e.target.value)} placeholder="Paste at least 20 characters of Markdown…" minLength="20" required /></label>
      <div className="field-grid three">
        <label>Generator<select value={form.generation_mode} onChange={(e) => { update("generation_mode", e.target.value); if (e.target.value !== "llm") { update("use_rag", false); update("generate_images", false); update("auto_select_images", false); setImageFiles([]); setAddPictures(false); } }}><option value="llm">AI · text and photos</option><option value="deterministic">Deterministic templates</option></select></label>
        <label>Goal<select value={form.goal} onChange={(e) => update("goal", e.target.value)}>{["awareness", "engagement", "traffic", "conversion"].map((v) => <option key={v}>{v}</option>)}</select></label>
        <label>Tone<select value={form.tone} onChange={(e) => update("tone", e.target.value)}>{["professional", "friendly", "educational", "energetic"].map((v) => <option key={v}>{v}</option>)}</select></label>
      </div>
      {form.generation_mode === "llm" && <section className="composer-images">
        <label>Campaign text / vision model<select value={form.vision_model} disabled={busy} onChange={(event) => update("vision_model", event.target.value)}>{models.vision.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
        <label className="rag-toggle"><input type="checkbox" checked={addPictures} disabled={busy || form.auto_select_images} onChange={(event) => { setAddPictures(event.target.checked); if (!event.target.checked) setImageFiles([]); }} /><span><strong>Add product pictures</strong><small>Attach views of the same product to guide the campaign text and optional images.</small></span></label>
        {addPictures && <><label className="upload-drop"><input type="file" multiple accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={(event) => {
          const files = Array.from(event.target.files || []); event.target.value = "";
          if (files.length > (form.generate_images ? 3 : 4)) return notify(`Choose at most ${form.generate_images ? 3 : 4} product photos`, "error");
          if (files.some((file) => file.size > 10 * 1024 * 1024)) return notify("Each photo must be 10 MB or smaller", "error");
          setImageFiles(files);
        }} /><strong>Choose product photos</strong><small>JPEG, PNG or WebP · up to 10 MB each · up to 3 photos for image generation</small></label>
        <div className="reference-grid">{imageFiles.map((file, index) => <div className="reference-photo" key={`${file.name}-${index}`}><LocalImagePreview file={file} /><strong>{file.name}</strong><button type="button" className="text-button" disabled={busy} onClick={() => setImageFiles((files) => files.filter((_, position) => position !== index))}>Remove</button></div>)}</div></>}
        <label className="rag-toggle"><input type="checkbox" checked={form.generate_images} disabled={busy} onChange={(event) => { update("generate_images", event.target.checked); if (!event.target.checked) update('auto_select_images', false); }} /><span><strong>Generate campaign images</strong><small>The campaign LLM plans a related image for each selected platform. Nano Banana creates the visuals automatically.</small></span></label>
        {form.generate_images && <div className="campaign-image-options">
          <label className="rag-toggle"><input type="checkbox" checked={form.auto_select_images} disabled={busy || !imageLibrary?.ready} onChange={event => {
            update('auto_select_images', event.target.checked);
            if (event.target.checked) { setImageFiles([]); setAddPictures(false); update('image_style', 'lifestyle scene'); }
          }} /><span><strong>Automatically find photos from my library</strong><small>{imageLibrary?.ready ? `${imageLibrary.ready} searchable photos. AI checks five candidates, selects up to two scene references, and finalizes the image plans. Includes an additional vision-model call.` : 'Add and index photos in the Photo library above first.'}</small></span></label>
          {form.auto_select_images && <label>If no suitable photo is found<select value={form.image_no_match_policy} disabled={busy} onChange={e => update('image_no_match_policy', e.target.value)}><option value="stop">Stop the image attempt and explain why</option><option value="illustration">Generate a general topic illustration</option></select></label>}
          <label>Visual style<select value={form.image_style} disabled={busy} onChange={(event) => update("image_style", event.target.value)}>{["studio product photography", "lifestyle scene", "minimal editorial"].map((style) => <option key={style}>{style}</option>)}</select></label>
          <label>Design preferences (optional)<input maxLength={1000} value={form.image_instructions} disabled={busy} onChange={(event) => update("image_instructions", event.target.value)} placeholder="Warm living room, neutral colors, lamp beside a reading chair…" /></label>
          <p className="fact-check-hint">Nano Banana · about $0.04 per image in our lamp tests · {form.platforms.length} image{form.platforms.length === 1 ? "" : "s"} requested. {form.platforms.map((platform) => `${platform === "linkedin" ? "LinkedIn" : platform === "x" ? "X" : platform === "instagram" ? "Instagram" : "Discord"}: ${["linkedin", "instagram"].includes(platform) ? "square 1:1" : "landscape 16:9"}`).join(" · ")}</p>
          {!imageFiles.length && !form.auto_select_images && <p className="fact-check-hint">Without a product photo, the model will create a topic illustration.</p>}
          {imageFiles.length > 3 && <p className="fact-check-error">Remove an extra photo: Nano Banana accepts at most three.</p>}
        </div>}
      </section>}
      <label>Call to action<input value={form.call_to_action} onChange={(e) => update("call_to_action", e.target.value)} required /></label>
      <div className="rag-controls">
        <label className="rag-toggle"><input type="checkbox" checked={form.use_rag} onChange={(e) => update("use_rag", e.target.checked)} disabled={form.generation_mode !== "llm" || !documents.length} /><span><strong>Use retrieved knowledge (RAG)</strong><small>{form.generation_mode !== "llm" ? "RAG is available with the LLM generator." : documents.length ? "Retrieve supporting passages from your indexed documents." : "Add and index a source document above to enable RAG."}</small></span></label>
        {form.use_rag && <div className="rag-options">
          <label>Passages to retrieve<select value={form.rag_top_k} onChange={(e) => update("rag_top_k", Number(e.target.value))}>{[3, 5, 8, 10].map((count) => <option key={count} value={count}>{count}</option>)}</select></label>
          <label>Minimum similarity<input type="number" min="0" max="1" step="0.01" value={form.rag_min_similarity} onChange={(event) => update("rag_min_similarity", Number(event.target.value))} /><small>Starting cutoff: 0.70. This is a cosine score, not a probability of relevance.</small></label>
          <button type="button" className="button subtle" disabled={busy || !documents.length} onClick={previewRetrieval}>Preview passages</button>
          <div className="rag-document-picker"><strong>Search these documents</strong><small>Choose none to search your whole library.</small>
            {documents.map((doc) => <label key={doc.document_id}><input type="checkbox" checked={form.rag_document_ids.includes(doc.document_id)} onChange={() => update("rag_document_ids", form.rag_document_ids.includes(doc.document_id) ? form.rag_document_ids.filter((id) => id !== doc.document_id) : [...form.rag_document_ids, doc.document_id])} />{doc.title}</label>)}
            {!documents.length && <span className="empty">Add documents above to enable retrieval.</span>}
          </div>
        </div>}
      </div>
      {retrieval && <section className="retrieval-preview"><div className="retrieval-heading"><div><p className="eyebrow">{retrieval.used ? "EVIDENCE USED" : "RETRIEVAL PREVIEW"}</p><h3>{retrieval.chunks.length} passages · {retrieval.model}</h3></div><span className="retrieval-chip">{retrieval.used ? "Attached to campaign" : "Preview only"}</span></div>
        <p className="fact-check-hint">Only passages with similarity ≥ {retrieval.min_similarity?.toFixed(2)} are included.{!retrieval.chunks.length && " None qualified; the campaign uses your supplied source only."}</p>
        {retrieval.chunks.map((chunk, index) => <article className="retrieved-chunk" key={`${chunk.document_id}-${chunk.chunk_index}`}><header><b>{index + 1}. {chunk.title}</b><span>similarity {chunk.similarity.toFixed(3)} · chunk {chunk.chunk_index + 1}</span></header><p>{chunk.text}</p>{chunk.source_url && <a href={chunk.source_url} target="_blank" rel="noreferrer">Open source ↗</a>}</article>)}
      </section>}
      <div className="composer-footer">
        <div className="platform-picker">{["x", "linkedin", "discord", "instagram"].map((platform) => <button type="button" key={platform} className={form.platforms.includes(platform) ? "platform active" : "platform"} onClick={() => togglePlatform(platform)}>{platform === "x" ? "𝕏" : platform === "linkedin" ? "in" : "◉"} {platform}</button>)}</div>
        <button className="button primary" disabled={busy}>{busy ? (phase || (form.use_rag ? "Retrieving & generating…" : "Generating…")) : form.generate_images ? "Generate campaign + images →" : "Generate campaign →"}</button>
      </div>
    </form>
  );
}

function FactCheckPanel({ variant, content, token, onRefresh }) {
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [suggestion, setSuggestion] = useState(null);
  const [fixBusy, setFixBusy] = useState("");
  const [showSuggestion, setShowSuggestion] = useState(true);
  const unsaved = content !== variant.content;
  const outdated = check && (check.stale || content !== check.draft_snapshot);
  const running = check?.outcome === "processing";

  useEffect(() => {
    let cancelled = false;
    let timer;
    async function load() {
      try {
        const [next, proposal] = await Promise.all([
          api(`/variants/${variant.id}/fact-check`, { token }),
          api(`/variants/${variant.id}/fix-suggestions/latest`, { token }),
        ]);
        if (cancelled) return;
        setCheck(next); setSuggestion(proposal);
        if (next?.outcome === "processing" || proposal?.status === "processing") timer = window.setTimeout(load, 3000);
      } catch (error) { if (!cancelled) setLoadError(error.message); }
    }
    load();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [variant.id, variant.content, token, reload]);

  async function checkFacts() {
    setChecking(true); setLoadError("");
    try {
      setCheck(await api(`/variants/${variant.id}/fact-check`, { token, method: "POST" }));
      await onRefresh();
    } catch (error) { setLoadError(error.message); }
    finally { setChecking(false); setReload((value) => value + 1); }
  }

  async function suggestFix() {
    setFixBusy("suggest"); setLoadError(""); setShowSuggestion(true);
    try {
      setSuggestion(await api(`/variants/${variant.id}/fix-suggestions`, {
        token, method: "POST", body: { fact_check_id: check.id },
      }));
    } catch (error) { setLoadError(error.message); }
    finally { setFixBusy(""); setReload((value) => value + 1); }
  }

  async function applyFix() {
    setFixBusy("apply"); setLoadError("");
    try {
      await api(`/variants/${variant.id}/fix-suggestions/${suggestion.id}/apply`, { token, method: "POST" });
      await onRefresh(); setReload((value) => value + 1);
    } catch (error) { setLoadError(error.message); }
    finally { setFixBusy(""); }
  }

  const suggestionOutdated = suggestion && (suggestion.stale || content !== suggestion.draft_snapshot);
  const suggesting = fixBusy === "suggest" || suggestion?.status === "processing";
  const label = outdated ? "Outdated check" : checking || running ? "Checking facts…"
    : check ? ({ needs_review: "Needs review", no_issues: "No issues detected", unknown: "No factual claims found",
        failed: "Evaluation failed", processing: "Checking facts…" }[check.outcome]) : "Not checked";
  const flagged = (check?.claims || []).filter((claim) => claim.verdict !== "supported");
  const supported = (check?.claims || []).filter((claim) => claim.verdict === "supported");
  return <section className={`fact-check ${outdated ? "outdated" : check?.outcome || "unchecked"}`} aria-label="Fact check">
    <div className="fact-check-header"><strong aria-live="polite">{label}</strong>
      <button type="button" className="button subtle" disabled={checking || running || unsaved || !!fixBusy || suggesting}
        onClick={checkFacts}>{checking || running ? "Checking…" : check ? "Recheck facts" : "Check facts"}</button>
    </div>
    <p className="fact-check-hint">{unsaved ? "Save your edit before checking facts." : outdated
      ? "This result applies to an older draft or source. Recheck the saved draft."
      : "Checks the saved draft against your article. Uses AI credits; approval is your decision."}</p>
    {loadError && <p role="alert" className="fact-check-error">{loadError}</p>}
    {check?.error_message && <p role="alert" className="fact-check-error">{check.error_message}</p>}
    {check?.outcome === "unknown" && <p className="fact-check-hint">No factuality score is available. This is not a passing evaluation.</p>}
    {check?.outcome === "no_issues" && !outdated && <p className="fact-check-hint">The judge found no issues in the claims it extracted. It may still miss errors.</p>}
    {flagged.map((claim, index) => <div className="fact-claim" key={index}>
      <span className={`fact-verdict ${claim.verdict}`}>{claim.verdict}</span>
      <p className="fact-claim-text">{claim.claim}</p><p>{claim.reason}</p>
      {claim.source_excerpt ? <blockquote><small>Source evidence</small>{claim.source_excerpt}</blockquote>
        : <p className="fact-check-hint">No supporting source passage identified.</p>}
    </div>)}
    {flagged.length > 0 && variant.status === "draft" && <div className="fix-actions">
      <button type="button" className="button subtle" onClick={suggestFix}
        disabled={unsaved || outdated || checking || running || !!fixBusy || suggesting}>
        {suggesting ? "Suggesting…" : "Suggest a fix"}</button>
      <p className="fact-check-hint">One additional AI call. Review the changes before applying.</p>
    </div>}
    {suggestion && showSuggestion && <div className="fix-preview" aria-label="Suggested correction">
      <div className="fact-check-header"><strong>{suggestion.applied_at ? "Suggestion applied" : suggestionOutdated
        ? "Outdated suggestion" : suggestion.status === "processing" ? "Preparing suggestion…" : "Suggested correction"}</strong>
        <button type="button" className="text-button" onClick={() => setShowSuggestion(false)}>Dismiss</button></div>
      {suggestion.error_message && <p role="alert" className="fact-check-error">{suggestion.error_message}</p>}
      {suggestion.suggested_content && <div className="fix-comparison">
        <div><h4>Before</h4><p>{suggestion.draft_snapshot}</p></div>
        <div><h4>Suggested</h4><p>{suggestion.suggested_content}</p></div>
      </div>}
      {suggestionOutdated && !suggestion.applied_at && <p className="fact-check-hint">The draft, article, or fact check changed. Request a fresh suggestion.</p>}
      {suggestion.status === "succeeded" && !suggestion.applied_at && <button type="button" className="button primary"
        disabled={suggestionOutdated || unsaved || checking || running || !!fixBusy || variant.status !== "draft"}
        onClick={applyFix}>{fixBusy === "apply" ? "Applying…" : "Apply suggestion"}</button>}
      {suggestion.applied_at && <p className="fact-check-hint">{variant.fact_recheck_required ? "Saved as a draft. Recheck facts before approving." : "Saved as a draft. Approval is your decision."}</p>}
      {suggestion.reported_cost_usd !== null && <p className="fact-check-hint">Suggestion cost: ${suggestion.reported_cost_usd.toFixed(4)}</p>}
    </div>}
    {variant.fact_recheck_required && <p className="fact-check-hint">This AI-edited draft needs a fresh fact check before approval.</p>}
    {supported.length > 0 && <details className="fact-supported"><summary>{supported.length} supported claim{supported.length === 1 ? "" : "s"}</summary>
      {supported.map((claim, index) => <div className="fact-claim" key={index}><p className="fact-claim-text">{claim.claim}</p><p>{claim.reason}</p><blockquote>{claim.source_excerpt}</blockquote></div>)}
    </details>}
    {check?.completed_at && <p className="fact-check-hint">Checked {new Date(check.completed_at).toLocaleString()}
      {check.reported_cost_usd !== null && ` · $${check.reported_cost_usd.toFixed(4)}`}</p>}
  </section>;
}

function VariantCard({ variant, schedule, delivery, token, refresh, notify, models, postId, publisher }) {
  const [content, setContent] = useState(variant.content);
  const [showReject, setShowReject] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [publishAt, setPublishAt] = useState(() => {
    const date = new Date(Date.now() + 5 * 60 * 1000);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  });
  const [busy, setBusy] = useState("");
  useEffect(() => { setContent(variant.content); }, [variant.content]);
  const limit = variant.platform === "x" ? 280 : variant.platform === "discord" ? 2000 : variant.platform === "instagram" ? 2200 : 3000;

  const accountChanged = schedule?.destination_account_id && publisher?.account_id
    && schedule.destination_account_id !== publisher.account_id;

  async function schedulePost() {
    const date = new Date(publishAt);
    if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) {
      throw new Error("Choose a future date and time for this post.");
    }
    return api(`/variants/${variant.id}/schedule`, { token, method: "POST", body: { publish_at: date.toISOString() } });
  }

  async function action(name, request) {
    setBusy(name);
    try { await request(); notify(`${name} complete`); await refresh(); }
    catch (reason) { notify(reason.message, "error"); }
    finally { setBusy(""); }
  }

  return (
    <article className="variant-card">
      <header><div className={`platform-icon ${variant.platform}`}>{variant.platform === "x" ? "𝕏" : variant.platform === "linkedin" ? "in" : "◉"}</div><div><h3>{variant.platform}</h3><StatusPill status={variant.status} /></div></header>
      <textarea value={content} disabled={variant.status !== "draft"} onChange={(e) => setContent(e.target.value)} />
      <div className="character-count"><span>{content.length} / {limit}</span><span>{content.split(/\s+/).filter((word) => word.startsWith("#")).length} hashtags</span></div>
      <VisualStudio postId={postId} variant={variant} token={token} models={models} notify={notify} refreshPost={refresh} />
      <FactCheckPanel variant={variant} content={content} token={token} onRefresh={refresh} />
      {variant.status === "draft" && <div className="button-row">
        <button className="button subtle" disabled={!!busy || content === variant.content} onClick={() => action("Save", () => api(`/variants/${variant.id}`, { token, method: "PATCH", body: { content } }))}>Save edit</button>
        <button className="button approve" disabled={!!busy || content !== variant.content || variant.fact_recheck_required || ((variant.image_required || variant.platform === "instagram") && !variant.image_attached)} onClick={() => action("Approval", () => api(`/variants/${variant.id}/approve`, { token, method: "POST" }))}>Approve text{variant.image_attached ? " + image" : ""} ✓</button>
        <button className="button reject" disabled={!!busy} onClick={() => setShowReject((visible) => !visible)}>Reject</button>
      </div>}
      {variant.status === "draft" && showReject && <div className="reject-box">
        <input value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} placeholder="Why is this draft unsuitable?" minLength="3" />
        <button className="button reject" disabled={!!busy || rejectReason.trim().length < 3} onClick={() => action("Rejection", () => api(`/variants/${variant.id}/reject`, { token, method: "POST", body: { reason: rejectReason.trim() } }))}>Confirm reject</button>
      </div>}
      {variant.status === "rejected" && variant.rejection_reason && <div className="rejection-note"><b>Rejection reason</b><span>{variant.rejection_reason}</span></div>}
      <p className="fact-check-hint">{publisher?.mode === "mock" ? "Test publisher: delivery is simulated; nothing is sent to this platform." : publisher?.configured === false ? (variant.platform === "instagram" ? "Connect Instagram in Connected accounts before scheduling." : "Connect a Discord webhook before live publishing.") : !publisher ? "Checking publishing connection…" : `Live publisher${publisher?.account ? ` · @${publisher.account}` : ""}`}{variant.image_attached ? " · This post includes the image shown above." : " · Text-only post."}</p>
      {variant.status === "approved" && <div className="schedule-box">
        <input aria-label="Publish date and time" type="datetime-local" value={publishAt} onChange={(e) => setPublishAt(e.target.value)} />
        <button className="button approve" disabled={!!busy || publisher?.configured !== true} onClick={() => action("Scheduling", schedulePost)}>{publisher?.mode === "mock" ? "Schedule test delivery" : "Schedule post"}</button>
      </div>}
      {variant.status === "approved" && <p className="fact-check-hint">Times use your device timezone: {Intl.DateTimeFormat().resolvedOptions().timeZone}.</p>}
      {schedule && <div className="schedule-state"><span>Schedule #{schedule.id}{schedule.destination_account_name ? ` · @${schedule.destination_account_name}` : ""}{schedule.image_attached ? " · text + image" : " · text only"}</span><StatusPill status={schedule.status} />{schedule.status === "pending" && <button disabled={!!busy || publisher?.configured !== true || accountChanged} className="text-button" onClick={() => action(publisher?.mode === "mock" ? "Test delivery" : "Publishing", () => api(`/schedules/${schedule.id}/publish`, { token, method: "POST" }))}>{publisher?.mode === "mock" ? "Run test delivery" : "Publish now"}</button>}</div>}
      {schedule?.publish_at && <p className="fact-check-hint">Scheduled for {new Date(schedule.publish_at).toLocaleString()}.</p>}
      {accountChanged && <p className="fact-check-error" role="alert">This schedule belongs to @{schedule.destination_account_name}. Reconnect that account before publishing.</p>}
      {variant.platform === "instagram" && <p className="fact-check-hint">Instagram requires an image. The reviewed image is delivered as a JPEG copy. Keep the image gateway and tunnel running until publishing completes.</p>}
      {busy && <p role="status">{busy}… {busy === "Publishing" ? "Preparing the image and waiting for Instagram. This can take up to 3 minutes." : ""}</p>}
      {delivery?.stage && <p role="status">Instagram delivery: {delivery.stage.replaceAll("_", " ")}</p>}
      {(delivery?.error_message || schedule?.last_error) && <p className="fact-check-error">{delivery?.error_message || schedule.last_error}</p>}
      {delivery?.external_url && <a href={delivery.external_url} target="_blank" rel="noreferrer">View published post ↗</a>}
      {schedule?.status === "failed" && !schedule.last_error?.startsWith("Outcome uncertain") && <button className="text-button" disabled={!!busy} onClick={() => action("Retry queued", () => api(`/schedules/${schedule.id}/retry`, { token, method: "POST" }))}>Retry delivery</button>}
    </article>
  );
}

function Workspace({ token, email, logout }) {
  const [activePage, setActivePage] = useState(() => {
    const saved = window.location.hash.slice(1);
    const page = ({ knowledge: 'documents', compose: 'generate' })[saved] || saved;
    return workspacePages.some(item => item.id === page) ? page : 'generate';
  });
  const [posts, setPosts] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [variants, setVariants] = useState([]);
  const [generations, setGenerations] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [publishHistory, setPublishHistory] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [ragDocuments, setRagDocuments] = useState([]);
  const [imageLibrary, setImageLibrary] = useState(null);
  const [notice, setNotice] = useState(null);
  const noticeTimer = useRef(null);
  const [loadError, setLoadError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [visualModels, setVisualModels] = useState(INITIAL_MODELS);
  const [publishers, setPublishers] = useState([{ platform: "x", mode: "mock" }, { platform: "linkedin", mode: "mock" }, { platform: "discord", mode: "live", configured: false }]);

  const notify = useCallback((message, type = "success") => {
    window.clearTimeout(noticeTimer.current);
    setNotice({ message, type });
    noticeTimer.current = window.setTimeout(() => setNotice(null), 6000);
  }, []);
  useEffect(() => () => window.clearTimeout(noticeTimer.current), []);
  useEffect(() => {
    window.history.replaceState(null, '', `#${activePage}`);
    document.getElementById('workspace-title')?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [activePage]);

  const refreshAll = useCallback(async (preferredId) => {
    try {
      const [postRows, scheduleRows, historyRows, summary] = await Promise.all([
        api("/posts", { token }), api("/schedules", { token }), api("/publish-history", { token }),
        api("/analytics/summary", { token }),
      ]);
      setPosts(postRows); setSchedules(scheduleRows); setPublishHistory(historyRows);
      setAnalytics(summary); setLoadError(""); setLoaded(true);
      const id = preferredId || selectedId || postRows[0]?.id;
      setSelectedId(id || null);
      if (id) {
        const [variantRows, generationRows] = await Promise.all([
          api(`/posts/${id}/variants`, { token }), api(`/posts/${id}/generations`, { token }),
        ]);
        setVariants(variantRows); setGenerations(generationRows);
      } else { setVariants([]); setGenerations([]); }
    } catch (reason) { setLoadError(reason.message); setLoaded(true); }
  }, [token, selectedId, notify]);

  useEffect(() => { refreshAll(); }, []); // Load once; actions perform explicit refreshes.
  useEffect(() => {
    let cancelled = false;
    Promise.all([api("/visual-models", { token }), api("/schedules/publishers", { token })]).then(([models, publisherRows]) => { if (!cancelled) { setVisualModels(models); setPublishers(publisherRows); } })
      .catch((reason) => { if (!cancelled) notify(reason.message, "error"); });
    return () => { cancelled = true; };
  }, [token, notify]);
  useEffect(() => {
    if (!schedules.some((row) => ["pending", "processing"].includes(row.status))) return;
    const timer = window.setInterval(() => refreshAll(selectedId), 5000);
    return () => window.clearInterval(timer);
  }, [schedules, refreshAll, selectedId]);
  const schedulesByVariant = useMemo(() => Object.fromEntries(schedules.map((item) => [item.variant_id, item])), [schedules]);
  const selectedPost = posts.find((post) => post.id === selectedId);
  const refreshPublishers = useCallback(async () => {
    setPublishers(await api("/schedules/publishers", { token }));
  }, [token]);
  const page = workspacePages.find(item => item.id === activePage);

  return (
    <div className="app-shell">
      <nav><div className="brand"><span className="brand-mark">S</span><span>Social Media Studio<small>Campaign operations</small></span></div><div className="nav-actions"><span className={`api-live ${loadError ? "unavailable" : ""}`}><i /> {loadError ? "Connection issue" : loaded ? "Workspace loaded" : "Connecting…"}</span><span className="user-chip">{email.slice(0, 1).toUpperCase()}</span><button className="text-button" onClick={logout}>Sign out</button></div></nav>
      {notice && <div role={notice.type === "error" ? "alert" : "status"} className={`toast ${notice.type}`}>{notice.message}</div>}
      <div className="workspace-layout">
        <aside className="workspace-sidebar"><p className="eyebrow">YOUR STUDIO</p><div role="navigation" aria-label="Workspace sections">{workspacePages.map(item => <button type="button" key={item.id} className={`sidebar-link ${activePage === item.id ? 'active' : ''}`} aria-current={activePage === item.id ? 'page' : undefined} onClick={() => setActivePage(item.id)}><span className="sidebar-icon" aria-hidden="true">{item.icon}</span><span>{item.label}</span>{item.id === 'documents' && <small>{ragDocuments.length}</small>}{item.id === 'images' && <small>{imageLibrary?.total ?? '—'}</small>}</button>)}</div></aside>
      <main className="dashboard workspace-content">
        <header className="page-heading"><div><p className="eyebrow">WORKSPACE</p><h1 id="workspace-title" tabIndex={-1}>{page.label}</h1><p>{page.description}</p></div>{activePage === 'overview' && <div className="metric-row"><div><b>{analytics?.campaigns ?? "—"}</b><span>Campaigns</span></div><div><b>{analytics?.variants_by_status.draft ?? "—"}</b><span>Drafts</span></div><div><b>{analytics ? Object.values(analytics.deliveries_by_status).reduce((total, count) => total + count, 0) : "—"}</b><span>Deliveries</span></div></div>}</header>
        {loadError && <div className="alert error workspace-error" role="alert"><span>{loadError}</span><button className="text-button" onClick={() => refreshAll(selectedId)}>Try again</button></div>}
        {!loaded && <p role="status">Loading your campaigns…</p>}
        <div className="workspace-page" hidden={activePage !== 'accounts'}><InstagramConnection token={token} notify={notify} onChange={refreshPublishers} /></div>
        {analytics && <section hidden={activePage !== 'overview'} className="panel workspace-analytics workspace-page" aria-label="Workspace analytics">
          <div className="section-heading compact"><div><p className="eyebrow">WORKSPACE ANALYTICS</p><h2>Operations at a glance</h2></div><button className="text-button" onClick={() => refreshAll(selectedId)}>Refresh</button></div>
          <p className="empty">Lifetime totals across all your campaigns.</p>
          <dl className="analytics-metrics">
            <div><dt>Awaiting approval</dt><dd>{analytics.variants_by_status.draft}</dd></div>
            <div><dt>Pending schedules</dt><dd>{analytics.schedules_by_status.pending}</dd></div>
            <div><dt>Failed schedules</dt><dd>{analytics.schedules_by_status.failed}</dd></div>
            <div><dt>Delivery success</dt><dd>{analytics.delivery_success_rate === null ? "—" : `${(analytics.delivery_success_rate * 100).toFixed(1)}%`}</dd></div>
            <div><dt>Generation runs</dt><dd>{analytics.ai_usage.runs}</dd></div>
            <div><dt>Generation tokens recorded</dt><dd>{(analytics.ai_usage.input_tokens + analytics.ai_usage.output_tokens).toLocaleString()}</dd></div>
            <div><dt>Reported generation cost</dt><dd>{analytics.ai_usage.reported_cost_usd === null ? "—" : `$${analytics.ai_usage.reported_cost_usd.toFixed(4)}`}</dd></div>
            <div><dt>Average generation latency</dt><dd>{analytics.ai_usage.average_latency_ms === null ? "—" : `${(analytics.ai_usage.average_latency_ms / 1000).toFixed(2)}s`}</dd></div>
          </dl>
          <p className="empty">Delivery success uses the latest outcome of each completed or failed delivery, excluding processing. Cost is available for {analytics.ai_usage.runs_with_reported_cost} of {analytics.ai_usage.runs} AI runs; missing usage is excluded. These totals cover generation; fact-check costs appear on each draft. These are operational metrics, not audience engagement.</p>
        </section>}
        <div className="workspace-page" hidden={activePage !== 'documents'}><KnowledgeBasePanel token={token} onDocuments={setRagDocuments} notify={notify} /></div>
        <div className="workspace-page" hidden={activePage !== 'images'}><ImageLibrary token={token} notify={notify} onChange={setImageLibrary} /></div>
        <div className="workspace-page" hidden={activePage !== 'generate'}><Composer token={token} notify={notify} documents={ragDocuments} models={visualModels} imageLibrary={imageLibrary} onCreated={(id) => { refreshAll(id); setActivePage('review'); }} /></div>

        <section id="review" hidden={activePage !== 'review'} className="workspace-grid workspace-page">
          <aside className="panel campaign-list"><div className="section-heading compact"><div><p className="eyebrow">LIBRARY</p><h2>Campaigns</h2></div><span>{posts.length}</span></div>{posts.map((post) => <button key={post.id} className={selectedId === post.id ? "campaign active" : "campaign"} onClick={() => refreshAll(post.id)}><b>{post.title}</b><small>#{post.id} · {new Date(post.created_at).toLocaleDateString()}</small></button>)}{!posts.length && loaded && !loadError && <p className="empty">Your generated campaigns will appear here.</p>}</aside>
          <section className="campaign-detail">
            <div className="section-heading"><div><p className="eyebrow">REVIEW QUEUE</p><h2>{selectedPost?.title || "No campaign selected"}</h2></div><span className="step-number">02</span></div>
            <div className="variant-grid">{variants.map((variant) => <VariantCard key={variant.id} variant={variant} schedule={schedulesByVariant[variant.id]} delivery={publishHistory.find((item) => item.delivery.schedule_slot_id === schedulesByVariant[variant.id]?.id)?.delivery} token={token} refresh={() => refreshAll(selectedId)} notify={notify} models={visualModels} postId={selectedPost?.id || selectedId} publisher={publishers.find((item) => item.platform === variant.platform)} />)}{!variants.length && <div className="empty large">Your platform drafts will appear here. <button type="button" className="text-button" onClick={() => setActivePage('generate')}>Generate a campaign</button></div>}</div>
          </section>
        </section>

        <section id="activity" hidden={activePage !== 'activity'} className="insights-grid workspace-page">
          <div className="panel"><div className="section-heading compact"><div><p className="eyebrow">AI OBSERVABILITY</p><h2>Generation audit</h2></div><span className="step-number">03</span></div>{generations.map((run) => <div className="audit-run" key={run.id}><div className="audit-row"><div><b>{run.model}</b><span>{run.platforms} · {run.prompt_version}</span></div><div className="audit-metrics"><span>{run.input_tokens ?? "—"} in</span><span>{run.output_tokens ?? "—"} out</span><span>{run.latency_ms ? `${(run.latency_ms / 1000).toFixed(1)}s` : "—"}</span><StatusPill status={run.status} /></div></div>{run.rag_enabled && <details className="audit-rag"><summary>RAG evidence · {run.rag_context?.length || 0} passages · cutoff {run.rag_min_similarity ?? "legacy"}</summary><small>{run.rag_model_id}</small>{!run.rag_context?.length && <p>No passages passed the cutoff; source-only generation was used.</p>}{(run.rag_context || []).map((chunk, index) => <article key={`${chunk.document_id}-${chunk.chunk_index}`}><header><b>{index + 1}. {chunk.title}</b><span>similarity {chunk.similarity.toFixed(3)} · chunk {chunk.chunk_index + 1}</span></header><p>{chunk.text}</p>{chunk.source_url && <a href={chunk.source_url} target="_blank" rel="noreferrer">Open source ↗</a>}</article>)}</details>}</div>)}{!generations.length && <p className="empty">LLM runs for this campaign will appear here.</p>}</div>
          <div className="panel"><div className="section-heading compact"><div><p className="eyebrow">DELIVERY LOG</p><h2>Publish history</h2></div><button className="text-button" onClick={() => refreshAll(selectedId)}>Refresh</button></div>{publishHistory.slice(0, 6).map(({ delivery, attempts }) => <div className="audit-row" key={delivery.id}><div><b>{delivery.platform} · delivery #{delivery.id}</b><span>{attempts.length} attempt{attempts.length === 1 ? "" : "s"}</span></div><div><StatusPill status={delivery.status} />{delivery.external_url && <a href={delivery.external_url} target="_blank" rel="noreferrer">View post ↗</a>}{delivery.error_message && <p className="fact-check-error">{delivery.error_message}</p>}</div></div>)}{!publishHistory.length && <p className="empty">Published posts and attempts will appear here.</p>}</div>
        </section>
      </main>
      </div>
    </div>
  );
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem("studio_token"));
  const [email, setEmail] = useState(() => localStorage.getItem("studio_email") || "user");
  const [authMessage, setAuthMessage] = useState("");
  function authenticated(nextToken, nextEmail) { localStorage.setItem("studio_token", nextToken); localStorage.setItem("studio_email", nextEmail); setToken(nextToken); setEmail(nextEmail); setAuthMessage(""); }
  function logout() { localStorage.removeItem("studio_token"); localStorage.removeItem("studio_email"); setToken(null); }
  useEffect(() => {
    const expired = (event) => {
      if (event.detail !== token) return;
      logout();
      setAuthMessage("Your session expired. Sign in again to continue.");
    };
    window.addEventListener("studio:session-expired", expired);
    return () => window.removeEventListener("studio:session-expired", expired);
  }, [token]);
  return token ? <Workspace token={token} email={email} logout={logout} /> : <AuthScreen onAuthenticated={authenticated} message={authMessage} />;
}
