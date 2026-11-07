from uuid import uuid4


class DomainError(Exception):
    def __init__(
        self, code: str, message: str = "Operation could not be completed", status: int = 409
    ):
        self.code, self.message, self.status = code, message, status
        self.correlation_id = str(uuid4())
        super().__init__(message)

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, "correlation_id": self.correlation_id}
