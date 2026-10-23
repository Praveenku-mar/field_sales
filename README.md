### Field Sales

Field Sales is a Frappe app that lets a brand’s sales reps check in at retail shops with GPS, take orders against live Shopify stock, and sync those orders to Shopify automatically. Managers can track visits, orders and sync status from one dashboard.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app field_sales
```

### Order fields on Shopify

Orders (and draft orders) pushed from Field Sales carry two app-owned fields, **Field Order No** and **Sales Rep** (name and email), plus a single `field-sales` tag. Staff can see the fields on the order page but not edit them. The app creates the field definitions on the store itself, on **Test Connection** or before the first order; Shopify does not copy a draft's fields to its order, so they are written onto the order when the completed draft is recorded.

### Commission webhooks (Shopify)

Sales rep commission follows the order's state on the store. Subscribe these topics through the app itself (GraphQL `webhookSubscriptionCreate` with the app's token, or the app configuration), all pointing at the same URL the stock and product webhooks use, `https://<your-site>/api/method/field_sales.api.webhooks.receive`. Don't add them under Shopify admin > Settings > Notifications > Webhooks: Field Sales verifies the signature with the app's client secret, and admin-created webhooks may be signed with a different key.

- `orders/paid`
- `orders/fulfilled`
- `orders/cancelled`
- `refunds/create`

Each event makes Field Sales re-read the order from Shopify and update the commission entries, so repeated or late deliveries are harmless. Orders that were not placed from Field Sales are ignored. Commission statements for the previous month are drafted automatically on the 1st of every month.

### Shopify App Store setup

Store Connection has two **Auth Modes**. *Client Credentials* (the default, and what existing sites use) only works for a store in the same Shopify organization as the app: you type the store URL, client id and secret on Store Connection. *OAuth* is for merchants who install Field Sales from the Shopify App Store: the install fills in the store URL and tokens, and the app's credentials live in site config. One site serves one shop; while a shop is connected, an install from another shop is refused.

**1. Site config** (`bench --site <site> set-config <key> <value>`; never stored in the database):

| Key | Value |
| --- | --- |
| `shopify_app_client_id` | Client ID from the Dev Dashboard |
| `shopify_app_client_secret` | Client secret from the Dev Dashboard (also verifies webhook signatures) |
| `shopify_app_scopes` | Optional. Default `read_products,read_inventory,read_locations,write_customers,read_customers,write_draft_orders,read_draft_orders,write_orders,read_orders` |
| `shopify_app_api_version` | Optional. Admin API version used in OAuth mode, default `2026-10` |
| `shopify_app_handle` | Optional. The app's handle (its URL name in Shopify), used for the "Change your plan" link |
| `shopify_partner_org_id`, `shopify_partner_api_token`, `shopify_app_gid` | Optional. Partner API organization id, Partner API client token and the app's `gid://shopify/App/<id>`, to read the shop's Shopify App Pricing plan (plan limits are off without them) |
| `field_sales_demo_passwords` | Optional. `{"manager": "...", "rep": "..."}` for `bench --site <site> field-sales-demo` (demo data and reviewer logins) |
| `shopify_app_url` | The site's public HTTPS address, e.g. `https://sales.example.com` (used for the redirect URI and webhook address; falls back to the site URL) |

**2. Dev Dashboard / `shopify.app.toml`.** Copy `shopify.app.toml` from the repo root, replace the client id and every `https://fieldsales.example.com` with your public address, and deploy it with `shopify app deploy` (or enter the same values in the Dev Dashboard):

- App URL: `https://<site>/fieldsales` (the app opens embedded in the Shopify admin)
- Allowed redirection URL: `https://<site>/api/method/field_sales.shopify.oauth.callback` (only for installs started outside the admin through `/api/method/field_sales.shopify.oauth.install`)
- Compliance webhooks (`customers/data_request`, `customers/redact`, `shop/redact`): `https://<site>/api/method/field_sales.shopify.webhooks.receive`
- Store data webhooks (stock, products, orders): `https://<site>/api/method/field_sales.api.webhooks.receive`

`app/uninstalled` is subscribed automatically after each install. Installation is Shopify managed (`embedded = true`, scopes from `[access_scopes]`): on the first load inside the admin, Field Sales exchanges the App Bridge ID token for an expiring offline token.

**Inside the Shopify admin** the manager screens run in the admin's iframe with no Frappe login: each request carries an App Bridge session token and runs as Store Connection's **Embedded App User** (default `shopify-admin@<site>`, created on first use with the Sales Manager role and its own Sales Rep; pick another manager user there to use it instead). Managers create reps from the Sales Reps page there; reps get the welcome or reset email and sign in to the standalone `/fieldsales` as before. Production nginx: bench's template adds `X-Frame-Options: SAMEORIGIN` to every response; browsers ignore it when the page sends CSP `frame-ancestors` (Field Sales does, for embedded loads), but remove it for `/fieldsales` if a review tool flags it.

**3. Install on a development store.** Create a development store in the Dev Dashboard, then open *Apps > Field Sales > Test on development store*. Approve the scopes; Field Sales opens inside the admin, and Store Connection shows Auth Mode *OAuth*, the store URL, granted scopes and *Installed On*. A local site needs a public HTTPS tunnel (e.g. ngrok) as `shopify_app_url` and App URL.

Field Sales asks for **expiring offline tokens** (1 hour access token, 90-day refresh token) and renews them in the background. If the refresh token runs out or Shopify rejects it, the connection is disabled and Store Connection says *Reinstall required*: open the app from Shopify admin to reconnect. Uninstalling clears the tokens and disables the connection. Privacy requests from Shopify appear as **Privacy Request** records: a customer data request gets a private JSON export attached and System Managers are emailed to send it to the store owner within 30 days; customer and shop redaction anonymise the data and keep order amounts and dates for reporting.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/field_sales
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
