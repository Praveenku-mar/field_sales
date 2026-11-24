# Field Sales — one site for all merchants: design note (Phase 1)

Shopify edition, 21 Nov 2026. Source plan: *Field Sales Multi-Merchant Plan* (19 Nov 2026). The code facts behind every statement here are in [inventory.md](inventory.md).

## 1. Agreed decisions

| # | Decision |
|---|---|
| D1 | **Mixed countries.** Each store has its own time zone, currency and country. Nothing writes System Settings any more |
| D2 | **Shopify now, others later.** Built in `field_sales`; the store context, `get_connector(store)` and the isolation test fixture stay platform-neutral |
| D3 | **One vendor sender.** Sender display name = store name, `reply_to` = `Store.company_email` |
| D4 | **Per-store numbers for orders and invoices.** Visits, retailers, commission records and privacy requests share site series |
| D5 | **No merge tool.** Existing single-store sites keep running |
| D6 | **One server address** for the mobile app; the store comes from the login |

## 2. The Store model

New DocType **Store**: not single, named by `store_code`, permission level System Manager + Store Admin (own store only). It takes every **S** field in inventory §2:

- **Identity:** `store_code` (2–4 upper-case letters/digits, unique; changeable by rename until the first invoice), `store_name`, `platform`, `store_url` (unique, normalised as `_normalize_store_url` does today), `is_active`, `installed_on`, `uninstalled_on`.
- **Connection:** `shopify_auth_mode` (always OAuth on the shared site, Q3), `shopify_scopes`, `access_token`, `refresh_token`, `token_expiry`, `refresh_token_expiry`, `embedded_user`, `last_test_status`, `last_tested_on`, `metafields_ready_on`.
- **Selling:** `locations` (Store Location child table), `default_location_id`/`_name`, `order_mode`, `product_sync_frequency`.
- **Locale (D1):** the existing `shop_country`, `shop_currency`, `shop_timezone`, filled from the shop by `sync/shop_settings.py`; read-only for merchants, and the currency is locked once the store has orders (§6).
- **Tax and invoice:** `tax_mode` (`GST` / `None`, defaulting to `GST` when `country` = India); `company_name`, `company_address`, `company_state`, `company_gstin` (required when GST), `company_phone`, `company_email`.
- **Features:** `send_order_confirmation`, `enable_commission`, `retailer_inactive_anonymise_days`.
- **Payments:** none on the shared site. Shopify App Store requirement 1.1.2 (*Use Shopify checkout*) prohibits taking payment outside Shopify checkout and registering it through the API, so Razorpay and `orderMarkAsPaid` are off when `field_sales_multi_store` is set (see §7 *Checkout rule*). Razorpay stays as-is on single-store sites.

**Store Connection** shrinks to the vendor-wide settings: `shopify_api_version`, `plan_limits`, `failed_log_retention_days`. It is renamed in the UI to *Field Sales Settings* (the DocType keeps its name to avoid churn) and only System Manager can open it. Mobile Push Settings stays as it is.

**`tax_mode` is new (not in the PDF).** With mixed countries, a non-India store can't print a GST invoice. When `tax_mode = None`, the invoice print format leaves out GSTIN/HSN/CGST-SGST-IGST, and `_compute_totals()` treats the line `tax` as a plain sales-tax percentage.

### store link on business DocTypes
Each of these gets a required, read-only-after-insert `store` Link with `in_standard_filter`:
- Retailer, Field Order, Visit, Platform Product, Product Stock, Sales Rep
- Commission Plan, Commission Entry, Commission Statement
- Payment Transaction, Sync Log, Privacy Request (Razorpay Webhook Event gets no store: Razorpay is off on the shared site)

Mobile Device follows its user's store, so it needs no field. Child tables follow their parent.

Composite indexes: `(store, sales_rep, order_date)` on Field Order, `(store, sales_rep, visit_date)` on Visit, `(store, status)` on Sync Log, `(store, external_variant_id)` on Platform Product. Uniqueness rules are in inventory §5.

### Migration patch (Phase 2)
`patches/v2_0/create_first_store.py`:
1. Create a Store from the current Store Connection values, including passwords copied with `get_decrypted_password` → `set_encrypted_password`, and the location rows. `store_code` comes from the shop name, can be changed before go-live.
2. `update tab<X> set store = %s` on every table above.
3. (Razorpay Settings left untouched: single-store sites only.)
4. Clear the moved fields from Store Connection.
5. Give existing Sales Manager / Sales Rep / Retailer users a User Permission for the Store.

The patch is idempotent: it skips work if a Store already exists.

## 3. Users and roles

| Role | Who | Scope |
|---|---|---|
| System Manager | Vendor staff only | All stores, Desk, Field Sales Settings |
| **Store Admin** (new) | Merchant owner, plus the embedded user | Their store: Store form, sync failures, sync log, email account, users |
| Sales Manager | Merchant managers | Their store, their rep hierarchy |
| Field Sales Rep | Reps | Their store, own records |
| Field Sales Retailer | Retailer logins | Their store, own retailer |

- **One user, one store.** The store is held as a **User Permission** (`allow = Store`, `apply_to_all_doctypes = 1`, `is_default = 1`). There's no new field on User. The Frappe User Permission is both the source of truth and the second line of defence. The `utils/store.py → user_store(user)` helper reads it and is cached for the request. Creating a Sales Rep, retailer login or embedded user (`sales_rep.py:55`, `api/__init__.py:300`, `shopify/embedded.py:193`) adds the permission in the same transaction.
- **Store Admin replaces System Manager** in every merchant-facing gate: `utils/guards.py → is_system_manager()` (5 calls) and the 9 copies of `ADMIN_ROLES`/`MANAGER_ROLES`/`MONITOR_ROLES` (inventory §6). Phase 3 first gathers them into `permissions.py`.
- The embedded user gets Store Admin instead of System Manager-equivalent rights. `shopify/merchant_settings.py` stays as the gatekeeper for the Settings forms.

## 4. Keeping merchants apart

Three layers:

1. **Frappe User Permissions:** automatic for list, report, form, REST and `frappe.get_list` on every DocType with a `store` link.
2. **App permission hooks:**
   - `permissions.py → _query_conditions` and each `*_has_permission` start with `store = user_store(user)`, so even `is_admin()` (System Manager) users who belong to a store are limited to it. Only users with no store, i.e. vendor staff, see everything.
   - New query/has_permission pairs for Platform Product, Product Stock, Sync Log, Commission Plan.
   - `check_owner_change` (`permissions.py:315`) also refuses any change to `store`. The `doc_events` list grows to every store-linked DocType.
   - A `before_insert` doc event stamps `store = current_store()` when it is empty, and refuses a store that differs from the user's.
3. **Explicit filters where permissions don't apply.** Every `ignore_permissions`, `frappe.get_all`, `frappe.qb` and `frappe.db.sql` call (inventory §1: 58 / 76 / 60 / 2) adds `store = current_store()`.
   - Phase 3 makes the audit list from the grep and fixes it file by file, in this order: `api/mobile.py`, `api/__init__.py`, `api/dashboard.py`, `frontend_api.py`, `commission/`, `sync/`, `payments/`, `push/`, `daily_summary.py`.
   - A test-time guard checks that queries on store-linked tables carry a store condition. See §8.
4. **Role-wide recipient lookups:** `sync/notify.py → users_with_roles(roles)` takes a required `store` and joins User Permission. This covers low-stock alerts, payment notices, sync-failure and privacy notices, and the daily summary.

## 5. Current store for each request and job

`field_sales/utils/store.py` (new):
- `current_store()`: returns `frappe.local.field_sales_store`, or raises if it isn't set.
- `set_current_store(store)`
- `user_store(user)`
- `store_for_shop(shop_domain)`
- `use_store(store)`: a context manager for jobs and loops.

Platform-neutral (D2).

| Entry point | How the store is set |
|---|---|
| Embedded Shopify admin | `shopify/session_token.authenticate` (existing `auth_hooks`) → `store_for_shop(token.dest)` → sets the store and signs in that Store's `embedded_user` |
| Web / mobile login | New `before_request` hook (`hooks.py:289`, now enabled) → `user_store(session.user)`. Guest requests leave it unset |
| Shopify store webhooks (`api/webhooks.py:15`) | HMAC is checked with the vendor app secret first (OAuth webhooks are signed with it), then `store_for_shop(X-Shopify-Shop-Domain)`. An unknown or inactive shop gets 200 and is ignored |
| App webhooks (`shopify/webhooks.py:51`) | Same, for uninstall and the 3 privacy topics |
| Install / OAuth (`shopify/oauth.py:53, 81`) | The signed `shop` parameter. `store_for_shop` or create |
| Razorpay webhook (`payments/razorpay.py:526`) | Not served on the shared site: refused when `field_sales_multi_store` is set |
| Guest app endpoints (`www/fieldsales.py:82`, `api/mobile.py:1450`, `api/__init__.py:1300`) | Pre-login; no store needed. Mobile login returns the user's store in `get_session` (D6) |
| Background jobs | `frappe.enqueue(..., store=current_store())`. A `before_job` hook (`hooks.py:295`) pops `store` and calls `set_current_store` |
| Scheduled jobs | `for store in active_stores(): with use_store(store): …`, with a commit and `frappe.log_error` for each store, so one failure doesn't stop the rest. Behaviour of each job: inventory §7 |

**Connector.** `connectors/__init__.py → get_connector(store=None, require_enabled=True)` builds a `ShopifyConnector` from the Store row; the default is `current_store()`. Token refresh writes to that Store. `TOKEN_LOCK_KEY` becomes `field_sales:shopify_token_refresh:{store}`.

**Job ids** gain the store: `field_sales:product_sync:{store}`, `field_sales:shop_settings_sync:{store}`, `field_sales:register_webhooks:{store}`, `field_sales:setup_new_store:{store}`. Per-document ids stay.

**Caches.** Only the token lock must change. Other keys already use globally unique ids (inventory §6). `frappe.get_cached_doc("Store Connection")` in `sync/orders.py:267` becomes `frappe.get_cached_doc("Store", store)`.

## 6. Per-store settings

**Rule (D1).** The site keeps one fixed clock; each store's zone and currency are applied where a date, a period or an amount is worked out or shown. No currency conversion, and nothing writes store values into System Settings on the shared site. The store's values are the existing `Store.shop_timezone`, `shop_currency` and `shop_country`, filled by `sync/shop_settings.py → sync_shop_settings()`.

**Time zone.**
- **Fixed site clock.** The shared site's System Settings `time_zone` is set once and never changed (changing it would shift every stored time). All Datetime fields stay naive in that zone, as Frappe expects.
- **Dates vs times.**
  - Date fields mean the merchant's local date: `order_date`, `visit_date`, `posting_date`, statement periods.
  - Datetime fields are moments, stored in the site zone: check-in/out, `submitted_on`, `paid_on`, sync times.
- **Helpers** in `utils/store.py`: `store_now()`, `store_today()`, `store_month_bounds(date)`. They use Frappe's `get_datetime_in_timezone()` / `convert_utc_to_timezone()` with the current Store's `shop_timezone`, and `store_to_site(dt)` turns a local boundary into the site zone for Datetime filters.
- **Call sites.**
  - `today()`/`nowdate()` (26 calls in 14 files) become `store_today()`.
  - Each `now_datetime()` (51 in 19 files) is audited: stamping a moment stays; deciding "which day" changes. That covers the check-in "only on the visit's date" rule, dashboard Today/This month, Rep Performance, statement periods, the plan's orders-this-month count and the Razorpay expiry display.
- **Web app.** The boot (`www/fieldsales.py → get_boot()`) sends **two** values:
  - `server_time_zone`, to read server times correctly
  - `time_zone` (the store's), for display and for "today"
  
  `frontend/src/utils/format.js` (`siteNow()`, `today()`, `nowSql()`, `isPast()`, `msUntil()`) is changed to parse with the first and show with the second. Sending only the store zone would misread every server time.
- **Desk.** Each user's User `time_zone` is set to their store's zone when the user is created (Sales Rep, retailer login, embedded user); Frappe's Desk then shows times in it.
- **Emails, prints, mobile.** The confirmation email, daily summary and invoice print format times with the store's zone. `api/mobile.py → get_session` returns the store's zone; the mobile app must use it for "today" (check `models.dart` / its date utils).
- **Local-time jobs.**
  - The daily summary changes from `0 20 * * *` to hourly and sends for each store where it is 20:00 locally.
  - Monthly statements change from `0 2 1 * *` to hourly on days 1–2, and build each store's statement once its local month has ended.
  
  Both are idempotent per store, so a repeat hour does nothing.

**Currency.**
- **Store currency.** `shop_currency` is filled from the shop and locked once the store has a Field Order. If the merchant later changes their Shopify currency, the existing "wrong currency" check (`connectors/shopify.py`, the order push) refuses new orders and shows the mismatch; vendor staff resolve it.
- **Every money record carries its currency.**
  - Payment Transaction, Commission Entry and Commission Statement already have a `currency` field.
  - Field Order and Platform Product get one, set on insert from the store, and their Currency fields (6 and 2, `options` empty today) get `options: "currency"` so Frappe formats each amount with its own symbol.
  - A patch fills existing rows from the store.
- **Showing amounts.**
  - `utils/money.py → site_currency()` becomes `store_currency(store=None)` for new values.
  - Existing records show their own `currency`: the confirmation email, the two print formats (which read `frappe.db.get_default("currency")` today), the dashboard, reports, commission and Razorpay text.
- **No mixing.**
  - A merchant's lists and reports cover one store, so one currency.
  - Vendor-wide reports group totals by currency and never add them together.
  - Commission plan amounts (slab limits, flat amounts) are in the plan's store currency, since each plan belongs to one store.
- **No conversion.** Shopify prices and draft orders are already in the shop's currency; amounts pass through unchanged, as today.

**Single-store sites.** Unchanged: `match_shop_settings` may still copy the shop's currency and zone into System Settings. On the shared site (`field_sales_multi_store`) that copying (`sync/shop_settings.py` → `_apply_currency()`, `_apply_timezone()`) is skipped and the field is hidden.

**Email (D3).**
- `frappe.sendmail(..., reply_to=store.company_email, sender=formataddr((store.store_name, default_sender)))` in `sync/confirmation.py`, `daily_summary.py` and the invite/welcome mails.
- One vendor Email Account with SPF/DKIM on the vendor domain.
- The merchant Email Account screens in `shopify/merchant_settings.py` (`_merchant_accounts`) are removed for shared-site stores.

**Print formats.** The invoice and order print formats read company details and `tax_mode` from `doc.store`, using a Jinja helper `get_store(doc.store)`.

**Numbering (D4).**
- Field Order `naming_series` becomes `FO-.store_code.-.YYYY.-.#####`, set in `autoname()` via `make_autoname(f"FO-{code}-.YYYY.-.#####")`.
- Invoice: `make_autoname(f"{code}/.YY./.#####")`. For example `ABCD/26/00001` is 13 characters, within GST's 16-character limit.
- Uniqueness: (`store`, `invoice_no`) and (`store`, `client_temp_key`). Frappe's `unique` flag is dropped in favour of a composite unique index added in the doctype `on_doctype_update`.

## 7. Shopify install, uninstall, billing, privacy

- **Install (`shopify/oauth.py`).** An unknown shop gets a new Store with:
  - a code from its name, with a numeric suffix if taken
  - `tax_mode` and `shop_currency`/`shop_timezone`/`shop_country` from the shop
  - an embedded user with Store Admin plus its User Permission
  - its first location and first product sync, queued under that store's job ids
  
  A shop with an existing inactive Store is reactivated. The one-shop-per-site check is removed, along with the `session_token.py:40` "token for another shop" refusal.
- **Uninstall.** Sets `is_active = 0`, clears tokens, and the store's users stop being able to log in (`before_request` refuses users of an inactive store). Data is kept until `shop/redact`.
- **Privacy.** `customers/redact` and `customers/data_request` run inside that store. `shop/redact` anonymises or deletes that store's records only (`utils/anonymise.py`), then disables its users.
- **Checkout rule (App Store requirement 1.1.2).** On the shared site, orders go to Shopify only as **draft orders**. The buyer pays through the draft's Shopify `invoiceUrl` (Shopify checkout), or the merchant completes the draft in Shopify admin for credit or cash terms. `order_mode = Confirmed` (`orderCreate` + `orderMarkAsPaid`, `connectors/shopify.py:819, 881`) and `sync/orders.py → retry_store_payments` are refused or skipped when `field_sales_multi_store` is set. Payments reps record in the app stay in Field Sales only.
- **Billing (`shopify/billing.py`).** Plan lookup is per store, using the current store's token. Usage counts (reps, orders this month in the store's zone) filter by store. `plan_limits` stays vendor-wide.

## 8. Tests

- **Fixture:** `tests/utils.py → make_store(code="TST")` plus a `StoreTestCase` base class. It creates and enters a store, and gives created users their User Permission. The existing tests switch to it. Most changes are mechanical: `frappe.get_single("Store Connection")` → store fixture.
- **Isolation suite** `tests/test_store_isolation.py`. Stores A and B, each with a Store Admin, manager, rep, retailer, orders, visits, products, commission and payments. For every whitelisted endpoint (all 108, listed from the `@frappe.whitelist` grep), Desk list/report, REST `GET/PUT/DELETE`, mobile API, webhook and scheduled job, it checks that A's users see and change nothing of B's.
  - A meta-test fails when a new whitelisted endpoint is added without an isolation case.
- **Install tests:** two shops installing in turn; uninstall and `shop/redact` of one leaves the other untouched.
- **Locale tests:** a store in `America/New_York` and one in `Asia/Kolkata`: "today", statement month, daily summary hour, invoice tax layout.
- Runs only on `fieldsales-test`, never dev sites.

## 9. Risks and open questions

| Risk | Handling |
|---|---|
| A missed query leaks data | Three layers (§4), the isolation suite with an endpoint meta-test, and a `frappe.qb`/`get_all` audit list ticked off in review |
| `current_store()` not set in some path | It raises instead of returning `None`, so a missing context fails loudly in tests, not silently in production |
| Shared workers overloaded | Product syncs staggered by store; queues sized in Phase 9; per-store job ids stop pile-ups |
| One bad deploy hits everyone | Staging with 2–3 Shopify dev stores, a backup before every migration, `Store.is_active` as a per-store off switch |
| Non-GST invoice correctness | `tax_mode = None` invoice reviewed for each launch country before that country is opened |

**Decided (21 Nov 2026):**
- **Q1 Store code.** Generated at install from the shop name (2–4 chars, numeric suffix if taken). Vendor staff can change it until the store's first invoice is issued; after that it is fixed (`Store.validate` refuses the change once any Field Order of the store has an `invoice_no`).
- **Q2 Vendor support access.** Vendor staff have no store and hold the **Field Sales Vendor** role (`utils/store.py → VENDOR_ROLE`; on a single-store site a System Manager of no store), and see all stores in Desk. Lacking a store alone never grants it: a System Manager of no store without the role reaches no store. In the web app they pick a store with a "switch store" control, which sets the current store for their session only (vendor staff only) and writes a Comment / Activity Log entry on the Store for each switch.
- **Q3 Install type.** The shared site accepts OAuth (App Store) installs only. `Store.shopify_auth_mode` is fixed to OAuth there. The client-credentials code path stays for single-store sites and is selected by a site_config flag (`field_sales_multi_store`), so the client id/secret fields are dropped from Store.
- **Q4 Razorpay.** Dropped from the shared site entirely (App Store requirement 1.1.2); kept for single-store sites. Rep collections are recorded in Field Sales only and never pushed to Shopify as payments.

## 10. Revised estimate (working days)

| Phase | Days | Change from PDF |
|---|---|---|
| 1. Design and inventory | 2–3 | — |
| 2. Store model and migration | 7–8 | +2: `tax_mode`, composite indexes, and all Store Connection reads/writes move to Store here (moved from Phase 4) |
| 3. Keeping merchants apart | 7–9 | +2: 60 `frappe.qb` queries, role-constant consolidation, `users_with_roles` |
| 4. Request and job context | 5–6 | `before_request`/`before_job` hooks are new, not existing |
| 5. Per-store settings | 7–9 | +2: non-GST invoice, timed daily summary/statements |
| 6. Install, billing, privacy | 4–5 | — |
| 7. Web app and mobile | 3–4 | — |
| 8. Tests | 6–8 | +1: locale tests, endpoint meta-test |
| 9. Rollout (no merge tool, D5) | 2–3 | −1 to −2 |
| **Total** | **41–53 (8–11 weeks)** | Up from 35–50 because of the extra findings and D1 = mixed |

## 11. Status
- **Phase 2 done (21 Nov 2026):** Store DocType, `utils/store.py`, `store` links + indexes, all Store Connection uses moved to Store, shared-site rules behind `field_sales_multi_store`, patch `v2_0/create_first_store` (run on `fieldsales-test`). Store Connection keeps `failed_log_retention_days` and `plan_limits` only; `shopify_api_version` and the client id/secret stay per store, since single-store sites still use client credentials.
- **Phase 3 done (21 Nov 2026):**
  - one user = one store through a default User Permission on Store (`utils/store.py → assign_user_to_store()`), set when a Sales Rep, a retailer login or the embedded user is created (patch `v2_0/assign_users_to_store` for existing users)
  - new **Store Admin** role; the role sets live only in `permissions.py`; frontend role lists updated
  - the store comes first in every permission query, `has_permission`, `scope_criterion` and `hierarchy_filters`; store-only hooks for Platform Product, Product Stock, Sync Log, Commission Plan, Privacy Request; the mobile raw-SQL lists add it centrally
  - alert recipients (`users_with_roles`) and plan usage are per store; a new record takes its rep's store
  - Commission Plan names unique per store (`<name> (<store>)` when taken by another store)
  - audit of all 108 endpoints in `inventory.md` §8, enforced by a test; isolation suite `tests/test_store_isolation.py`
- **Changed from the plan:** there is no `before_request` hook: API-key (mobile) and Shopify session-token logins happen after Frappe calls it, so `get_store_name()` reads the session user's store lazily instead. Refusing a user of an inactive store moves to Phase 6, because `Store.enabled` is also switched off by a failed token refresh and the merchant must still be able to open the app to reinstall.
- **Store-scoped already (review of PR #43):** product sync (per-store job id, scheduled for each enabled Store, upsert/archive/webhook queries by store), shop redaction and customer privacy lookups, plan cache per store, rep hierarchy query per store; the shared site never changes System Settings currency/time zone (per-store currency in records: Phase 5).
- **Phase 9 done (23 Nov 2026):** product syncs capped per scheduler tick, most overdue first (`field_sales_product_syncs_per_tick`); readiness command `field-sales-check-shared` (`utils/rollout.py`); rollout guide `rollout.md` (staging, config, workers, App Store checklist, operations). D5 kept: no merge tool.
- **Phase 8 done (23 Nov 2026):** the store fixture (`tests/store_fixtures.py`) and two-store isolation suite were built in Phases 3–7; Phase 8 adds `tests/test_shared_site_flows.py`, whole flows on the shared site with a second store's records checked unchanged. It found that order events looked up Field Orders by `external_order_id` across stores (commission reconcile, store payment, draft linker): now filtered by store. Decided: the older single-store test files stay as they are (not moved onto the shared fixture).
- **Phase 7 done (23 Nov 2026), server and web app:** vendor store switcher (Q2: per-session store, recorded on the Store, picker before the app shell), merchant view of the connection settings on the shared site, GST row/fields hidden for `tax_mode` None, session gives the store `code`, `title`, `tax_mode` and `currency`. Not done: renaming the Store Connection doctype in Desk (the app's tab already edits the Store; the doctype is System Manager only). **Mobile app (separate repo):** use session `currency`, `time_zone` / `server_time_zone` and `store.tax_mode`.
- **Phase 6 done (23 Nov 2026):** shop settings learnt at install on the shared site regardless of `match_shop_settings`, tax mode from the shop's country while a store has no orders, users of an uninstalled store refused at login and on every request (auth hook on `uninstalled_on`; the design's `is_active` is `enabled` + `uninstalled_on`), `shop/redact` disables the store's users, reinstall re-enables its embedded user. Install, reinstall, per-store billing counts and draft-only orders were already done in Phases 2–5.
- **Phase 5 done (23 Nov 2026):** store clock (`store_timezone/store_today/store_to_site`, Date fields filled from it, boot and session send `server_time_zone` + `time_zone`, Desk users get the store zone, summary at 20:00 store time, statements once the store month ends); record currency (`currency` on Field Order and Platform Product, `options: currency`, patch `fill_record_currency`, locked once a store has orders, push refused on mismatch, reports one store at a time); mail in the store's name (`store_mail_args`), no merchant Email Accounts on the shared site; non-GST stores (`tax_mode` None). Changed from the plan: the "Today" defaults were dropped (filled on insert) instead of auditing every caller; the Privacy Request mail keeps the vendor sender. **Mobile app (separate repo, not changed here):** use `get_session` `time_zone` for "today" and `server_time_zone` to read server times, and each record's `currency` for amounts.
- **Phase 4 done (23 Nov 2026):** `enqueue_in_store` / `run_in_store` (a wrapper job instead of a `before_job` hook: Frappe's deadlock retry re-uses the popped kwargs, so the store would be lost), `for_each_store` for scheduled jobs, `shop_store(shop)` for webhooks, session token and OAuth install (a new shop on the shared site gets its own Store), Error Log for shops with no Store (`log_sync_or_error`), Razorpay webhook refused on the shared site, token lock per shop for exchange and refresh. Daily summary and statements loop stores at their old cron times; local-time sending stays Phase 5.
