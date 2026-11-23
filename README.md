# Dairy-trip charging ledger

The current dashboard is a local Tkinter application. On Windows, double-click
`Launch Dairy Dashboard.cmd` in this folder, or run `python .\dairy_charging.py`
using Python with Tkinter. It starts with an empty
ledger at `D:\AI_Output\TeslaChargingDashboard\ledger.sqlite3` on Windows
(on other systems, `~/.local/share/tesla-charging-dashboard/ledger.sqlite3`).
The database contains unencrypted personal information: protect the folder
with appropriate account permissions, keep it out of public or synced folders,
and back it up privately. It is not stored in the repository or published.

Enter vehicle and dairy names locally; previously entered names appear in the
editable lists. Records represent trips to or from a dairy. Enter local date and
24-hour time, location, energy in kWh, and optionally odometer miles, trip miles,
rate in USD/kWh, and actual paid in USD. Blank optional fields mean unknown,
not zero. The actual paid amount is never calculated from the rate. Select a row
to correct it, then save; use New / clear to create another. Filters apply exact
vehicle, dairy and direction matches and inclusive date ranges. Totals include
only known paid amounts and trip distances and report missing values; odometer
readings are never summed.

No Tesla account, import, scheduling, or network access is required. The former
static proof of concept is preserved in Git at the `archive-poc-2026-11-23` tag.
Both former public dashboard URLs now serve retirement notices. The old CSV
preview command is retired; Tesla discovery tools below remain available. No
existing records were migrated. Historical Git content may still be public.

### Review saved Tesla responses

The local dashboard's **Review saved Tesla responses** button opens one or more
files the user explicitly selects; it makes no Tesla request. To review both
probe pages together, navigate to the displayed private response directory and
select `page-1.bin` and `page-2.bin` using Ctrl-click. Select up to 20 files
and 5,000 candidates at once. If pages contain overlapping session identifiers,
the selection is rejected rather than silently choosing a version; review those
pages separately instead. All selected files are checked before the review
opens, and no records are saved by selecting them.

The selected responses are potentially incomplete pages, not a complete history.
The review window shows the reported vehicle identifier, location and timestamps
locally as unverified clues. Select **View unverified fee details** to inspect
all source fee entries and tiers for the selected candidate, including any
additional fee fields, without collapsing them. The displayed JSON is private:
avoid sharing it. Missing, null and empty fee details remain distinct. Extremely
large or malformed fee details are rejected rather than silently truncated.
Nothing is prefilled or calculated: independently verify the field meanings,
units and whether an amount was actually paid; a total due is not proof of
payment. For each dairy trip, manually enter and confirm the vehicle, dairy,
direction, date, time, location and verified kWh; leave optional odometer, trip
distance, rate and actual payment blank if unknown. If kWh is unknown, do not
save until verified.
The confirmation checkbox also asks you to check the ledger for duplicates.

On save, the source session identifier is stored unencrypted in the private
ledger to block repeat imports of that identifier. Manual entries and records
imported through other means have no such link and cannot be automatically
deduplicated; review them yourself. Existing records are not overwritten.
Saving a candidate does not establish that the page or history is complete.
No automatic synchronization or pagination is implemented.

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
location, and charging commands. A user-authorized first-page request succeeded,
but pagination and field meanings remain unverified.

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

By default, if partner key lookup returns HTTP 403, the helper stops without attempting
registration. Tesla lists both authorization/scope issues and an unregistered
account as possible causes of 403; the status alone does not establish whether
the domain has a registered key. Check the application's access and regional
registration with Tesla before taking action. The lookup includes the JSON
content-type header required by Tesla's
[API conventions](https://developer.tesla.com/docs/fleet-api/getting-started/conventions);
Tesla associates a missing header with HTTP 406, so it is not established as
the cause of a 403.

After an unconfirmed registration outcome, do **not** repeat the registration
POST. Instead, use `python .\register_tesla_partner.py --verify-only` (add
`--env-file "D:\path\outside-git\.env"` if needed). In a private terminal,
confirm credential use when prompted. This requests a partner token and performs
only the read-only key lookup; it never sends a registration POST or prints the
returned key. A matching key confirms the lookup returned the expected public
key. HTTP 403, HTTP 404, malformed responses, and a different key do not
confirm registration; check Tesla or contact its support before taking action.

For a first registration, an explicit `--first-registration` option is
available in a private terminal, alongside `--env-file` if needed. This is
separate from the safer default: if the lookup returns 403 or 404, the
registered key remains unknown. The helper rechecks the hosted key and lookup,
stops on any known different key or other lookup failure, and warns that the
POST may replace an existing key and disrupt other integrations. Immediately
before the POST it requires you to type
`I CONTROL THE DOMAIN AND ACCEPT KEY REPLACEMENT RISK`. Do not continue if
you cannot accept that risk; a lookup failure is not proof of first
registration. Tesla does not provide an atomic check-and-register operation,
so even a successful check cannot rule out a concurrent change. If the POST or
its verification fails, the outcome is unconfirmed: check Tesla before retrying.

As an alternative to entering credentials at the prompts, explicitly pass a
local file outside all Git repositories:
`python .\register_tesla_partner.py --env-file "D:\path\outside-git\.env"`.
In a private terminal, the helper still requires `CONTINUE` before reading the
file and requesting a partner token. The UTF-8 file must have exactly the
literal, unquoted lines `CLIENT_ID=<value>` and `CLIENT_SECRET=<value>`;
blank lines and comments are allowed. Values are not shell-expanded, printed,
or copied. Files inside Git repositories and symbolic links are rejected.
Keep the file private and outside cloud-synced or publicly shared locations.
Git ignores `.env` and `.env.*` in this repository, but that does not remove
files already tracked or prevent other software from syncing them.

Client ID and secret prompts show one `*` per entered character. Asterisks
confirm input was received, not that it was pasted correctly. If a paste
shortcut is received as a control character, the helper stops before making
the request. Use a paste shortcut supported by your terminal (such as
Ctrl+Shift+V where supported), or type the value manually. Special keys and
some non-ASCII characters may be rejected instead of being silently changed.

For an offline input check, run `python .\check_tesla_client_id.py` in your own
private terminal. Paste the application client ID at the first masked prompt
and type it manually at the second; do not enter the client secret. The script
compares the entries and checks for UUID-shaped format,
and shows whether the registration helper's whitespace trimming would change
the first entry. Unsupported control and invisible characters are rejected.
It does not print or save the ID or make network requests.
Matching entries do not establish that Tesla recognizes the ID; UUID shape is
only a diagnostic, not a documented requirement.

The helper pins the prepared public key's SHA-256 fingerprint. A key rotation
requires reviewing and updating that fingerprint before using the helper. Tesla's
[partner endpoint reference](https://developer.tesla.com/docs/fleet-api/endpoints/partner-endpoints)
documents the registration and lookup endpoints; its indexed excerpt did not
show the POST body, so the `{"domain": ...}` shape follows the
[example in Tesla's vehicle-command repository](https://github.com/teslamotors/vehicle-command/issues/6)
and a [third-party integration](https://teslasync.dev/guide/tesla-fleet-api.html).
A registration POST returned HTTP 200, but the subsequent key lookup was
forbidden; registration and key state remain unverified.

The callback is separate from that public application domain. This prototype
uses the exact redirect URI `http://localhost:8765/callback` and listens only
on `127.0.0.1:8765`. Register that exact callback in the portal if accepted;
Tesla's indexed documentation does not establish whether the portal accepts
HTTP localhost callbacks; a user-authorized flow succeeded with this callback.
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

To reuse the same local credential file supported by the partner helper, run
`python .\authorize_tesla.py --env-file "D:\path\outside-git\.env"` instead.
This option requires a private interactive terminal and explicit `CONTINUE`
confirmation before reading the file. The file format and location restrictions
above still apply; its contents are read literally, never executed, and are not
printed. Complete sign-in and consent in your browser yourself.

### Optional offline renewal (Windows)

After registration and ordinary authorization are working, opt in from a private
interactive terminal with `python .\authorize_tesla.py --offline-setup`. Complete
Tesla sign-in and consent yourself. This separately requests `offline_access`
along with `openid` and `vehicle_charging_cmds`; it does not fetch history during
setup. The client secret and access token are not saved. The client ID and refresh
token are encrypted using Windows DPAPI for the current Windows user and stored
in `%LOCALAPPDATA%\TeslaChargingDashboard\offline-token.bin`. DPAPI is not a
substitute for protecting your Windows account and device. The existing default
command remains unchanged. Setup refuses to overwrite an existing record.
To use the local credential file for setup, add `--env-file "D:\path\outside-git\.env"`
to the `--offline-setup` command; the same interactive confirmation is required.
`--renew` does not use this file.

Run `python .\authorize_tesla.py --renew` explicitly to exchange the saved refresh
token and fetch one private first-page response. It needs no interactive prompts
and installs no scheduled task. It does not interpret or publish the response. A
local lock rejects overlapping setup or renewal runs. Before exchanging a refresh
token, the helper marks the encrypted record pending; it saves the newly rotated
token before fetching history. An interrupted or uncertain exchange leaves the
record pending and blocks subsequent renewals rather than automatically retrying.
Sudden power loss or storage failure can still defeat filesystem durability.

If renewal is blocked by a pending record, do not repeatedly run it. With all
helper processes stopped, privately move `offline-token.bin` to a secure backup
outside the repository and repeat `--offline-setup` to obtain fresh consent. Do
not share or publish the backup. Tesla documents that the most recently used
refresh token may remain usable for up to 24 hours if saving fails, but this
helper does not automatically retry it. Tesla also documents a three-month
refresh-token lifetime; revoked or expired authorization requires new consent.
See Tesla's [third-party token documentation](https://developer.tesla.com/docs/fleet-api/authentication/third-party-tokens).
The renewal flow, Tesla's localhost callback, charging-history permission, and
account compatibility have not been verified against a real Tesla account.

### Optional two-page pagination experiment

This separate interactive tool tests the third-party SDK's **unverified**
`pageNo=1` and `pageNo=2`, `pageSize=25` parameters. From the repository folder,
run `python .\probe_tesla_pagination.py --env-file "D:\path\outside-git\.env"`
in your own private terminal, or omit `--env-file` for masked prompts. When
installed alongside the standalone dashboard, double-click
`Launch Pagination Probe.cmd` for masked prompts, or run the adjacent script
with `--env-file` from a private terminal. Type
`CONTINUE` to begin authorization, confirm file use separately if applicable,
then complete Tesla sign-in and consent in your browser. The requested charging
scope also permits charging commands, although this tool sends none. Immediately
before the history GETs, review the billing, scope and unencrypted-storage
warnings and type `FETCH`; press Enter at either gate to cancel. No automatic
retry or registration POST is performed.

The tool makes at most two read-only requests, saves responses in a new
`.private\pagination-probe-*` folder next to the script, and reports only page
counts, whether reported totals match, and identifier overlap. The folder is unencrypted and
inherits local access controls. The repository's `.private` folder is ignored
by Git; the separately installed folder is outside the repository. Neither
location's access controls are guaranteed, and Git ignore does not prevent other users,
backups or sync services from seeing it. Protect it and never publish raw
responses. If a request, save or parsing step fails, the tool stops; any earlier
saved page remains in the displayed folder. Distinct pages and matching totals
do not establish pagination semantics, a complete or stable history, or
partner-key registration. These candidate pages can be selected together or
individually for review in the local ledger; they are not automatically imported.

### Optional bounded history staging

This separate, user-initiated tool stages pages for manual review; it does not
automatically synchronize or import charges. From the standalone folder,
double-click `Launch Tesla History Staging.cmd`, or run
`python .\stage_tesla_history.py --env-file "D:\path\outside-git\.env"` in a
private terminal. Without `--env-file`, enter application credentials in the
masked prompts. No offline access, registration or schedule is activated.

Choose a maximum of **1 to 5 history GETs** (25 requested records per page).
Read the scope, potential billing, private unencrypted storage and unverified
pagination warnings, then type `CONTINUE` to begin authorization. Complete your
own Tesla sign-in and consent; immediately before any history GET, read the
warnings and exact cap again and type `FETCH`. Enter cancels at either gate.
The scope also permits charging commands, but this tool sends none.

The tool tests the unverified `pageNo` and `pageSize` parameters and saves only
pages that pass its structural and consistency checks to a new
`.private\history-stage-*` folder beside the script. It stops without retries
when the cap is reached, totals change, identifiers repeat, a page is malformed
or too large, a short or empty page occurs before the reported total, or a
request or save fails. The failing page is not intentionally staged; a failed
write may leave a partial file. Previously staged pages remain in the displayed
private folder. Even when the row count matches the reported total, a stable
snapshot and complete history are **not verified**. Storage is unencrypted,
inherits local access controls, and is not protected from backups or sync merely
by its folder name or Git ignore.

In the dashboard select **Review saved Tesla responses** and Ctrl-click the
staged `page-*.bin` files in that folder. Nothing is imported automatically:
review each candidate, identify the vehicle and dairy trip yourself, verify
values and units independently, and check manual-entry duplicates before saving.

### Optional noninteractive bounded staging (Windows)

This separate command uses the existing per-user encrypted offline token; it
does not perform offline setup or install a schedule. First, you must personally
run the **Optional offline renewal** setup above and consent to `offline_access`
and the charging-management scope, which also permits charging commands. A
future scheduled run requires separate explicit approval of its frequency,
request cap, possible billing and private unencrypted response storage. No
scheduled task is created by these tools.

For an explicitly authorized one-time run, choose a maximum between 1 and 5
history GETs and include both acknowledgments. For example, from the standalone
folder, the following authorizes at most one history GET:

```powershell
python .\stage_tesla_history_offline.py --max-pages 1 --accept-offline-scope-and-billing --accept-unencrypted-storage-and-unverified-pagination
```

The command has no prompts. It refreshes the token under the same lock as
`--renew`, marks the old token pending before exchange, and saves the rotated
token before any history GET. The lock remains held throughout staging, so
overlapping offline runs fail rather than race. An uncertain refresh leaves the
token pending and blocks automatic retries; follow the recovery instructions
above. A missing access token also stops before a history GET. It uses exactly
the bounded staging checks, private folder and manual-review workflow described
above, and never modifies the ledger. Matching totals do not prove a stable
snapshot or complete history. The offline renewal and history pagination have
not been verified end-to-end against Tesla.

### Check saved page structure locally

Double-click `Launch Tesla Page Check.cmd` beside the standalone dashboard, then
select the first and second saved response in the file dialogs. Alternatively,
run `python .\check_tesla_page_structure.py "D:\private\page-1.bin" "D:\private\page-2.bin"`
in a private terminal. The checker only reads the selected files; it makes no
network requests and writes no response files. Its fixed-label report gives
aggregate counts-and-types comparisons without showing record values, file
paths, identifiers or raw error details. A failed or canceled check produces no
successful report; empty arrays are shown as not applicable for field coverage.

Field presence and type do not establish meaning, units, whether payment was
actually made, or whether unlisted fields exist. The sample names checked for
mileage and energy are not an exhaustive schema. Even matching totals without
overlap cannot prove completeness or stable pagination. Keep uncertain values
manual, and do not share raw responses.

### Discover the personal-history schema

This is a read-only **first-response discovery step**, not an automated refresh:
it sends one GET with no pagination parameters to the [documented history endpoint](https://developer.tesla.com/docs/fleet-api/endpoints/charging-endpoints)
on Tesla's [North America/APAC regional host](https://developer.tesla.com/docs/fleet-api/getting-started/regions-countries).
It does not request further pages, interpret fields, or modify the dashboard.
The GET includes the JSON content-type header required by Tesla's
[API conventions](https://developer.tesla.com/docs/fleet-api/getting-started/conventions).
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

The former `refresh_dashboard.py` CSV-to-HTML preview command is retired; it
does not read the CSV or create output. Its original implementation and template
remain in the archive tag. Do not copy archived personal data into a public
folder. The local ledger does not automatically import historical or Tesla
data; the saved-response review described above requires manual confirmation.
Verify personal-history fields and pagination before implementing automatic
synchronization. Never put tokens in the repository or publish raw responses.

Run local checks with `python -m unittest test_dairy_charging test_refresh_dashboard test_fetch_charging_history test_authorize_tesla test_tesla_token_store test_register_tesla_partner test_check_tesla_client_id test_masked_input`.

## License

Personal project; no warranty.
