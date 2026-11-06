# Instagram publishing acceptance test

Date: 16 October 2026. Completion recorded at 17:17:21 Africa/Cairo
(14:17:21 UTC).

## Result

One real Instagram single-image feed post was published successfully. The user
confirmed that the published caption and image matched the approved campaign.
This is a manual acceptance result for one campaign, not a publishing success-rate
benchmark or an automated visual comparison.

## Evidence

Read-only inspection of the local PostgreSQL records confirmed:

| Field | Recorded value |
| --- | --- |
| Delivery | 1 |
| Schedule | 1 |
| Platform | Instagram |
| Delivery status | `succeeded` |
| Schedule status | `completed` |
| Image attached | Yes |
| Attempts | 2: one failed, one succeeded |
| External post ID | Stored, nonempty |
| External post URL | Stored Instagram permalink |

No tokens, app secrets, signed image links, or private image paths are included
in this report. The actual post ID and permalink remain in delivery history.

## Failure and recovery

1. The first delivery attempt stopped with `Cannot reach public campaign image;
   check the tunnel`. Its saved provider stage and external post ID were empty:
   it stopped before container creation and did not publish a post.
2. Investigation found that the local image gateway was running, but the
   temporary Cloudflare hostname no longer resolved. Tunnel logs showed a
   network interruption followed by `Unauthorized: Tunnel not found`.
3. A replacement tunnel was created. `PUBLIC_MEDIA_BASE_URL` was updated locally,
   and the API and Instagram-only worker were restarted to load the new origin.
4. The actual scheduled image was fetched over public HTTPS with HTTP 200 and
   verified against the JPEG content digest. Account routes remained inaccessible
   through the image-only gateway.
5. The user retried the existing delivery without regenerating the campaign.
   Attempt 2 succeeded and saved an external post ID and permalink.
6. The user inspected the post and confirmed it matched the approved draft.

This tested recovery from a known pre-publication failure. It does not prove
recovery from an uncertain publication response; those outcomes intentionally
require manual reconciliation to avoid duplicate posts.

## Supporting checks

- The last complete automated run passed **146 tests**, including simulated
  Instagram preparation, polling, publishing, retry and workspace-access cases.
- The React production build passed.
- A separate earlier live preparation check produced an unpublished container
  with status `FINISHED` and left the published-post quota usage at zero.
- The Instagram-only worker was running for the live test. This acceptance
  run used a user-requested retry; unattended future-time dispatch is covered
  by automated worker tests, not a separate live timing experiment here.

## Scope and limitations

The implementation supports one configured Instagram account bound to one
workspace owner, with single-image feed posts. Local delivery requires the
database, worker, image gateway, tunnel, and laptop to remain available. Temporary
tunnel addresses may expire after interruptions or change when restarted.
Tokens are replaced manually when expired or revoked. Reels, carousels,
engagement monitoring, multi-account OAuth onboarding, and MCP remain outside
this test's scope.
