# Instagram Login: one-time app-owner setup

Regular users click **Connect Instagram** in the dashboard and authorize on
Instagram. These instructions are for the owner deploying Social Media Studio,
not for every user.

## Configuration

Install `requirements.txt`, apply `python -m scripts.run_local migrate`, and set
these values in the ignored root `.env`:

```dotenv
INSTAGRAM_APP_ID=your-instagram-app-id
INSTAGRAM_APP_SECRET=your-instagram-app-secret
INSTAGRAM_REDIRECT_URI=https://your-public-origin/instagram/oauth/callback
INSTAGRAM_TOKEN_ENCRYPTION_KEY=your-fernet-key
INSTAGRAM_API_VERSION=v25.0
FRONTEND_BASE_URL=http://localhost:5173
PUBLIC_MEDIA_BASE_URL=https://your-public-origin
```

Use the **Instagram app ID and Instagram app secret** in the Instagram API
setup, rather than assuming they are the same as the parent Meta app's credentials.
Generate the encryption key once with `Fernet.generate_key()` from `cryptography`.
Keep it private and backed up separately from the database. Losing or changing it
without re-encrypting records makes existing tokens unreadable. Never commit real
credentials or put them in React/Vite environment variables.

For local testing, start `python -m scripts.run_local media` and tunnel port 8001.
The gateway serves only signed image downloads and the OAuth callback relay. It
does not expose the dashboard, token-exchange endpoints, or account management.
After a temporary tunnel changes, update both public URLs here, update Meta's
registered callback, and restart API, media gateway and worker.

## Meta configuration

1. Open the app's **Instagram API → API setup with Instagram login**.
2. Under **Set up Instagram business login**, configure **Valid OAuth Redirect
   URIs** to exactly match `INSTAGRAM_REDIRECT_URI`, including scheme and path.
3. Enable the login flow and save the settings shown by Meta.
4. Add `instagram_business_basic` and `instagram_business_content_publish` in
   **Permissions and features**.
5. For development, use an Instagram Business/Creator account with an accepted
   Instagram Tester role on the app.
6. Complete Meta's applicable review, verification and policy requirements before
   allowing ordinary external users. Implementing OAuth does not grant advanced
   access or complete App Review.

Meta may require additional app details, such as privacy policy and data-deletion
information, for a public release. Follow the requirements in the actual app dashboard.
Production needs a stable HTTPS callback and a deployed dashboard/API; local-only
addresses and temporary tunnels are for development.

## Browser and backend flow

1. An authenticated dashboard user calls `POST /instagram/oauth/start`.
2. The backend saves a hash of a random state, its owner, a browser-cookie binding,
   the exact redirect URI and a 10-minute expiry. Starting a new request invalidates
   the owner's previous requests.
3. The browser navigates to Instagram's authorization page for the two scopes.
4. Instagram returns to the public callback. The relay redirects to our fixed
   dashboard origin using a URL fragment, so the code is not sent in frontend
   HTTP query logs or referrers. The frontend immediately removes the fragment.
5. The dashboard calls `POST /instagram/oauth/complete` with its normal JWT, the
   one-use code/state and the HttpOnly browser cookie. State, owner, browser and
   expiry must match. A replay is rejected.
6. The backend exchanges the code, obtains a long-lived token, reads the actual
   account identity, validates scopes/account type, and encrypts the token with
   Fernet. The encrypted payload is bound to both workspace and Instagram account.
7. The browser receives only connection metadata. The publishing adapter loads
   credentials for the request's owner and verifies the account before posting.

Use the same site for dashboard/API during development (`localhost` consistently,
not a mix of `localhost` and `127.0.0.1`). In production, use same-site HTTPS origins,
configure CORS to the actual dashboard origin, and preserve secure cookies through
the reverse proxy. Only OAuth start/complete fetches include browser credentials;
normal API authentication continues to use bearer tokens. OAuth callback query
strings must be omitted or redacted from gateway/proxy logs. The supplied local
media gateway already disables access logs.

## Lifecycle and scheduled destinations

- One connection is stored per workspace in `instagram_connections`.
- Tokens are encrypted, not hashed: the backend needs to decrypt them when calling
  Instagram. Keep the encryption key out of the database and browser.
- The Instagram worker checks expiring connections hourly. Eligible tokens with
  fewer than seven days remaining are refreshed; tokens must be at least a day
  old. Publishing also checks expiry and refreshes when needed.
- Expired tokens are blocked. A revoked token detected by Instagram marks the
  connection as needing reconnection. Provider/network errors do not expose raw
  responses or secrets in user-visible errors.
- Imported legacy tokens have unknown expiry and keep working until invalidated;
  reconnect through OAuth to record expiry and enable automatic renewal.
- Disconnect clears the saved token and invalidates pending OAuth requests. It
  stops future publishing, but cannot recall an already-sent publish request.
  Users can revoke the provider grant in Instagram's Apps and websites settings.
- Scheduling freezes the destination account ID. Connecting another account later
  does not redirect an old scheduled campaign to it; delivery stops and asks for
  the original account. Existing completed delivery history is preserved.

## Verification

Automated tests cover successful and denied OAuth, wrong-user/wrong-browser state,
expiry, replay, encrypted storage, cross-owner access, account transfer after
disconnect, reconnect races, token refresh, scheduled-destination mismatch and
safe HTTP logging. HTTP provider calls are simulated; they do not constitute live
Meta authorization. A live browser test still requires the real app secret, a
registered callback and the user's explicit approval on Instagram.

The existing live publishing test is documented separately in
`evaluations/results/INSTAGRAM_PUBLISHING_REPORT.md`.
