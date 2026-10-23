# Screenshot list

All shots use the demo data (`bench --site <site> field-sales-demo`, see testing-instructions.md).
Desktop: 1600x900 px (16:9), app UI inside the Shopify admin, browser chrome cropped. Mobile:
the rep app on a phone-sized viewport (e.g. 390x844), shown inside a 1600x900 frame if the form
requires 16:9. Shopify's rules: show the app's UI in at least one, no pricing, reviews or outcome
claims, no personal data (demo names and `@example.com` emails only), alt text for each.

## Desktop (6)

| # | Screen | What to show | Alt text |
|---|---|---|---|
| 1 | Dashboard (embedded) | Today's visits, orders this month, team table, Field Sales menu in the admin sidebar | Field Sales dashboard inside the Shopify admin with visit and order summaries |
| 2 | Orders list | Demo orders with sync status pills, one Draft and several Synced | List of field orders with their store sync status |
| 3 | Order detail | A synced order: items, live stock column, store order number, payment card | Field order with items, stock levels and the linked store order |
| 4 | Visits | Visit list over the last 30 days with Completed and Planned visits | Sales rep visits with check-in times and status |
| 5 | Sync Failures | One failed order with the store's message and the Retry button | Failed order sync with the store's error message and a retry button |
| 6 | Commission | Commission summary for the demo rep and the Demo Flat 2% plan | Sales rep commission summary by month |

## Mobile rep app (3)

| # | Screen | What to show | Alt text |
|---|---|---|---|
| 7 | Visit check-in | A planned visit for today with the Check In button and map | Rep checking in to a shop visit on a phone |
| 8 | New order | Order entry with product search, quantities and stock warnings | Rep taking an order with live stock on a phone |
| 9 | Customers | Retailer list with search | Rep's list of retail customers on a phone |

## Before taking them
- Run the demo command, then make one order fail on purpose for shot 5 (e.g. set a product to
  out of stock in the dev store, submit an order for it, then put the stock back after the shot).
- Hide the browser's bookmarks bar; use a clean dev store name.
