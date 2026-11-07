"""A retrieval budget refreshed before every application database statement."""

from datetime import datetime

from app.shared.errors import DomainError


class QueryBudget:
    def __init__(self, deadline: datetime, clock, maximum_ms: int):
        self.deadline, self.clock, self.maximum_ms = deadline, clock, maximum_ms

    def remaining_ms(self) -> int:
        instant = self.clock()
        remaining = (self.deadline - instant).total_seconds()
        if remaining <= 0:
            raise DomainError("knowledge_deadline_exceeded", "Knowledge deadline exceeded")
        return max(1, min(int(remaining * 1000), self.maximum_ms))

    def prepare(self, connection) -> None:
        milliseconds = self.remaining_ms()
        connection.execute(
            "SELECT set_config('statement_timeout',%s,true),set_config('lock_timeout',%s,true)",
            (str(milliseconds), str(milliseconds)),
        )
        # Configuration itself may wait on the database. Do not begin application SQL afterward.
        self.remaining_ms()


class BudgetedCursor:
    def __init__(self, cursor, connection, budget: QueryBudget):
        self.cursor, self.connection, self.budget = cursor, connection, budget

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def execute(self, *args, **kwargs):
        self.budget.prepare(self.connection)
        return self.cursor.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class BudgetedConnection:
    """Delegate storage work while retaining the supplied connection and transaction."""

    def __init__(self, connection, budget: QueryBudget):
        self.connection, self.budget = connection, budget

    def execute(self, *args, **kwargs):
        self.budget.prepare(self.connection)
        return self.connection.execute(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        return BudgetedCursor(self.connection.cursor(*args, **kwargs), self.connection, self.budget)

    def __getattr__(self, name):
        return getattr(self.connection, name)
