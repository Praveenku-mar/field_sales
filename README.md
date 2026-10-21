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
