# Multi-merchant inventory (Phase 1)

Measured on `develop` at `dc2dc4d`, 21 Nov 2026. Tests are excluded throughout.

**Classes:**
- **S** (per-Store): moves to the new Store record and is read through the current store.
- **V** (vendor-wide): stays site-wide.
- **R** (remove): goes away.
- **P** (patch-only): old patch; no change, marked obsolete.
- **D** (doc only): a comment or docstring wording change.

## 1. Counts checked against the plan PDF

| Item | PDF | Measured | Note |
|---|---|---|---|
| Store Connection refs in .py | 79 in 22 files | **152 lines in 34 files** (53 `get_single`/`get_single_value` calls) | The PDF left out patches, docstrings and doctype modules |
| Store Connection refs in the Vue frontend | — | 23 lines in 9 files (`frontend/src`) | Not in the PDF |
| Whitelisted endpoints | 108 | **108 in 21 files** (8 `allow_guest`) | Matches |
| Scheduled jobs | 10 | **10** (`hooks.py:214`) | Matches |
| Raw SQL | 2 | **2** outside patches (`commission/__init__.py:14`, `api/mobile.py:184`) | Matches. There are also **60 `frappe.qb` queries in 11 files**, which bypass permissions just like raw SQL |
| `ignore_permissions` | — | 58 in 23 files | |
| `frappe.get_all(` | — | 76 in 25 files | |
| Permission hooks | 8 | **7** query + 7 has_permission (`hooks.py:177-195`) | Notification Log is a `doc_events` hook, not a permission hook |
| `before_request` hook | "existing" | **Does not exist** (commented out, `hooks.py:289`). Only `auth_hooks` (`shopify/session_token.authenticate`) | Phase 4 adds it |

## 2. Store Connection fields

| Field(s) | Class | Note |
|---|---|---|
| `platform` | S | Always Shopify today; keep per store for D2 |
| `enabled` | S | Becomes `Store.is_active` plus the connection state |
| `store_url` | S | Unique per store; the key for webhook and session-token lookups |
| `shopify_auth_mode` | S | OAuth for App Store stores. Client Credentials only for legacy/dev stores |
| `shopify_client_id`, `shopify_client_secret` | S (legacy) / V (OAuth) | OAuth mode already reads the app id from `site_config` (`connectors/shopify.py:1014`) |
| `shopify_api_version` | V | One app, one API version |
| `shopify_scopes`, `installed_on`, `uninstalled_on` | S | |
| `embedded_user` | S | One embedded user per store |
| `access_token`, `refresh_token`, `token_expiry`, `refresh_token_expiry` | S | Encrypted Password fields on Store |
| `default_location_id`, `default_location_name`, `locations` (Store Location child table) | S | Child table moves under Store |
| `order_mode`, `product_sync_frequency` | S | |
| `last_test_status`, `last_tested_on`, `metafields_ready_on` | S | |
| `shop_name`, `shop_currency`, `shop_country`, `shop_timezone` | S | Feed `Store.currency` / `country` / `time_zone` (D1) |
| `match_shop_settings` | **R** | Writes System Settings (`sync/shop_settings.py:73-113`). Can't work with mixed stores (D1) |
| `send_order_confirmation` | S | |
| `company_name`, `company_address`, `company_state`, `company_gstin`, `company_phone`, `company_email` | S | Invoice details. `company_email` becomes the reply-to (D3) |
| `enable_commission` | S | Read by raw SQL on `tabSingles` (`commission/__init__.py:14`); rewrite |
| `failed_log_retention_days` | V | Log clean-up stays site-wide (one value for all) |
| `retailer_inactive_anonymise_days` | S | A merchant's data-retention choice |
| `plan_limits` | V | App-wide plan → limit map (`shopify/billing.py:13`) |

## 3. Store Connection uses, by file

| File | Lines | Class | What changes |
|---|---|---|---|
| `connectors/shopify.py` | 29 | S | Connector reads and writes tokens/settings on the given Store. `TOKEN_LOCK_KEY` (`:57`) gets the shop added. Token/metafield writes at `:468, 1094-1139, 1207, 1703-1737` all target the Store row. `:1732` Singles-table datetime read is no longer needed |
| `connectors/__init__.py` | 3 | S | `get_connector(require_enabled)` → `get_connector(store=None, require_enabled=True)`; defaults to the current store |
| `connectors/base.py` | 4 | D | Docstrings; `validate_settings(settings)` gets a Store |
| `api/__init__.py` | 10 | S | `:87` settings save, `:132` default location, `:577` company name for print, 5× `get_connector()` fail-fast |
| `sync/shop_settings.py` | 9 | S + R | Saving shop currency/zone/country stays (on Store). Everything that writes System Settings / Global Defaults (`:47-113`, `:144`) is removed |
| `shopify/merchant_settings.py` | 9 | S | Read/write the current store; `:95` System Manager shortcut becomes Store Admin |
| `sync/stock.py` | 7 | S | Sell-from locations come from the Store child table (`:34-60, 173, 200`) |
| `sync/orders.py` | 7 | S | `platform`/`enabled` from the order's store; `:267` `get_cached_doc` cache is per store |
| `shopify/embedded.py` | 7 | S | `:55` connected check, `:168/193` embedded user, `:225/285` page context, all for the session token's shop |
| `shopify/oauth.py` | 6 | S | `:152-250`: install creates or reactivates a Store for the shop instead of overwriting the single record |
| `shopify/billing.py` | 6 | S + V | `:108, 128, 159, 186` per store; `:116` `plan_limits` vendor-wide |
| `sync/products.py` | 5 | S | Scheduled sync loops stores (`:62`) |
| `shopify/session_token.py` | 5 | S | `:141` returns the Store for the token's shop. The "token for another shop is refused" rule (`:40`) becomes "shop must have an active Store" |
| `field_sales/doctype/field_order/field_order.py` | 4 | S | Cancel check and location default use the order's store |
| `utils/anonymise.py` | 3 | S | Job loops stores; days and platform per store |
| `shopify/webhooks.py` | 3 | S | App webhooks resolve the Store from the shop header |
| `field_sales/doctype/sync_log/sync_log.py` | 3 | V | Retention stays site-wide; `:90` comment |
| `field_sales/doctype/retailer/retailer.py` | 3 | S | `:98` enabled, `:200` country default from the retailer's store |
| `hooks.py` | 3 | D | Comments |
| `sync/confirmation.py` | 2 | S | Send flag and company/shop name from the order's store |
| `payments/razorpay.py` | 2 | S | `:316` OAuth-mode check for the payment's store |
| `frontend_api.py` | 2 | S | `:380` locations/order mode, `:653` boot connection state |
| `commission/__init__.py` | 2 | S | Raw `tabSingles` SQL becomes a Store field read |
| `push/messages.py`, `commission/engine.py`, `sync/webhooks.py` | 1 each | S / D | Platform label from the record's store |
| `field_sales/doctype/sales_rep/sales_rep.py` | 1 | S | Embedded-user check against the rep's store |
| `shopify/privacy.py` | 1 | S | `shop/redact` disables only that Store |
| `patches/v1_0/*` (6 files) | 14 | P | Already ran; Phase 2's migration patch runs after them |
| Store Connection doctype module | — | S | `validate`, `on_update`, `_validate_locations`, `_clear_token_on_credential_change`, `_normalize_store_url` move to Store |

**Frontend** (`frontend/src`): `components/StoreConnectionForm.vue`, `InvoiceSettings.vue`, `CommissionSettings.vue`, `pages/Settings.vue`, `SyncLogs.vue`, `Retailers.vue`, `shopify/merchantSettings.js`, `composables/useSellLocations.js`, `router.js` all switch to the current Store (Phase 7).

## 4. Other single settings

| Settings | Reads | Class | Note |
|---|---|---|---|
| Razorpay Settings | 7 (`payments/razorpay.py:95, 444, 685`; `frontend_api.py:561, 623`) | **S** | Each merchant has their own Razorpay account. Move `key_id`, `key_secret`, `webhook_secret`, `mode`, `link_expiry_hours`, `notify_*` to the Store (or a Store-linked doc). The guest webhook (`payments/razorpay.py:526`) must find the store **before** checking the signature, e.g. `?store=<code>` in the webhook URL (each store gets its own URL), then verify with that store's secret |
| Mobile Push Settings | 2 (`push/fcm.py:62`) | V | One FCM project for the one mobile app |
| System Settings `time_zone`, `currency` | 4 (`sync/shop_settings.py`) | R | Writes removed (D1) |
| System Settings `country` | 1 (`retailer.py:205`) | S | Fallback becomes `Store.country` |
| Global Defaults `default_currency` | 1 (`sync/shop_settings.py:79`) | R | |
| `get_system_timezone()` / `site_currency()` | 16 in 8 files | S | Becomes `store_timezone()` / `store_currency()` |

## 5. Naming and uniqueness

| DocType | Today | Multi-store rule |
|---|---|---|
| Field Order | `FO-.YYYY.-.#####`; unique `client_temp_key`, `invoice_no` | `FO-{code}-.YYYY.-.#####` (D4). Unique (`store`, `client_temp_key`), (`store`, `invoice_no`) |
| Invoice no. | `INV-.YYYY.-.#####` (`sync/confirmation.py:19`), one site series | `{code}/{YY}/#####`, per store; at most 16 chars, as GST requires |
| Visit | `VIS-.YYYY.-.#####`; unique `client_temp_key` | Shared series; unique (`store`, `client_temp_key`) |
| Retailer | `RET-.#####`; unique `user` | Shared series; `user` stays unique (one login → one store) |
| Sales Rep | `field:user` | Unchanged; the user belongs to one store |
| Commission Plan | `field:plan_name`, unique | Hash or series name + `plan_name` title, unique per (`store`, `plan_name`); the Link field label shows the title |
| Commission Entry | `CE-.YYYY.-.#####`; unique `dedupe_key` | Shared series; `dedupe_key` includes the store |
| Commission Statement | `CS-.YYYY.-.MM.-.#####` | Shared series |
| Platform Product | `{platform}-{external_variant_id}`; unique `external_variant_id` | Shopify variant ids are globally unique, so this is safe for Shopify. For D2 later, name `{store}-{platform}-{variant}` and unique (`store`, `external_variant_id`) |
| Product Stock | `{product}-{location_id}` | Safe once Platform Product is store-scoped |
| Payment Transaction | hash; unique `transaction_key` | Key includes the store, or stays a gateway-unique id; check in Phase 2 |
| Razorpay Webhook Event | hash; unique `event_id` | Razorpay event ids are global; safe |
| Mobile Device | hash; unique `fcm_token` | Safe (device token) |
| Privacy Request | `PRIV-{#####}` | Shared series + `store` link |
| Sync Log | hash | + `store` link |

## 6. Cross-store leaks found beyond the PDF

| Where | Issue |
|---|---|
| `permissions.py:128 _query_conditions` | `is_admin()` (System Manager) sees everything, so merchants can't keep System Manager |
| Role constants | `ADMIN_ROLES` / `MANAGER_ROLES` / `MONITOR_ROLES` are defined separately in 9 files (`permissions.py:27`, `sync/notify.py:12`, `api/dashboard.py:18`, `commission/statements.py:27`, `shopify/embedded.py:42`, `shopify/merchant_settings.py:27`, commission entry/statement modules). Store Admin must replace System Manager in all of them, so gather them into `permissions.py` first. (A Sales Manager with no own rep gets `1=0`, sees nothing: safe) |
| `sync/notify.py:36 users_with_roles()` | Role lookup over the **whole site**. Used by low-stock alerts (`push/events.py:75`), payment notices (`payments/razorpay.py:866`), sync-failure alerts (`sync/notify.py:27, 90`) and privacy notices (`shopify/privacy.py:270`). Every merchant's managers would get every store's alerts. Needs a `store` filter |
| `daily_summary.py:72, 84` | Recipients and totals queried site-wide |
| Redis keys | `field_sales:shopify_token_refresh` (one lock for every store), `field_sales:low_stock_notified:{name}` (safe, name is unique), `field_sales:webhook:{event_id}` / `shopify_app_webhook:{id}` (Shopify ids are global; safe), `field_sales:shopify_shop_id:{shop}` (safe). Only the token lock **must** change |
| Job ids | `field_sales_shop_settings_sync` (`store_connection.py:53`), `field_sales_shopify_register_webhooks`, `field_sales_shopify_setup_new_store` (`shopify/oauth.py:274, 282`) are site-wide: two installs at once would drop one. Add the store. Per-document ids (`push_order:{name}` etc.) are safe |
| `frappe.qb` queries (60 in 11 files) | Run without permission checks; every one gets an explicit `store` condition (Phase 3 audit) |

## 7. Scheduled jobs

| Job | Multi-store behaviour |
|---|---|
| `sync_log.clear_old_logs` | Site-wide (V) |
| `shop_settings.scheduled_sync` | Loop active stores; save shop zone/currency on each Store only |
| `anonymise.anonymise_inactive_retailers` | Loop stores; per-store days |
| `razorpay.reconcile_open_links` | Loop stores with Razorpay on; each uses its own keys |
| `orders.retry_store_payments` | Loop stores |
| `products.scheduled_sync` | Loop stores; per-store frequency and job id `product_sync:{store}`; stagger start times |
| `orders.link_completed_drafts` | Loop stores |
| `orders.retry_failed_orders` | Loop stores |
| `daily_summary.send_daily_summary` (`0 20 * * *`) | **Must run hourly** and send for stores where it is 20:00 in their time zone (D1) |
| `statements.generate_monthly_statements` (`0 2 1 * *`) | Run hourly on day 1–2; generate for stores whose local month has ended (D1) |

## 8. Whitelisted endpoints and how each stays inside the user's store

All `@frappe.whitelist` functions (Phase 3 audit, 108; two added in Phase 7, three with the admin onboarding). `tests/test_store_isolation.py::TestEndpointAudit` fails when a function is added or removed without updating this list.

Mechanisms: **doc permission** = `check_permission` / `has_permission` (the store + hierarchy hooks in `permissions.py`); **get_list** = `frappe.get_list` (permission query hooks add the store); **scope_criterion** / **hierarchy+store** = `permissions.py` helpers that include the store; **keyset store clause** = `api/mobile.py _execute_keyset_query`; **store filter** = `store_filters()`; **current store** = the user's own store through `utils/store.py`; **role gate** = `only_for` / role check.

| Endpoint | Guest | Stays in the store by |
|---|---|---|
| `field_sales.api.test_connection` |  | role gate; acts on the user's own store |
| `field_sales.api.get_locations` |  | role gate; acts on the user's own store |
| `field_sales.api.get_sell_locations` |  | doc permission |
| `field_sales.api.enqueue_product_sync` |  | role gate; syncs the user's own store (job id per store) |
| `field_sales.api.enqueue_customer_import` |  | role gate; imports into the user's own store (`enqueue_in_store`, job id per store) |
| `field_sales.api.create_order` |  | doc permission |
| `field_sales.api.set_order_location` |  | saves the order: permission hooks (store + rep) |
| `field_sales.api.get_user_for_sales_rep` |  | doc permission, store user check |
| `field_sales.api.create_sales_rep` |  | doc permission |
| `field_sales.api.set_sales_rep_password` |  | doc permission |
| `field_sales.api.send_sales_rep_password_reset` |  | doc permission |
| `field_sales.api.get_retailer_login` |  | doc permission |
| `field_sales.api.create_retailer_login` |  | doc permission |
| `field_sales.api.send_retailer_password_reset` |  | doc permission |
| `field_sales.api.revoke_retailer_login` |  | doc permission |
| `field_sales.api.sync_retailers` |  | current store, doc permission |
| `field_sales.api.check_in` |  | only the visit's own rep; a rep belongs to one store |
| `field_sales.api.check_out` |  | `check_permission(write)` on the visit (store + rep) + rep/manager gate |
| `field_sales.api.get_live_stock` |  | doc permission |
| `field_sales.api.refresh_stock` |  | doc permission |
| `field_sales.api.retry_sync` |  | current store, doc permission |
| `field_sales.api.retry_sync_orders` |  | current store, doc permission |
| `field_sales.api.get_store_order_url` |  | current store, doc permission |
| `field_sales.api.get_sync_failures` |  | get_list (hooks), hierarchy+store, role gate |
| `field_sales.api.retry_all_sync_failures` |  | current store, get_list (hooks), hierarchy+store, role gate |
| `field_sales.api.get_sync_failure_count` |  | get_list (hooks), hierarchy+store |
| `field_sales.api.get_territories` |  | doc permission, get_list (hooks) |
| `field_sales.api.get_retailer_cities` |  | doc permission, get_list (hooks) |
| `field_sales.api.get_retailer_summary` |  | doc permission, scope_criterion |
| `field_sales.api.get_visit_orders` |  | doc permission, get_list (hooks) |
| `field_sales.api.mobile_login` | yes | guest; signs in one user, returns only their own data |
| `field_sales.api.dashboard.get_dashboard` |  | get_list (hooks) |
| `field_sales.api.mobile.get_orders_list` |  | hierarchy, keyset store clause, staff gate |
| `field_sales.api.mobile.get_visits_list` |  | hierarchy, keyset store clause, staff gate |
| `field_sales.api.mobile.get_customers_list` |  | hierarchy, keyset store clause, staff gate |
| `field_sales.api.mobile.get_products_list` |  | doc permission, get_list (hooks), keyset store clause, staff gate |
| `field_sales.api.mobile.get_team_list` |  | keyset store clause, staff gate |
| `field_sales.api.mobile.get_commission_statements_list` |  | hierarchy, keyset store clause, staff gate |
| `field_sales.api.mobile.get_retailer_orders_list` |  | keyset store clause + the user's own retailer |
| `field_sales.api.mobile.get_retailer_payments_list` |  | keyset store clause, staff gate |
| `field_sales.api.mobile.get_order_detail` |  | doc permission, get_list (hooks), staff gate |
| `field_sales.api.mobile.get_customer_detail_records` |  | doc permission, staff gate |
| `field_sales.api.mobile.get_visit_detail` |  | doc permission, staff gate |
| `field_sales.api.mobile.get_product_detail_stock` |  | doc permission, get_list (hooks), staff gate |
| `field_sales.api.mobile.get_product_sync_status` |  | role gate, staff gate, store filter |
| `field_sales.api.mobile.get_mobile_count` |  | get_list (hooks), staff gate |
| `field_sales.api.mobile.mobile_search` |  | doc permission, staff gate |
| `field_sales.api.mobile.find_order_by_client_temp_id` |  | get_list (hooks), staff gate |
| `field_sales.api.mobile.find_visit_by_client_temp_id` |  | get_list (hooks), staff gate |
| `field_sales.api.mobile.get_mobile_manifest` | yes | guest; static contract, no store data |
| `field_sales.api.mobile.register_device` |  | the session user's own device row |
| `field_sales.api.mobile.unregister_device` |  | the session user's own device row |
| `field_sales.api.mobile.set_device_push_enabled` |  | the session user's own device row |
| `field_sales.api.webhooks.receive` | yes | guest webhook: the shop header's Store (`shop_store`), HMAC checked with its keys |
| `field_sales.chatter.get_chatter` |  | `_readable()`: `check_permission(read)` on the record |
| `field_sales.chatter.post_note` |  | `_readable()`: `check_permission(read)` on the record |
| `field_sales.commission.api.get_my_commission_summary` |  | the session user's own rep |
| `field_sales.commission.api.get_team_commission` |  | doc permission, get_list (hooks), hierarchy, staff gate |
| `field_sales.commission.api.get_commission_entries` |  | doc permission, get_list (hooks), staff gate |
| `field_sales.commission.api.get_commission_statements` |  | doc permission, get_list (hooks), staff gate |
| `field_sales.commission.api.get_commission_statement` |  | doc permission |
| `field_sales.commission.api.approve_statement` |  | `check_permission(write)` on the statement (store + rep) |
| `field_sales.commission.api.get_commission_plans` |  | doc permission, get_list (hooks) |
| `field_sales.commission.api.save_commission_plan` |  | doc permission |
| `field_sales.commission.api.assign_commission_plan` |  | doc permission |
| `field_sales.commission.api.create_adjustment` |  | `has_permission` on the rep; the entry is stamped with the rep's store |
| `field_sales.commission.api.generate_statements` |  | get_list (hooks), hierarchy |
| `field_sales.commission.statements.regenerate_statement` |  | doc permission |
| `field_sales.commission.statements.mark_paid` |  | doc permission |
| `field_sales.field_sales.doctype.mobile_push_settings.mobile_push_settings.get_push_status` |  | vendor only (System Manager) |
| `field_sales.field_sales.doctype.mobile_push_settings.mobile_push_settings.users_with_phones` |  | get_list (hooks), role gate |
| `field_sales.field_sales.doctype.mobile_push_settings.mobile_push_settings.send_test_notification` |  | vendor only (System Manager) |
| `field_sales.field_sales.doctype.retailer.retailer.get_default_country` |  | current store, doc permission |
| `field_sales.field_sales.doctype.retailer.retailer.get_phone_hint` |  | doc permission |
| `field_sales.frontend_api.get_list_preset` |  | doc permission, get_list (hooks), staff gate |
| `field_sales.frontend_api.get_session` |  | the session user's own roles, rep and store |
| `field_sales.frontend_api.get_vendor_stores` |  | vendor staff on the shared site only (`_require_vendor`); lists Stores, no records |
| `field_sales.frontend_api.switch_store` |  | vendor staff on the shared site only; sets this session's store (`set_vendor_store`) and comments on the Store |
| `field_sales.shopify.store_admin.set_store_admin` |  | the current store's Store Admin (`store_scope()` == the store) or vendor staff, shared site only; the email may not be vendor staff or another store's user (`can_be_embedded_user`) |
| `field_sales.shopify.store_admin.resend_admin_invite` |  | as `set_store_admin`; mails the store's own admin |
| `field_sales.shopify.store_admin.update_password` | yes | Frappe's `update_password` (override); only adds a sign-out and the login page for a store's user after a reset link |
| `field_sales.frontend_api.get_sidebar_counts` |  | counts through `get_list` (store + hierarchy hooks) |
| `field_sales.frontend_api.get_notifications` |  | the session user's own Notification Log rows |
| `field_sales.frontend_api.search` |  | get_list (hooks), staff gate |
| `field_sales.frontend_api.get_dashboard` |  | doc permission, get_list (hooks), staff gate |
| `field_sales.frontend_api.get_product_activity` |  | doc permission, get_list (hooks), staff gate |
| `field_sales.frontend_api.get_order_sync_info` |  | current store, doc permission, staff gate |
| `field_sales.frontend_api.get_rep_month_stats` |  | doc permission, hierarchy+store, staff gate |
| `field_sales.frontend_api.get_order_payment_info` |  | doc permission, staff gate |
| `field_sales.payments.manual.mark_order_paid` |  | doc permission |
| `field_sales.payments.razorpay.get_webhook_url` |  | Razorpay Settings: single-store sites only (off on the shared site) |
| `field_sales.payments.razorpay.test_connection` |  | Razorpay Settings: single-store sites only (off on the shared site) |
| `field_sales.payments.razorpay.create_link_for_order` |  | doc permission |
| `field_sales.payments.razorpay.get_payment_config` |  | doc permission, staff gate |
| `field_sales.payments.razorpay.webhook` | yes | guest; refused on the shared site (Razorpay off); single-store sites only |
| `field_sales.payments.razorpay.refresh_payment_status` |  | doc permission |
| `field_sales.payments.refunds.refund_order_payment` |  | doc permission, role gate |
| `field_sales.shopify.billing.get_plan_info` |  | the user's own store (usage counted per store) |
| `field_sales.shopify.embedded.get_store_status` |  | the user's own store |
| `field_sales.shopify.merchant_settings.get_store_settings` |  | the user's own store |
| `field_sales.shopify.merchant_settings.save_store_settings` |  | the user's own store |
| `field_sales.shopify.merchant_settings.fetch_locations` |  | the user's own store |
| `field_sales.shopify.merchant_settings.get_email_accounts` |  | refused on the shared site (`_refuse_on_shared_site`; mail goes out in the store's name, D3); single-store: merchant's own accounts only |
| `field_sales.shopify.merchant_settings.save_email_account` |  | refused on the shared site (`_refuse_on_shared_site`; mail goes out in the store's name, D3); single-store: merchant's own accounts only |
| `field_sales.shopify.oauth.install` | yes | guest; install of a shop: Phase 6 (a Store per shop) |
| `field_sales.shopify.oauth.callback` | yes | guest; install of a shop: Phase 6 (a Store per shop) |
| `field_sales.shopify.oauth.reset_connection` |  | vendor role gate; acts on the current store |
| `field_sales.shopify.webhooks.receive` | yes | guest webhook: the shop header's Store (`shop_store`), HMAC checked with its keys |
| `field_sales.sync.confirmation.resend_order_confirmation` |  | doc permission |
| `field_sales.visit_import.download_visit_template` |  | reps and customers through `get_list` (store + hierarchy hooks) |
| `field_sales.visit_import.preview_visit_import` |  | reps/customers through `get_list`; each visit re-checked on create |
| `field_sales.visit_import.create_visits_from_import` |  | Visit insert: permission hooks (rep's store + hierarchy) |
| `field_sales.www.fieldsales.get_context_for_dev` | yes | guest; dev helper, returns the page boot only |
