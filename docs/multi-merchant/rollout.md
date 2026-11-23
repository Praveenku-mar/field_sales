# Field Sales — shared site rollout (Phase 9)

How to bring up the shared multi-store site and keep it healthy. Design: `design.md`; endpoint audit: `inventory.md`.

**Decision D5, no merge tool:** existing single-store sites keep running as they are, on the same code. Nothing moves their data to the shared site. A merchant who wants the App Store version installs it on the shared site as a new store.

## 1. Staging first
- **Site:** a staging site with the shared-site config (section 2) and 2–3 Shopify **development stores** from the Partner Dashboard.
  - Give them different currencies and time zones (e.g. INR / Asia/Kolkata, USD / America/New_York).
  - Give each products and a location.
- **App:** one Shopify app for staging, distributed **publicly** (unlisted is fine). Custom distribution allows only one shop.
- **Install the app on every dev store and check:**
  - one Store per shop;
  - each store's `shopify-admin-<code>@<site>` user;
  - "Register Webhooks" Success in Sync Log;
  - products synced per store;
  - order numbers `FO-<code>-…`;
  - per store: currency, time zone and tax mode;
  - uninstall one store and reinstall it: only its users are refused, and the same Store comes back;
  - the admin-email prompt after install: the invitation arrives, its link sets the password, the login page follows, and the Shopify admin then opens as that account.
- **Automated tests**, on the test site:
  - `bench --site fieldsales-test.localhost run-tests --app field_sales`
  - `tests/test_store_isolation.py`, `tests/test_shared_site_flows.py` and `tests/test_rollout.py` cover the shared site.

## 2. Site config
```bash
bench new-site <site> --install-app field_sales
bench --site <site> set-config -p field_sales_multi_store 1
bench --site <site> set-config shopify_app_client_id "<client id>"
bench --site <site> set-config shopify_app_client_secret "<client secret>"
bench --site <site> set-config shopify_app_url "https://<public address>"
# Plans (Partner API) and the pricing page link
bench --site <site> set-config shopify_partner_org_id "<org id>"
bench --site <site> set-config shopify_partner_api_token "<token>"
bench --site <site> set-config shopify_app_gid "gid://partners/App/<id>"
bench --site <site> set-config shopify_app_handle "<app handle>"
# Optional: product syncs one scheduler tick may queue (default 5)
bench --site <site> set-config -p field_sales_product_syncs_per_tick 5
bench --site <site> enable-scheduler
```
Then in Desk:
- **System Settings time zone:** set it once, before the first store. Never change it afterwards: every stored time is in it.
- **Default outgoing Email Account:** SPF/DKIM on the vendor domain. Every store's mail goes out from it, in the store's name.
- **Razorpay Settings:** leave them off. Razorpay is always off on the shared site.

Never set `field_sales_multi_store` on a site that already has a merchant's data. Never turn it off once there are several stores: code without a store context then raises `NoStoreContext`.

**Check before opening and after every deploy:**
```bash
bench --site <site> field-sales-check-shared
```
It changes nothing. Any `FAIL` line means the site must not take installs (`utils/rollout.py`).

## 3. Shopify app config
- Fill in `shopify.app.toml` in a local copy; never commit real values:
  - `client_id`;
  - every `https://fieldsales.example.com` → the site's public address.
- Then run `shopify app deploy`. That registers:
  - the App URL (`/fieldsales`, embedded);
  - the OAuth redirect `/api/method/field_sales.shopify.oauth.callback`;
  - `app/uninstalled`;
  - the three compliance webhooks.
- The store webhooks (stock, product, order) are registered per store at install (`shopify/webhooks.register_webhooks`).

## 4. Workers and queues
- **Long queue:** each product sync is one job on it (timeout 25 min). Start with **2 long workers**, and keep `field_sales_product_syncs_per_tick` close to the number of long workers. Due stores are queued most overdue first, and the rest wait for the next 30-minute tick (`sync/products.py → scheduled_sync`).
- **Short queue:** webhooks, order pushes, confirmations and other per-record jobs. Start with 2 workers, and raise them if the short queue backs up at busy times.
- **Hourly jobs:** the daily summary and statements run hourly and act only for stores whose local time has come, so their work is spread over the day across time zones.
- **Duplicates:** every store job has its own job id (`field_sales:product_sync:<store>`, `customer_import:<store>`, …), so a slow store never stacks up copies of its own job.

## 5. App Store review checklist
| Requirement | Where it is met |
|---|---|
| Embedded app, App Bridge session tokens (2.3.3) | `shopify/session_token.py`, `www/fieldsales.py` |
| Install and reinstall through OAuth or token exchange | `shopify/oauth.py`, `shopify/embedded.py` (one Store per shop) |
| Checkout only through Shopify (1.1.2) | orders go as draft orders only; payments are never marked on the shop (`store_payments_off`) |
| No outside payment provider (1.1.7) | Razorpay off; Settings hides it for App Store installs |
| TLS everywhere (3.1.1) | `shopify/config.public_url` refuses an `http://` address |
| Mandatory compliance webhooks | `customers/data_request`, `customers/redact`, `shop/redact` → `shopify/privacy.py` (store-scoped; shop/redact also disables the store's users) |
| Uninstall | `app/uninstalled` → Store disabled, tokens cleared, users refused (`refuse_uninstalled_store_user`) |
| Billing through Shopify | Partner API plan lookup per store (`shopify/billing.py`), pricing page link |
| Reviewer access | a dev store with the app installed, plus demo data: `bench --site <site> field-sales-demo` (passwords from site_config `field_sales_demo_passwords`) |
| Privacy policy and help pages | `www/` pages, linked from the listing |

## 6. Running it
- **Watch these every day:**
  - Error Log titles `Field Sales scheduled job failed: <job> (<store>)` (one per failing store);
  - Sync Log rows with status Failed (filter by Store);
  - Scheduled Job Log failures;
  - `field-sales-check-shared` WARN lines for stores without currency, time zone or location.
- **Support:** vendor staff open the app and pick a store (*Switch store*). Each switch is recorded as a comment on the Store.
- **Turning one store off:** untick **Enabled** on the Store. Its product syncs, order retries, draft linking and store webhooks stop. Its users can still sign in, and the daily summary, statements and anonymising still run for it. To also keep its users out, set **Uninstalled On**.
- **Deploys:**
  - `bench --site <site> backup --with-files` before every `migrate`;
  - run the readiness check after the migrate;
  - a failed migrate is rolled back by restoring that backup (`bench --site <site> restore …`).
- **Currency change by a merchant:** after a store has orders its currency is fixed. If the shop's currency changes, new order pushes stop with `CURRENCY_MISMATCH`, and the Store's mismatch says so. Resolve it with the merchant; do not edit `shop_currency` on a store with orders.
- **Non-GST stores:** review the plain invoice (`tax_mode = None`) for each country before opening it to merchants there.
