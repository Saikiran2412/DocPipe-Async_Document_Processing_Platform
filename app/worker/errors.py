from botocore.exceptions import EndpointConnectionError
from sqlalchemy.exc import OperationalError



class TransientError(Exception):
    """Temporary problem; retrying may succeed."""


class PermanentError(Exception):
    """Retrying cannot help (bad input, corrupt file)."""


from botocore.exceptions import (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

TRANSIENT_TYPES = (
    TransientError,
    ConnectionError,
    TimeoutError,
    EndpointConnectionError,
    ConnectionClosedError,
    ConnectTimeoutError,
    ReadTimeoutError,
    OperationalError,
)




def is_transient(exc: Exception) -> bool:
    return isinstance(exc, TRANSIENT_TYPES)