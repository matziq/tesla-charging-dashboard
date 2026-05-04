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

The dashboard is a single self-contained HTML file (`index.html`) — no build step, no JS dependencies. Just open it in any browser.

The underlying export (`tesla_charging_analysis.xlsx`) is intentionally **not** published.

## Local preview

```pwsh
# Just open it
Start-Process .\index.html
```

## License

Personal project; no warranty.
