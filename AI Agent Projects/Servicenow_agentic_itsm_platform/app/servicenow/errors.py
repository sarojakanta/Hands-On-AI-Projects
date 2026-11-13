class ServiceNowError(Exception):
    """Base class. Messages never contain credentials."""

    retriable = False


class ServiceNowConfigError(ServiceNowError):
    pass


class ServiceNowAuthError(ServiceNowError):
    pass


class ServiceNowTimeoutError(ServiceNowError):
    retriable = True


class ServiceNowUnavailableError(ServiceNowError):
    retriable = True


class ServiceNowRateLimitError(ServiceNowUnavailableError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ServiceNowResponseError(ServiceNowError):
    """The response was not in the expected ServiceNow Table API shape."""
