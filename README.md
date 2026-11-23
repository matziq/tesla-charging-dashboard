# Tesla Charging Dashboard

Static HTML dashboard summarizing Tesla Supercharger usage for **Aurora** & **Cherry Bomb** (Jan–May 2026), including:

- Total cost / kWh / sessions
- Work vs. Travel cost split
- Monthly charging trend
- Per-vehicle breakdown
- Route insights (work hubs + long-distance corridors)
- Charger-frequency heatmap

## Live site

🌐 **<https://matziq.github.io/tesla-charging-dashboard/>**

## Source

`index.html` is a static overview with no build step. `Dashboard.html` is a
separate interactive tax tracker that loads JavaScript and CSS from CDNs.

The underlying export (`tesla_charging_analysis.xlsx`) is intentionally **not** published.

## Private refresh prototype

The indexed excerpt of Tesla's [charging endpoint documentation](https://developer.tesla.com/docs/fleet-api/endpoints/charging-endpoints)
describes a paginated charging history and an invoice PDF endpoint, but does not
show the personal-history response fields or pagination parameters. Direct access
to the full page returned HTTP 403 during this investigation. The excerpt says
the separate charging sessions endpoint, which explicitly includes pricing and
energy, is limited to business fleet owners. That does **not** prove those fields
are absent from personal history: their shape and availability remain unverified. Tesla's
[scope documentation](https://developer.tesla.com/docs/fleet-api/authentication/overview)
describes `vehicle_charging_cmds` as covering charging history, billed amount,
location, and charging commands. The authorization flow and fetch below have not
been tested with a Tesla account or real response.

### Application setup and local authorization

Tesla's [partner registration documentation](https://developer.tesla.com/docs/fleet-api/endpoints/partner-endpoints)
requires each application to register before general API access, using a partner
token and a domain matching the root of an allowed origin. The application must
host its **public** EC secp256r1 key at
`https://<app-domain>/.well-known/appspecific/com.tesla.3p.public-key.pem`;
keep the corresponding private key secret. The key must remain hosted. Register
the application for the North American region using Tesla's
[onboarding](https://developer.tesla.com/docs/fleet-api) and
[partner-token instructions](https://developer.tesla.com/docs/fleet-api/authentication/partner-tokens).
A localhost-only allowed origin cannot satisfy the public HTTPS domain/key
requirement. Use a public HTTPS domain you control and configure an appropriate
allowed origin in Tesla's portal; check Tesla's current portal rules.

For this site, the public key is hosted at
`https://memconfigmgr.org/.well-known/appspecific/com.tesla.3p.public-key.pem`.
After confirming the application allows `https://memconfigmgr.org`, run
`python .\register_tesla_partner.py` in your own interactive terminal. The
helper verifies the hosted key against the P-256 key prepared for this project,
then asks for confirmation and privately prompts for the application client ID
and secret. It requests a North American partner token in memory and looks up
Tesla's registered key. If a different key is registered, it stops without
replacing it. A 404 lookup is **not proof** that no registration exists: the
helper warns that a registration POST can change the registered key and requires
you to type `REGISTER` before it sends that POST. It rechecks immediately before
the POST and checks Tesla's registered key again before reporting success; Tesla
does not provide an atomic compare-and-set here, so a concurrent change remains
possible. Unexpected responses or failures stop the
process without printing credentials, tokens, or response bodies. For a token HTTP
error, the helper displays a recognized OAuth error category or a fixed response
shape label; unknown error text and descriptions remain private. The launch
message identifies diagnostic format v3, so an older copy is easy to recognize.
The allowlist includes Tesla-specific categories shown in
[Tesla's vehicle-command issue tracker](https://github.com/teslamotors/vehicle-command/issues/267);
`client_not_found` was also observed with synthetic credentials. The format of
any particular live response is not known in advance.
If verification fails or a POST fails, check Tesla before retrying because the
outcome may be unknown.

For an offline input check, run `python .\check_tesla_client_id.py` in your own
private terminal. Paste the application client ID at the first hidden prompt
and type it manually at the second; do not enter the client secret. The script
compares the entries, checks for invisible characters and UUID-shaped format,
and shows whether the registration helper's whitespace trimming would change
the first entry. It does not print or save the ID or make network requests.
Matching entries do not establish that Tesla recognizes the ID; UUID shape is
only a diagnostic, not a documented requirement.

The helper pins the prepared public key's SHA-256 fingerprint. A key rotation
requires reviewing and updating that fingerprint before using the helper. Tesla's
[partner endpoint reference](https://developer.tesla.com/docs/fleet-api/endpoints/partner-endpoints)
documents the registration and lookup endpoints; its indexed excerpt did not
show the POST body, so the `{"domain": ...}` shape follows the
[example in Tesla's vehicle-command repository](https://github.com/teslamotors/vehicle-command/issues/6)
and a [third-party integration](https://teslasync.dev/guide/tesla-fleet-api.html).
No live registration has been performed or verified.

The callback is separate from that public application domain. This prototype
uses the exact redirect URI `http://localhost:8765/callback` and listens only
on `127.0.0.1:8765`. Register that exact callback in the portal if accepted;
Tesla's indexed documentation does not establish whether the portal accepts
HTTP localhost callbacks, and this has not been tested with a real application.
If Tesla rejects it, do not forward a public callback to this local server or
enter credentials into an untrusted site; a different verified design is needed.

Once registration is complete, run `python .\authorize_tesla.py` in your own
interactive terminal. Enter the application client ID and, at the hidden
prompt, its client secret. Your browser opens Tesla sign-in and consent; you
must complete them yourself. The script uses a random state and PKCE S256 (listed
in Tesla's [third-party discovery metadata](https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/thirdparty/.well-known/openid-configuration)),
then exchanges the code using the endpoint in Tesla's
[user-token documentation](https://developer.tesla.com/docs/fleet-api/authentication/third-party-tokens).
It requests only `openid` and `vehicle_charging_cmds`, does not request a refresh
token, and does not print or save client secrets, authorization codes, or tokens.
It uses the access token in memory for one history GET and saves the raw response
locally as described below. A new authorization is needed for each run. The
scope also allows charging commands even though this script never sends them.
The browser may retain the callback URL in its history; treat it as sensitive.
Don't share credentials, callback URLs, or raw responses in chat.

### Discover the personal-history schema

This is a read-only **first-response discovery step**, not an automated refresh:
it sends one GET with no pagination parameters to the [documented history endpoint](https://developer.tesla.com/docs/fleet-api/endpoints/charging-endpoints)
on Tesla's [North America/APAC regional host](https://developer.tesla.com/docs/fleet-api/getting-started/regions-countries).
It does not request further pages, interpret fields, or modify the dashboard.
It refuses redirects and does not print the token or response contents. Non-200
responses, networking failures, and responses over 10 MiB are not saved.

Tesla's [billing and limits](https://developer.tesla.com/docs/fleet-api/billing-and-limits)
may apply, including to unsuccessful requests. If you already have a suitable
user access token instead of using the local authorization flow, enter it privately
in your own PowerShell window; do not paste it into this chat:

```powershell
$token = Read-Host "Tesla Fleet user access token" -AsSecureString
$env:TESLA_FLEET_ACCESS_TOKEN = [System.Net.NetworkCredential]::new("", $token).Password
try { python .\fetch_charging_history.py }
finally { Remove-Item Env:\TESLA_FLEET_ACCESS_TOKEN -ErrorAction SilentlyContinue; Remove-Variable token }
```

Inspect the printed `.private\charging-history-*.bin` path locally in a
trusted editor. It contains the unmodified response bytes, potentially
including sensitive locations and identifiers. The ignored folder is not an
access control; protect and remove the file when no longer needed. HTTP errors
are reported by status without saving their potentially sensitive bodies.

The script has a fixed North American host; don't use it with a token for another
regional audience. Do not send the response to a third-party tool or publish it.

Until a real, privately inspected response and pagination scheme can be verified,
prepare a complete, manually reviewed CSV from your available records, including
the desired work/travel classifications. This import step does not parse the
downloaded response, extract invoices, infer categories, or merge/deduplicate
sessions. Repeated rows are kept because separate sessions can have identical values.

1. Make a UTF-8 CSV with exactly these headers: `Date,Vehicle,Location,State,kWh,Cost,Category`.
   Dates must be `YYYY-MM-DD`; kWh and cost must be nonnegative numbers; category
   must be `WORK` or `TRAVEL`. Include the **full** history you want displayed,
   not just new rows. Amounts should be in USD, matching the dashboard.
2. From this folder, run `python .\refresh_dashboard.py <path-to-reviewed-csv>`.
   The command creates a new, git-ignored `.private\dashboard-*.html` preview
   and prints its path. It leaves both tracked dashboards untouched. The preview
   is based on `Dashboard.html`, not the separate static `index.html` landing page.
3. Open the preview locally and review the sessions, totals, classifications,
   and sensitive locations before deciding whether to publish anything. The
   preview loads the same external JavaScript and CSS resources as `Dashboard.html`;
   third-party scripts can access its data. It is not an offline-only page.
   No automatic publishing is configured.

`Dashboard.html`'s filtered CSV export also works as input after removing its
blank line and summary footer; its `$/kWh` column is accepted and ignored.
Confirm the export's filters include every session intended for the preview.
The original records, CSV, and previews are private local files; do not commit
or share them inadvertently. Git ignore rules are not access controls.

Verify the actual personal-history response and pagination privately before
mapping fields or implementing further automation. Never put tokens in the
repository or publish raw responses.

Run the local checks with `python -m unittest test_refresh_dashboard test_fetch_charging_history test_authorize_tesla test_register_tesla_partner test_check_tesla_client_id`.

## Local preview

```pwsh
# Just open it
Start-Process .\index.html
```

## License

Personal project; no warranty.
