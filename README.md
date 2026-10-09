### Field Sales

Field Sales is a Frappe app that lets a brand’s sales reps check in at retail shops with GPS, take orders against live Shopify stock, and sync those orders to Shopify automatically. Managers can track visits, orders and sync status from one dashboard.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app field_sales
```

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
