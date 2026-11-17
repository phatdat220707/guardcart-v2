from backend.services.errors import ServiceError


class AiUnavailable(ServiceError):
    def __init__(self, upstream_status: int | None = None):
        super().__init__("ai_unavailable", "The AI provider is unavailable. No purchase was authorized.")
        self.upstream_status = upstream_status


class AiProtocolError(ServiceError):
    def __init__(self):
        super().__init__("ai_protocol_error", "The AI response failed validation. No purchase was authorized.")


class AiQuotaExceeded(ServiceError):
    def __init__(self):
        super().__init__("ai_quota_exceeded", "The configured AI request quota has been reached.")


class AiBusy(ServiceError):
    def __init__(self):
        super().__init__("ai_busy", "An AI request is already running; wait for it to finish.")


class AiContextChanged(ServiceError):
    def __init__(self):
        super().__init__("ai_context_changed", "The mandate or catalogue changed. Start a fresh recommendation request.")
