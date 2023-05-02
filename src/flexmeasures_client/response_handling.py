from typing import Callable
from aiohttp import ContentTypeError


def check_response(self, response, reauth_step: int):
    """
    <300: passes
    401: reauthenticate
    todo: 503 + Retry-After header: poll again
    otherwise: call error_handler
    """
    status = response.status
    payload = response.json()
    headers = response.headers
    if status < 300:
        pass
    elif status == 401:
        self.get_access_token()
        reauth_step += 1
    elif status == 503 and "Retry-After" in headers:
        # todo: move the client_should_retry logic into this function)
        pass
    else:
        response.raise_for_status

def check_content_type(response):
    content_type = response.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        text = response.text()
        raise ContentTypeError(
            "Unexpected content type response from the API",
            {"Content-Type": content_type, "response": text},
        )

def check_for_status(status, expected_status):
    if status != expected_status:
        raise ValueError(
            f"Request failed with status code {status} and message: {response}"
        )