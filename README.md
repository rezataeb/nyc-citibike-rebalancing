# Citi Bike Rebalancing Explorer

## Running the dashboard

`dashboard.html` fetches its data files (`data/flows.json`,
`data/live_status.json`) with `fetch()`, which browsers block when a page is
opened directly from disk (`file://...`) for security reasons -- the fetch
rejects before a response ever comes back. **Double-clicking `dashboard.html`
will not work.**

Serve the `app/` directory over plain HTTP instead:

```
python3 -m http.server 8000
```

then open **http://localhost:8000/dashboard.html** in a browser.

Once this project is hosted on GitHub Pages (or any other real HTTP host),
opening the page's URL directly will work with no extra steps -- the
`file://` restriction only affects local, on-disk viewing.
