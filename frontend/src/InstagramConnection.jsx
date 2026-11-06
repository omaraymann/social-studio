import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

function captureReturn() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  if (params.get("instagram_oauth") !== "1") return null;
  const result = { state: params.get("state"), code: params.get("code"), denied: params.get("denied") === "1" };
  // Remove the one-use code immediately; it never enters browser storage or server query logs.
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return result;
}

let pendingReturn = captureReturn();

export default function InstagramConnection({ token, notify, onChange }) {
  const [connection, setConnection] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const load = useCallback(async () => {
    const result = await api("/instagram/connection", { token });
    setConnection(result);
    return result;
  }, [token]);

  useEffect(() => { load().catch((reason) => setError(reason.message)); }, [load]);
  useEffect(() => {
    if (!pendingReturn) return;
    const result = pendingReturn;
    pendingReturn = null;
    if (!result.state) { setError("Instagram did not return a connection request. Please connect again."); return; }
    setBusy("Finishing connection");
    api("/instagram/oauth/complete", { token, method: "POST", credentials: "include", body: result })
      .then(async (response) => {
        notify(response.cancelled ? "Instagram connection cancelled" : `Connected @${response.username}`);
        await load();
        await onChange();
      })
      .catch((reason) => setError(reason.message))
      .finally(() => setBusy(""));
  }, [token, load, onChange, notify]);

  async function connect() {
    setError(""); setBusy("Opening Instagram");
    try {
      const result = await api("/instagram/oauth/start", { token, method: "POST", credentials: "include" });
      window.location.assign(result.authorization_url);
    } catch (reason) { setError(reason.message); setBusy(""); }
  }

  async function disconnect() {
    setError(""); setBusy("Disconnecting");
    try {
      await api("/instagram/connection", { token, method: "DELETE" });
      await load(); await onChange();
      setConfirmDisconnect(false);
      notify("Instagram disconnected. Future posts need a connected account.");
    } catch (reason) { setError(reason.message); }
    finally { setBusy(""); }
  }

  async function verify() {
    setError(""); setBusy("Checking connection");
    try {
      const result = await api("/instagram/connection/verify", { token, method: "POST" });
      notify(`Instagram connection verified: @${result.username}`);
    } catch (reason) { setError(reason.message); }
    finally {
      try { await load(); await onChange(); }
      catch (reason) { setError(reason.message); }
      setBusy("");
    }
  }

  const status = connection?.connected ? "Connected" : ({ expired: "Access expired", needs_reconnect: "Reconnect needed", disconnected: "Not connected" }[connection?.status] || (error ? "Unavailable" : "Loading…"));

  return <section id="accounts" className="panel instagram-connection" aria-label="Instagram connection">
    <div className="section-heading compact"><div><p className="eyebrow">CONNECTED ACCOUNTS</p><h2>Instagram</h2></div>
      <span className={`pill ${connection?.connected ? "pill-succeeded" : "pill-draft"}`}>{status}</span></div>
    {connection?.username && <p className="connected-handle"><strong>@{connection.username}</strong></p>}
    {["expired", "needs_reconnect"].includes(connection?.status) && <p className="fact-check-error">Reconnect this account to resume publishing.</p>}
    <p className="fact-check-hint">Connect a Business or Creator account through Instagram. Your password is entered only on Instagram’s website.</p>
    {connection?.expires_at && <p className="fact-check-hint">Access expires {new Date(connection.expires_at).toLocaleString()}. The publishing worker attempts to renew active connections before expiry.</p>}
    {connection?.connected && !connection.expires_at && <p className="fact-check-hint">Your existing account is connected. Reconnect to enable automatic token renewal.</p>}
    {connection && !connection.oauth_configured && <p className="fact-check-hint">Instagram sign-in setup is being completed by the app administrator.</p>}
    {connection && !connection.media_configured && <p className="fact-check-error">Image hosting must be configured before publishing.</p>}
    {error && <p className="fact-check-error" role="alert">{error}</p>}
    {!connection && error && <button className="button subtle" onClick={() => { setError(""); load().catch((reason) => setError(reason.message)); }}>Retry account status</button>}
    {busy && <p role="status">{busy}…</p>}
    <div className="button-row">
      <button className="button approve" disabled={!!busy || !connection?.oauth_configured} onClick={connect}>{connection?.connected ? "Reconnect Instagram" : "Connect Instagram"}</button>
      {connection?.connected && <><button className="button subtle" disabled={!!busy} onClick={verify}>Check connection</button>
        <button className="text-button" disabled={!!busy} onClick={() => setConfirmDisconnect(true)}>Disconnect</button></>}
    </div>
    {confirmDisconnect && <div className="disconnect-confirm" role="group" aria-label="Confirm Instagram disconnect">
      <p>Disconnect @{connection?.username}? Pending posts stay saved, but publishing needs this account reconnected. Posts already published remain on Instagram.</p>
      <div className="button-row"><button className="button reject" disabled={!!busy} onClick={disconnect}>Disconnect account</button><button className="button subtle" disabled={!!busy} onClick={() => setConfirmDisconnect(false)}>Keep connected</button></div>
    </div>}
  </section>;
}
