# Architecture

## Overview
Field Sales is a Frappe v16 app (no ERPNext) for a brand's field reps: they check in at retail shops with GPS, take orders against live store stock, and the orders sync to the online store (Shopify today). Managers track visits, orders, sync health and sales-rep commission.
Backend: Python on Frappe (DocTypes, whitelisted APIs, RQ background jobs, scheduler). Frontend: a Vue 3 + frappe-ui SPA (Vite, Pinia, Tailwind) served at `/fieldsales` (alias `/fs`), plus standard Desk forms, reports and number cards.
Store access goes through a platform-neutral connector layer; only `connectors/shopify.py` knows Shopify (Admin GraphQL).

## Folder map
- `field_sales/hooks.py` – permissions hooks, scheduler, fixtures, website routes, app-screen entry.
- `field_sales/field_sales/doctype/` – DocTypes and their controllers (see Modules).
- `field_sales/field_sales/report/` – script reports: `rep_performance`, `rep_commission_summary`.
- `field_sales/field_sales/{number_card,dashboard_chart,workspace,page,notification}/` – Desk dashboard pieces, the "Field Order Sync Failed" Notification.
- `field_sales/api/` – whitelisted actions (`__init__.py`), dashboard data (`dashboard.py`), public webhook endpoint (`webhooks.py`).
- `field_sales/frontend_api.py` – read APIs for the SPA (session, sidebar counts, search, dashboard, notifications).
- `field_sales/connectors/` – `base.py` neutral contract + `ConnectorError`; `shopify.py` implementation; `__init__.py → get_connector()`.
- `field_sales/sync/` – background jobs: products, stock, customers, orders, webhooks, order confirmation email, realtime notify.
- `field_sales/payments/` – payment gateways: `razorpay.py` (Razorpay REST client, settings test, payment links).
- `field_sales/commission/` – commission engine, monthly statements, commission API.
- `field_sales/visit_import.py` – Excel/CSV visit import.
- `field_sales/permissions.py` – reports_to hierarchy permission rules.
- `field_sales/utils/` – `geo.py` (distance), `guards.py` (`keep_server_fields()`, `is_system_manager()`).
- `field_sales/daily_summary.py` – daily email to managers.
- `field_sales/www/fieldsales.{py,html}` – SPA shell page and boot data.
- `field_sales/templates/emails/` – email templates (order confirmation, daily summary).
- `field_sales/fixtures/print_format.json` – "Field Sales Order" and "Field Sales Invoice" print formats.
- `field_sales/patches/v1_0/` – data patches (listed in `patches.txt`).
- `field_sales/tests/` – integration tests (one file per feature).
- `frontend/src/` – SPA: `pages/`, `components/` (+ `layout/`), `composables/`, `stores/session.js`, `utils/` (`nav.js`, `api.js`, `format.js`), `router.js`, `sw.js` (PWA worker).
- `field_sales/public/frontend/` – Vite build output (generated; not edited by hand).

## Data flow
1. **Products & stock in:** scheduler → `sync/products.py → scheduled_sync()` → `connector.get_products()` → Platform Product + Product Stock rows. Live stock on order: `sync/stock.py → fetch_live_stock()`. Store pushes `inventory_levels/update`, `products/update|delete` webhooks (step 5).
2. **Customers out:** Retailer `on_update` → `sync/customers.py → enqueue_push()` → `push_retailer()` → `connector.create_customer()` → `external_customer_id`.
3. **Visit:** rep checks in/out via `api/__init__.py → check_in() / check_out()` (GPS, geofence, own visit, visit date). Creating/reassigning a Visit notifies the rep (Notification Log).
4. **Order out:** Field Order `on_submit` → `sync/orders.py → enqueue_push_order()` → `push_order()` → `connector.create_order()` (Draft mode: draft order; Confirmed mode: real order). On an order id: `commission/engine.py → enqueue_create_entries()` and `sync/confirmation.py → enqueue_order_confirmation()`. Draft mode gets its order id later from `link_completed_drafts()` (cron). Failures: `retry_failed_orders()` (cron), Sync Log, "Field Order Sync Failed" Notification.
5. **Webhooks in:** store → `api/webhooks.py → receive()` (HMAC verify, dedupe by delivery id in Redis, enqueue) → `sync/webhooks.py → process_event()` → stock/product updates or `commission/engine.py → reconcile_order()` for order paid/fulfilled/cancelled/refund. Stock events publish `stock_updated`; `products/update` applies payload prices (`connector.handle_product_event()` → `_apply_prices()`), re-fetches the product (`sync_one_product()`), then publishes `product_price_updated` `{product, price, compare_at_price}` for prices that changed (`_publish_price_changes()`). Both go to the Platform Product doctype room; the SPA listens via `composables/useRealtime.js`.
6. **Commission:** `reconcile_order()` re-reads the order (`connector.get_order()`) and sets entries to match the store's current state → monthly `commission/statements.py → generate_monthly_statements()` → Approve (submit) → `mark_paid()`.
7. **Emails:** order confirmation with invoice PDF (`sync/confirmation.py`), daily manager summary (`daily_summary.py`), Frappe Notifications; sent via the default outgoing Email Account.
8. Every store and Razorpay call writes a Sync Log (`sync_log.py → log_sync() / log_sync_durable()`).

## Modules
**Visits** – plan, check in/out with GPS and geofence. Entry: `doctype/visit/visit.py → Visit` (`_guard_check_in_fields()`, `_compute_distance_and_geofence()`, `_notify_rep()`); `api/__init__.py → check_in(), check_out(), _check_visit_date(), _check_own_visit()`; `utils/geo.py → haversine_m()`. UI: `pages/Visits.vue`, `pages/VisitDetail.vue`, `components/VisitDialog.vue`.

**Visit import** – Excel/CSV bulk planning (Sales Manager/System Manager). `visit_import.py → download_visit_template(), preview_visit_import(), create_visits_from_import(), _check_import_access()`. UI: `components/VisitImport.vue` (Visits page, `?tab=import`).

**Orders** – order entry with stock checks, discount limit, tax. `doctype/field_order/field_order.py → FieldOrder` (`validate()`, `_compute_item_amounts()` (rate = product price unless `PRICE_OVERRIDE_ROLES`), `_validate_amounts()` (tax 0–100, discount bounds), `_validate_discount()`, `before_submit() → _apply_live_stock()`, `on_submit()`); `sync/orders.py → push_order(), retry_failed_orders(), link_completed_drafts()`. UI: `pages/Orders.vue`, `pages/OrderDetail.vue`, `components/NewOrderDialog.vue`.

**Order confirmation & invoice** – one email per order once it reaches the store, GST tax invoice PDF, and a Razorpay Pay Now link when payments are enabled (see Payments). `sync/confirmation.py → send_order_confirmation(), resend_order_confirmation(), _build_pdf()` (60 s cap); print format "Field Sales Invoice" (fixture); company details on Store Connection (Invoice section). UI: Invoice card in `OrderDetail.vue`, `components/InvoiceSettings.vue`.

**Products & stock** – `sync/products.py → sync_products(), sync_one_product()`; `sync/webhooks.py → _process_product_update(), _apply_prices(), _publish_price_changes()`; `sync/stock.py → get_sell_locations(), set_location_stock(), fetch_live_stock()`. DocTypes: Platform Product, Product Stock, Store Location (child of Store Connection). UI: `pages/Products.vue`, `pages/ProductDetail.vue`.

**Barcode scanning** – camera scan via `components/BarcodeScanner.vue` (html5-qrcode; EAN-13/8, UPC-A/E, Code 128; emits `scanned`, releases the camera on close). Lookup `utils/api.js → findProductByBarcode()` (exact match on Platform Product `barcode`, tries UPC-A with/without leading 0). Scan buttons: Products page (opens the product; slot `search-actions` in `ListFilters.vue`) and the Order Detail item search (`onScanned()`: adds qty 1 or +1). Typed/USB-scanner input goes through the normal search boxes (barcode is a search field).

**Customers (Retailer)** – `doctype/retailer/retailer.py`; push in `sync/customers.py → push_retailer()`.

**Sales reps & hierarchy** – Sales Rep (`autoname field:user`, `reports_to`, `max_discount_pct`, commission plan). `permissions.py → get_allowed_reps()` (own rep + all below), `_query_conditions()`, `*_has_permission()`; `api/__init__.py → create_sales_rep()`. Passwords: own via `set_sales_rep_password()` (needs the current one); someone else's via `send_sales_rep_password_reset()` (Frappe's `User._reset_password(send_email=True)` → `/update-password?key=`), direct set for System Managers only; both use `_check_password_target()`. UI: `pages/SalesRepDetail.vue`.

**Commission** – plans (Flat %, Product Rate, Tiered Slab, Per Order), discount penalty, manager overrides, lifecycle Pending → Earned → Reversed/Cancelled, Adjustments for locked entries. `commission/engine.py → create_entries(), reconcile_order(), resolve_rate(), slab_rate(), penalty_multiplier()`; `commission/statements.py → generate_monthly_statements(), build_statement(), regenerate_statement(), mark_paid(), marginal_commission()`; `commission/api.py → get_my_commission_summary(), get_team_commission(), get_commission_entries(), save_commission_plan(), assign_commission_plan(), create_adjustment(), generate_statements(), approve_statement()`. DocTypes: Commission Plan (+ Rate, Slab), Commission Entry, Commission Statement (+ Entry). UI: `pages/Commission.vue`, `pages/CommissionStatement.vue`, `components/CommissionPlanDialog.vue`, `AssignPlanDialog.vue`, `CommissionAdjustmentDialog.vue`, `GenerateStatementsDialog.vue`.

**Reports** – `report/rep_performance/rep_performance.py → execute()`; `report/rep_commission_summary/rep_commission_summary.py → execute(), get_data(), _paid_statements()`. In-app: `pages/Reports.vue` (list from `utils/nav.js → REPORT_LINKS`), `pages/RepPerformance.vue`, `pages/RepCommission.vue` (both call `frappe.desk.query_report.run`).

**Sync monitoring** – Sync Log DocType (`clear_old_logs()` daily), `api/__init__.py → get_sync_failures(), retry_sync(), retry_all_sync_failures()`. UI: `pages/SyncLogs.vue`, `SyncLogDetail.vue`, `SyncFailures.vue`.

**Settings** – Store Connection (Single): platform, credentials, token, locations, order mode, invoice/company details, `metafields_ready_on`. `api/__init__.py → test_connection(), get_locations()`. UI: `pages/Settings.vue` (tabs Store / Invoice / Email / Razorpay), `components/StoreConnectionForm.vue`, `EmailAccountSettings.vue`.

**Payments (Razorpay)** – payment links for submitted Field Orders (in progress). Field Order Payment tab: `payment_status` (Unpaid / Link Sent / Partially Paid / Paid / Link Expired / Link Cancelled), `amount_paid`, `razorpay_payment_link_id|url`, `razorpay_payment_id`, `payment_link_expires_on`, `paid_on` — all read-only, allow_on_submit, no_copy; shown as a column pill in `field_order_list.js` (formatter; the status indicator stays sync status), Orders list column and Payment card in `OrderDetail.vue`. Razorpay Settings (Single, System/Sales Manager only): keys, webhook secret, link expiry, SMS/email notify, callback URL, read-only `webhook_url` (set in `onload`). `payments/razorpay.py → request()` (basic auth, 20 s timeout, raises `RazorpayError` with Razorpay's `error.description`), `test_connection()`, `get_webhook_url()` (the SPA's `frappe.client.get` skips `onload`). Links: `create_link_for_order()` (whitelisted; row lock `for_update`, `check_permission("write")`, reuses a valid Link Sent link) → `_replace_link()` (`_retire_link()` cancels a still-payable old one, `_create_unique()` picks reference_id NAME, NAME-2, …) → `create_payment_link()` / `_link_payload()` (paise, `indian_phone()`, notify only for present contacts and when `notify=True`). Confirmation email: `link_for_confirmation()` reuses or creates the link with Razorpay notify off and never raises (no link → email still goes out); called by `sync/confirmation.py → send_order_confirmation()` before its savepoint so a created link survives a failed send; template shows a Pay Now block when `payment_url` is set. Other helpers: `fetch_payment_link()`, `cancel_payment_link()`; `get_payment_config()` returns only `{enabled}`. Calls write Sync Log with platform Razorpay (`_logged()`). UI: Create Payment Link / Create New Link (expired or cancelled) / Copy Payment Link — Desk `field_order.js → _add_payment_link_button()` (Payment group) and the Payment card in `OrderDetail.vue → linkAction, createPaymentLink()`; hidden when Razorpay is off, Paid or Partially Paid (a new link would ask for the full total). Test Connection button in `razorpay_settings.js`; SPA Settings tab `?tab=razorpay` → `components/RazorpaySettings.vue` (editable by Sales Managers too, unlike the other Settings tabs; `Settings.vue → canEdit`).

**Notifications & shell** – `frontend_api.py → get_session(), get_sidebar_counts(), get_notifications(), search()`; `www/fieldsales.py → get_context(), has_app_access(), get_boot()`. UI: `components/layout/` (AppLayout, AppSidebar, MobileTabBar, CommandPalette, TopBar, UserMenu), `pages/Notifications.vue`, `pages/Dashboard.vue`.

## External services
- **Shopify Admin GraphQL**, version from `Store Connection.shopify_api_version` (currently `2026-10`). Auth: client-credentials grant (`connectors/shopify.py → _request_new_token()`), token stored encrypted on Store Connection, refreshed 5 min before expiry. Needs scopes for products, inventory, customers, draft orders, orders (`write_orders` for Confirmed mode; read for commission). App and store must be in the same Shopify organization (client-credentials rule).
- **Shopify webhooks** → `/api/method/field_sales.api.webhooks.receive`; topics `inventory_levels/update`, `products/update`, `products/delete`, `orders/paid`, `orders/fulfilled`, `orders/cancelled`, `refunds/create`. Subscriptions are created outside this app (no registration helper in code; the Sync Log "Register Webhooks" operation is unused).
- **Order metafields** (app-owned, namespace `$app:field_sales`): `field_order_no`, `sales_rep`; definitions created by `connectors/shopify.py → ensure_order_metafields()`.
- **WooCommerce / Odoo** – fields exist on Store Connection; no connector implemented (`connectors/__init__.py → get_connector()` only maps Shopify).
- **Razorpay REST API** `https://api.razorpay.com/v1`, basic auth with Razorpay Settings `key_id` / `key_secret`. Mode (Test/Live) is display-only: the key decides it. Webhook → `/api/method/field_sales.payments.razorpay.webhook`, signed with `webhook_secret` (not the key secret).
- **Email** – Frappe default outgoing Email Account (configured in Settings → Email).
- **PDF** – wkhtmltopdf via Frappe (`frappe.utils.pdf.get_pdf`).
- **Camera** – `html5-qrcode` (npm) in the SPA; uses the browser's BarcodeDetector when available.
- **Maps** – Leaflet in the SPA (`components/MapEmbed.vue`, `MapPickerDialog.vue`); tiles from OpenStreetMap, place search via OSM Nominatim (`MapPickerDialog.vue`).
- **Env vars / secrets**: none read from the environment; credentials live in Store Connection and Razorpay Settings (Password fields, read only with `get_password()`). `frappe.conf.developer_mode` affects the SPA shell. Site config `host_name` must be reachable locally (see Gotchas).

## Conventions
- Python: tabs, docstrings on public functions, `_()` for user-facing text, type annotations on all whitelisted methods (`require_type_annotated_api_methods = True`).
- Store-specific code only in `connectors/`; callers use neutral shapes documented in `connectors/base.py`. Platform ids are stored plain (no `gid://`).
- Store failures raise `ConnectorError(message, code, http_status, details, partial)`; sync jobs catch it, mark the record Failed, write Sync Log, and do not re-raise; other exceptions are re-raised after logging.
- Background work: `frappe.enqueue(..., enqueue_after_commit=True, job_id=..., deduplicate=True)`. Never pass `event=` as a job kwarg (reserved by `frappe.enqueue`).
- Permissions: rep-scoped DocTypes use `permissions.REP_FIELD` + hooks `permission_query_conditions` / `has_permission`; manager actions check Sales Manager / System Manager.
- Roles: System Manager, Sales Manager, Field Sales Rep.
- Frontend: frappe-ui components, `PageHeader`, `SectionCard`, `DataTable` (page-size footer), `StatusPill`, `NumberCard`, `EmptyState`; page tabs kept in `?tab=`; nav in `utils/nav.js`. Only Tailwind classes present in the frappe-ui 1.0 build (e.g. no `bg-surface-white`).
- Tests: `field_sales/tests/test_*.py`, `frappe.tests.IntegrationTestCase`, store mocked by patching connector methods; per-test rollback in `tearDown` (class-level rollback only otherwise).

## Commands
- Run: `bench start` (web, workers, scheduler, socketio). SPA dev server: `cd frontend && yarn dev` (port 8080, proxies to bench; `FS_DEV_PORT` to change).
- Build SPA: `cd frontend && yarn build` (→ `field_sales/public/frontend`, `www/fieldsales.html`, PWA `sw.js`).
- Migrate: `bench --site <site> migrate`.
- Tests: `bench --site fieldsales-test.localhost run-tests --app field_sales [--module field_sales.tests.test_x]` — only on the dedicated test site (`allow_tests`). Needs Redis on 11000/13000 (`bench start` or `redis-server config/redis_*.conf`); without it ~130 tests error in `global_search.sync_value_in_queue`.
- Manual commission sync: `bench --site <site> execute field_sales.commission.engine.reconcile_order --kwargs "{'external_order_id': '<id>'}"`.
- Deploy: TODO: verify (no deploy scripts in the repo).

## Gotchas
- Store Connection is a Single; `frappe.db.get_single_value` returns `0001-01-01` for an unset Datetime — read via `frappe.get_single(...)` instead.
- Commission is state-based: replayed or out-of-order webhooks are safe; entries on Approved/Paid statements are never edited — corrections become negative Adjustments (so Unpaid can go below zero).
- Commission entries keep the rate they were created with; editing a plan doesn't change existing entries. Tiered Slab rates are provisional until the statement is built.
- Field Order tax is calculated before the discount (invoice prints the Field Order's own amounts). TODO: verify with accounting whether tax should follow the discount.
- Draft mode: the store order (and commission, confirmation email, metafields) only exists once the draft is completed; Shopify doesn't copy draft metafields to the order, so `link_completed_drafts()` sets them.
- Retry de-duplication finds an existing order by `source_identifier` (Field Order name), falling back to the legacy `field-order-<name>` tag.
- PDF generation fetches print CSS from the site's `host_name`; a public/unreachable `host_name` (e.g. a tunnel URL) makes wkhtmltopdf hang — capped at 60 s in `sync/confirmation.py`.
- Shopify requests use a (5 s connect, 30 s read) timeout; a broken IPv6 route otherwise stalls each call.
- Webhook signatures are verified with the app's client secret; webhooks created in Shopify admin may use a different key and fail verification.
- The SPA's only realtime use is `composables/useRealtime.js → onDoctypeEvent()` (Order Detail: live price/stock); sidebar counts still poll every 60 s and some pages poll for job results. The socket goes to `hostname:socketio_port` when `dev_server` (boot values from `www/fieldsales.py → get_boot()`), else the page origin — production needs nginx proxying `/socket.io`; a tunnel to the dev server gets no realtime.
- Field Order Item `rate` is sent to the store as the line price, so for non-managers the server sets it to the product's current price on save (read-only is only a UI hint; REST could set it) — except a saved row whose product and rate are unchanged (`get_doc_before_save()`), so a rep's save keeps a manager's rate and the price the draft was made at. An amended order's rows are new rows, so they get current prices.
- Price webhooks never change `rate` on order rows: draft rows show "Price changed" (`OrderDetail.vue → priceChange()`) with Apply (for a rep, any rate change becomes the product's current price, which is the same thing); the announced price is the one stored after the re-fetch, so out-of-order deliveries can't announce a stale price.
- Geofence can't be moved by reps: a located Retailer's latitude/longitude and any `geofence_radius_m` are manager-only (`retailer.py → _guard_location()`; reps may set the first location, check-in sets it via `frappe.db.set_value`). A checked-in Visit keeps the retailer position it was measured against (`visit.py → _guard_check_in_fields()`, `RETAILER_POSITION_FIELDS`); without that, the read-only `fetch_from` re-fetches the Retailer's current position on every save and recomputes the distance.
- Check-in only on the visit's date (system time zone) and only by the assigned rep; check-out is not date-limited and managers may check out a rep's visit.
- Excel import is manager-only and re-validates every row server-side on create; uploads are private and deleted after a successful import.
- Notification Log "Alert" type is delivered even to the sender, so `Visit._notify_rep()` skips self-assignments itself.
- Camera (barcode scan) and GPS only work in a secure context: HTTPS or `localhost`. Plain `http://<LAN-IP>` on a phone shows "Camera not available".
- Print formats render with wkhtmltopdf (old WebKit): no flexbox/grid — lay out with tables; colours need `-webkit-print-color-adjust: exact` (see "Field Sales Order" in `fixtures/print_format.json`). Fixtures only re-sync when their `modified` is newer than the site's.
- `create_sales_rep()` can link any existing User, so rep-scoped actions on the *user* must check that user's roles: `_check_password_target()` lets non-System Managers reset only users whose roles are within `REP_ONLY_ROLES` (Field Sales Rep + automatic roles).
- Frappe only sends system email through an Email Account that is enabled **and** default outgoing (or site config `mail_server`); an enabled non-default account is not enough. Check with `EmailAccount.find_default_outgoing()`.
- Field Order Item `available_qty` is always the server's stored stock at the order's location (`_set_row_stock()`, on draft saves *and* the submit save), never the client's; don't set it from Platform Product, which holds only the default location's stock and would also undo `_apply_live_stock()`.
- Store ids and sync state on Field Order (`SYNC_FIELDS`) and Retailer (`SERVER_FIELDS`) are server-only: `validate()` undoes client changes (`utils/guards.py → keep_server_fields()`; new and duplicated docs start clean; System Managers may correct saved ones). Sync code must write them with `db_set` / `frappe.db.set_value`, or save with `flags.ignore_sync` (Retailer). `location_id` is deliberately not guarded: it is the user's pick, checked by `_validate_location()`. Field Order has no `amended_from`, so cancelled orders are redone by Duplicate. Payment fields (`field_order.py → PAYMENT_FIELDS`) are guarded for everyone, System Managers included, in `validate()` **and** `before_update_after_submit()` (they are allow_on_submit, and validate does not run on Update after submit); payment code must `db_set` them.
- Razorpay `reference_id` must be unique per link, so a replacement link for an order uses NAME-2, NAME-3 (next after the stored link's reference; a taken id moves on). Expiry is stored in the system time zone from Razorpay's `expire_by` (`_from_unix()`); `link_expiry_hours = 0` means no expiry. If creating fails after the old link was cancelled, the order is committed as Link Cancelled before the error.
