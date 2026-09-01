CONTENT_TYPE = "application/json"
CONTENT_TYPE_HEADERS = {
    "Content-Type": CONTENT_TYPE,
}
API_VERSION = "v3_0"

# Ceiling for the exponential backoff between polls (see client.py /
# response_handling.py): without it, later polling steps sleep for minutes
# (polling_interval * 2**step), dozing far past the moment a result becomes
# available - and taking just as long to conclude that it never will.
MAX_POLLING_SLEEP = 10.0  # seconds
