# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Shopify App Store pieces: OAuth install (``oauth.py``), app lifecycle and privacy
compliance webhooks (``webhooks.py``, ``privacy.py``) and the app's site_config
settings (``config.py``). Core code never imports this package; it reaches the
store only through ``field_sales.connectors.get_connector()``.
"""
