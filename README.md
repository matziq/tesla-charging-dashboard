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
location, and charging commands. No account connection or Tesla API call has been
implemented or tested here.

Until a real, privately inspected response and pagination scheme can be verified,
prepare a complete, manually reviewed CSV from your available records, including
the desired work/travel classifications. This prototype does not download records,
extract invoices, infer categories, or merge/deduplicate sessions. Repeated rows
are kept because separate sessions can have identical values.

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

To investigate eventual API automation, a Tesla developer application, regional
registration, appropriate user authorization, and a securely held token would
be needed. Verify the current Tesla requirements and inspect actual personal
history privately before mapping fields or implementing pagination. Never put
tokens in the repository or publish raw responses.

Run the local checks with `python -m unittest test_refresh_dashboard`.

## Local preview

```pwsh
# Just open it
Start-Process .\index.html
```

## License

Personal project; no warranty.
