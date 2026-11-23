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
location, and charging commands. The fetch below has not been tested with a Tesla
account or real response.

### Discover the personal-history schema

This is a read-only **first-response discovery step**, not an automated refresh:
it sends one GET with no pagination parameters to the [documented history endpoint](https://developer.tesla.com/docs/fleet-api/endpoints/charging-endpoints)
on Tesla's [North America/APAC regional host](https://developer.tesla.com/docs/fleet-api/getting-started/regions-countries).
It does not request further pages, interpret fields, or modify the dashboard.
It refuses redirects and does not print the token or response contents. Non-200
responses, networking failures, and responses over 10 MiB are not saved.

1. Follow Tesla's [developer onboarding](https://developer.tesla.com/docs/fleet-api)
   and [user authorization-code flow](https://developer.tesla.com/docs/fleet-api/authentication/third-party-tokens)
   to register an application in the appropriate region and obtain a user access
   token for the North American audience. Tesla lists `vehicle_charging_cmds` for
   charging history; this scope also authorizes charging commands, so grant it only
   to an application you trust. Tesla's [billing and limits](https://developer.tesla.com/docs/fleet-api/billing-and-limits)
   may apply, including to unsuccessful requests. This script uses no commands.
2. In your own PowerShell window, enter the token privately when prompted; do not
   paste tokens, authorization codes, or responses into this chat. Run:

   ```powershell
   $token = Read-Host "Tesla Fleet user access token" -AsSecureString
   $env:TESLA_FLEET_ACCESS_TOKEN = [System.Net.NetworkCredential]::new("", $token).Password
   try { python .\fetch_charging_history.py }
   finally { Remove-Item Env:\TESLA_FLEET_ACCESS_TOKEN -ErrorAction SilentlyContinue; Remove-Variable token }
   ```

3. Inspect the printed `.private\charging-history-*.bin` path locally in a
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

Run the local checks with `python -m unittest test_refresh_dashboard test_fetch_charging_history`.

## Local preview

```pwsh
# Just open it
Start-Process .\index.html
```

## License

Personal project; no warranty.
