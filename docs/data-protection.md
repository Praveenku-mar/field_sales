# Field Sales: protected customer data controls

Answers for the Partner Dashboard → API access requests → Protected customer data access →
Data protection details. Field Sales uses Level 2 data (customer name, email, phone, address).

Each control says whether it is covered **by code** (file → function), **by hosting** or **by a
process**. Every `[TO FILL]` must be completed before submitting.

Note: Shopify lists "encrypt data at rest and in transit" under the Level 1 requirements; Level 2
adds encrypted backups, separate test/production data, data loss prevention, staff access limits,
strong passwords, access logs and incident response. Both are covered below.

## Level 2 controls

### 1. Encryption in transit
- **Hosting:** the site is served only over HTTPS. [TO FILL: TLS termination (Frappe Cloud / own nginx), minimum TLS version, HTTP→HTTPS redirect.]
- **Code:** every call to Shopify and Razorpay uses HTTPS (`connectors/shopify.py` → `ShopifyConnector._graphql()`, token URLs; `payments/razorpay.py` → `request()`); webhooks are verified with HMAC (`shopify/webhooks.py` → `receive()`, `api/webhooks.py` → `receive()`).

### 2. Encryption at rest
- **Code:** store tokens and secrets are Password fields, encrypted with the site's encryption key (`connectors/shopify.py` → `save_oauth_tokens()`, `set_encrypted_password`; Razorpay Settings keys). The app's Shopify client secret is kept in site config, not the database (`shopify/config.py`).
- **Hosting:** database and file storage encryption. [TO FILL: disk/volume encryption of the database server and file storage at the hosting provider.]

### 3. Encrypted backups
- **Hosting:** [TO FILL: backup schedule, where backups are stored, and how they are encrypted — e.g. provider-managed encrypted backups, or Frappe's backup encryption with `backup_encryption_key` in site config. Backup retention period.]

### 4. Test and production data kept separate
- **Code / process:** automated tests run only on a dedicated test site (`allow_tests`), never on production (ARCHITECTURE.md → Commands). Development uses a Shopify development store. The demo-data command refuses to run on a site with real submitted orders unless developer mode is on (`commands.py` → `_check_allowed()`).
- **Process:** [TO FILL: rule that production databases are never copied to development or test, or only after anonymisation (`utils/anonymise.py` → `anonymise()`).]

### 5. Data loss prevention
- **Code:**
  - Technical logs mask secrets and customer personal data before saving (`field_sales/doctype/sync_log/sync_log.py` → `_mask_payload()`, `_SENSITIVE_KEYS`, `_PERSONAL_KEYS`).
  - Retention: successful logs 30 days, failed logs `failed_log_retention_days` (default 180) (`sync_log.py` → `clear_old_logs()`); optional anonymisation of inactive retailers (`utils/anonymise.py` → `anonymise_inactive_retailers()`, Store Connection `retailer_inactive_anonymise_days`).
  - Personal-data exports are private files and are recorded in the access log (`shopify/privacy.py` → `data_request()`, `visit_import.py` → `download_visit_template()`).
  - Shopify redaction requests are handled (`shopify/privacy.py` → `customer_redact()`, `shop_redact()`).
- **Process:** [TO FILL: staff device policy (disk encryption, screen lock), no personal data in email/chat/tickets, how exports sent to merchants are deleted afterwards.]

### 6. Limited staff access
- **Code (merchant's users):** role-based access — Field Sales Rep, Sales Manager, System Manager; reps see only their own and their team's records (`permissions.py` → `get_allowed_reps()`, `*_has_permission()`); Store Connection is System Manager only; Privacy Requests are System Manager (Sales Manager read). Users opening the app from the Shopify admin act as one configured user (`shopify/embedded.py` → `get_embedded_user()`).
- **Process (our staff):** [TO FILL: who on our team can access production sites and the hosting dashboard, how access is granted, reviewed and removed, and whether access to a merchant's data needs their request.]

### 7. Strong passwords
- **Configuration:** [TO FILL: enable on every production site — System Settings → Enable Password Policy with minimum password score 3 (or higher), and two-factor authentication for System Manager and Sales Manager roles. Our own hosting/Partner accounts: password manager and 2FA required.]
- Note: reps set their own password through Frappe's reset link (`api/__init__.py` → `send_sales_rep_password_reset()`); Frappe enforces the site's password policy there.

### 8. Access log, reviewed regularly
- **Code:**
  - Frappe **Access Log** records personal-data exports (`visit_import.py` → `download_visit_template()`, `shopify/privacy.py` → `data_request()`), plus Frappe's own report and list exports.
  - Frappe **View Log** records who opened each Retailer and Field Order (`track_views` on both doctypes).
  - **Version** history records every change (`track_changes` on Retailer, Field Order, Visit).
  - Embedded requests record the Shopify staff id on the request (`shopify/session_token.py` → `authenticate()`, `frappe.local.shopify_staff_id`).
- **Process:** [TO FILL: who reviews Access Log and View Log, how often (e.g. monthly), and what triggers an investigation.]

### 9. Security incident response
- **Process:** [TO FILL: incident response policy — how incidents are detected and reported, who leads, timeline to notify affected merchants (and Shopify, where required), how secrets are rotated (Shopify client secret, Razorpay keys, site encryption key) and how the incident is recorded.]

## Level 1 summary
- **Minimum data:** only the fields needed for visits, orders and store sync are collected (see the privacy policy, `www/privacy.html`).
- **Inform merchants:** privacy policy at `/privacy`, help at `/help`.
- **Retention periods:** see control 5.
- **Privacy and data protection agreements with merchants:** [TO FILL: data processing agreement / terms merchants accept.]
- **Consent and opt-outs, automated decisions:** the app sends no marketing and makes no automated decisions about customers.
