DOMAIN = "quilt"

CONF_HOST = "host"

DEFAULT_HOST = "api.prod.quilt.cloud:443"

# How often we refresh the full HomeDatastore snapshot from Quilt.
# Real-time updates are handled via the Notifier stream, but polling remains
# as a fallback (and for ambient/state changes if notifications lag).
DEFAULT_POLL_INTERVAL_SECONDS = 10

# Quilt auth (Cognito User Pools)
# Derived from a mitmproxy capture of the Quilt iOS app login flow.
COGNITO_REGION = "us-west-2"
COGNITO_HOST = f"cognito-idp.{COGNITO_REGION}.amazonaws.com"
COGNITO_CLIENT_ID = "6lef74vtc8p7pgu47nmqubd9vn"
COGNITO_ISSUER = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_mP0zkCEzn"

CONF_EMAIL = "email"
CONF_ID_TOKEN = "id_token"
CONF_REFRESH_TOKEN = "refresh_token"

CONF_ACCEPT_TERMS = "accept_terms"

CONF_ENABLE_NOTIFIER = "enable_notifier"
DEFAULT_ENABLE_NOTIFIER = True

# Energy metrics polling (hourly buckets, derived from Quilt app behavior).
DEFAULT_ENERGY_LOOKBACK_DAYS = 7
DEFAULT_ENERGY_POLL_INTERVAL_SECONDS = 60 * 30
