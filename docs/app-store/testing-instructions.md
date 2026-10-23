# Testing instructions (submission form)

## Setup
1. Install Field Sales on the review store from the listing (Shopify-managed installation).
   The app opens inside the admin and connects the store; no extra account is needed for the
   embedded manager screens.
2. Wait about a minute for the first product and stock sync (Settings shows the connected shop,
   granted scopes and locations).
3. Demo data is already loaded on our demo site [TO FILL: demo site URL] for the development
   store [TO FILL: demo store URL].

## Logins for the rep app (outside the Shopify admin)
The rep mobile app is at `https://<site>/fieldsales` and uses its own sign-in:

| Role | Email | Password |
|---|---|---|
| Sales manager | demo.manager@example.com | [TO FILL: from site_config field_sales_demo_passwords] |
| Sales rep | demo.rep@example.com | [TO FILL: from site_config field_sales_demo_passwords] |

GPS check-in needs location permission in the browser and works only on the visit's date.

## Test cases and expected results

| # | Steps | Expected result |
|---|---|---|
| 1 | Open Field Sales from the admin sidebar | The dashboard loads inside the admin without any sign-in screen; menu items appear in the admin sidebar |
| 2 | Reload the admin page; repeat in a Chrome incognito window | The app loads again on the same page; no redirect out of the admin |
| 3 | Settings | Shows the connected shop, granted scopes, locations, order mode, Help and Privacy links |
| 4 | Sales Reps → New Sales Rep with your email | The rep is created and a welcome email with a password link is sent |
| 5 | Sign in as the demo rep at `/fieldsales` on a phone → today's planned visit → Check In | The visit becomes In Progress with the check-in time and distance to the shop |
| 6 | On the visit: New Order → add products → Submit | Live stock is shown; the order is submitted and shows "Synced" within a minute |
| 7 | In the Shopify admin, open Orders / Draft orders | The order is there with the retailer as customer and the Field Sales order number |
| 8 | Set a product's stock to 0 in the store, order it in Field Sales, submit | Submission is refused with an out-of-stock message (live stock check) |
| 9 | Sync Failures | Any failed sync shows the store's message; Retry sends it again |
| 10 | Commission | The demo rep's commission entries and statements are listed |
| 11 | Uninstall the app | Access is removed at once; the app no longer syncs; Shopify's shop/redact request later anonymises the data |

Privacy webhooks (customers/data_request, customers/redact, shop/redact) are answered at
`https://<site>/api/method/field_sales.shopify.webhooks.receive` with HMAC verification (401 on a
bad signature).
