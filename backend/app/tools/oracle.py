import asyncio

import oracledb
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlglot import exp, parse

from ..config import Settings
from .common import ToolAccessError, json_result, observed_tool_call, tool_error_result


class OracleQueryInput(BaseModel):
    query: str = Field(
        min_length=6,
        max_length=20_000,
        description="One read-only Oracle SELECT or WITH query. No DML, DDL, or PL/SQL.",
    )
    parameters: dict[str, str | int | float | None] = Field(
        default_factory=dict,
        description="Optional Oracle bind parameters without the leading colon.",
    )
    max_rows: int = Field(
        default=100,
        ge=1,
        le=5000,
        description="Maximum number of rows to return.",
    )


class OracleDiagnosticService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def validate_read_only(query: str) -> None:
        try:
            statements = parse(query, read="oracle")
        except Exception as exc:
            raise ToolAccessError(f"Oracle SQL could not be parsed: {exc}") from exc
        if len(statements) != 1:
            raise ToolAccessError("Exactly one SQL statement is allowed.")
        statement = statements[0]
        if not isinstance(statement, exp.Query):
            raise ToolAccessError("Only SELECT or WITH queries are allowed.")
        blocked_nodes = (
            exp.Alter,
            exp.Command,
            exp.Create,
            exp.Delete,
            exp.Drop,
            exp.Insert,
            exp.Into,
            exp.Merge,
            exp.Update,
        )
        for blocked in blocked_nodes:
            if statement.find(blocked):
                raise ToolAccessError(f"SQL node '{blocked.__name__}' is not allowed.")

    async def query(
        self,
        query: str,
        parameters: dict[str, str | int | float | None] | None = None,
        max_rows: int = 100,
    ) -> str:
        if not self.settings.oracle_configured:
            raise ToolAccessError("Oracle diagnostics are not configured.")
        self.validate_read_only(query)
        row_limit = min(max_rows, self.settings.oracle_max_rows)
        arguments = {
            "query": query,
            "parameters": parameters or {},
            "max_rows": row_limit,
        }

        def execute_sync() -> str:
            with oracledb.connect(
                user=self.settings.oracle_user,
                password=self.settings.oracle_password,
                dsn=self.settings.oracle_dsn,
            ) as connection:
                connection.call_timeout = int(
                    self.settings.diagnostics_tool_timeout_seconds * 1000
                )
                connection.module = "Local Agent Studio"
                connection.action = "read-only diagnostics"
                with connection.cursor() as cursor:
                    cursor.execute(query, parameters or {})
                    columns = [column.name for column in cursor.description or []]
                    rows = cursor.fetchmany(row_limit + 1)
                    truncated = len(rows) > row_limit
                    returned = rows[:row_limit]
                    return json_result(
                        columns=columns,
                        rows=[
                            {
                                columns[index]: value
                                for index, value in enumerate(row)
                            }
                            for row in returned
                        ],
                        row_count=len(returned),
                        truncated=truncated,
                        max_rows=row_limit,
                    )

        async def execute() -> str:
            return await asyncio.wait_for(
                asyncio.to_thread(execute_sync),
                timeout=self.settings.diagnostics_tool_timeout_seconds + 1,
            )

        return await observed_tool_call(
            "oracle_select",
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    def as_tool(self) -> StructuredTool:
        return StructuredTool.from_function(
            coroutine=self.query,
            name="oracle_select",
            description=(
                "Run one bounded, read-only SELECT or WITH query against the configured Oracle "
                "database. Use bind parameters for values. DML, DDL, PL/SQL, SELECT INTO, and "
                "multiple statements are rejected. Results are capped."
            ),
            args_schema=OracleQueryInput,
            handle_tool_error=tool_error_result,
        )
