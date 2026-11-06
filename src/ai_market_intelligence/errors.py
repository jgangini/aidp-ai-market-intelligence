"""OCI errors extracted from the frozen installer contract."""
import re

class AidpProvisionPending(Exception):
    def __init__(self, message: str, phase: str = "content") -> None:
        super().__init__(message)
        self.phase = phase

class AidpProvisionError(Exception):
    pass

class AidpRequestError(AidpProvisionError):
    """Transport metadata without request bodies, response bodies or credentials."""

    def __init__(self, message: str, *, method: str, path: str, phase: str,
                 status_code: int, request_id: str) -> None:
        super().__init__(message)
        self.diagnostic = {
            "method": method.upper(), "phase": phase, "status_code": status_code,
            "endpoint": re.sub(r"(/workspaces/)[^/]+", r"\1{workspace}", path.split("?", 1)[0])[:240],
            "request_id": request_id if re.fullmatch(r"[A-Za-z0-9:/_.=-]{1,256}", request_id) else "",
        }

class AidpProvisionConflict(Exception):
    pass
