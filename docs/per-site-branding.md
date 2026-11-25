# Per-site branding (two sites, one codebase)

One codebase serves many sites. Each site has its own Directus and its
own database, so each site sets its own identity in its own
`site_settings` row. Nothing about a site's identity is hardcoded in
the templates anymore.

## The three fields (Directus `site_settings`)

| Field | What it does | Genabay | Genaveh Stocks |
|---|---|---|---|
| `title_suffix` | Short suffix after the site title in `<title>` and the footer line. Empty string = no suffix. NULL = fallback. | فروشگاه لوازم خانگی | بازار سهام گناوه |
| `enamad_seal_id` | Enamad trust seal id. Both id and code must be set for the seal to render. | 8856743 | (empty = no seal) |
| `enamad_seal_code` | Enamad trust seal Code. | S6nwLjmqnP9H84VIqFaj0olAcSxyVyo3 | (empty = no seal) |

The fallback (when Directus cannot answer) is Genabay's identity, so
the shop never breaks: `title_suffix` = "فروشگاه لوازم خانگی" and the
Genabay Enamad seal.

## How it works

- `integrations/directus_client.py`: `SiteSettings` carries the three
  fields. `_fetch_site_content` reads them from Directus. A CMS string
  wins; an explicitly empty string means "none on this site"
  (no suffix / no seal); NULL keeps the fallback.
- `templates/base.html`: the `<title>` and the footer line append the
  suffix only when it is set. The Enamad seal block renders only when
  both id and code are set, and uses their values in the link.
- `api/routes/pages.py`: `/manifest.webmanifest` is built per-site
  from the same settings (name = title + suffix, short name = title,
  description = tagline), so each site installs under its own name.

## Adding the fields to a new site

Run `add_site_fields.py` on the server (it reads the admin login from
`/srv/bandeh/app/.env` and uses the local Caddy vhost):

```
DIRECTUS_HOST=directus.<domain> python3 add_site_fields.py "<suffix>" "<enamad_id>" "<enamad_code>"
```

Use `""` for a field that should be empty. The script creates the
three fields in `site_settings` and sets the values on row id 1.
