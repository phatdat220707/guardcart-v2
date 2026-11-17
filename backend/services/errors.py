class ServiceError(Exception):
    """Structured application failure with a safe, English message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AuthenticationRequired(ServiceError):
    def __init__(self):
        super().__init__("authentication_required", "A valid session is required.")


class ResourceNotFound(ServiceError):
    def __init__(self):
        super().__init__("not_found", "Resource not found.")


class Conflict(ServiceError):
    pass


class OperationDenied(ServiceError):
    pass


class StorageBusy(ServiceError):
    def __init__(self):
        super().__init__("storage_busy", "Storage is busy; try the same request again later.")
