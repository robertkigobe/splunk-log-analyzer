"""Log loading, query tools and the Claude tool-use loop for the Splunk Log Analyzer.

Claude never sees the raw log file. It answers by calling the tools below,
which run pandas queries over the full dataset and return small tables, so
every number in an answer is computed from the data rather than guessed.
"""

import os
from pathlib import Path

import anthropic
import pandas as pd

MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
MAX_TOOL_ROUNDS = 6
SAMPLE_PATH = Path(__file__).parent / "sample_data" / "springboot_error_logs.csv"

# Vendor-specific column names mapped to the canonical names the tools use.
COLUMN_ALIASES = {
    "level": "log_level",
    "severity": "log_level",
    "loglevel": "log_level",
    "errorType": "error_type",
    "exception": "error_type",
    "error": "error_type",
    "err_type": "error_type",
    "errorMessage": "message",
    "msg": "message",
    "log_message": "message",
    "app": "service",
    "application": "service",
    "service_name": "service",
    "statusCode": "status_code",
    "downstreamService": "downstream_service",
    "@timestamp": "timestamp",
    "_time": "timestamp",
}

_client = None


def _claude():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


# ── Loading ─────────────────────────────────────────────────────────────────

def normalize_schema(df: pd.DataFrame) -> pd.DataFrame:
    renames = {}
    for src, dst in COLUMN_ALIASES.items():
        if src in df.columns and dst not in df.columns and dst not in renames.values():
            renames[src] = dst
    df = df.rename(columns=renames)
    if "log_level" in df.columns:
        df["log_level"] = df["log_level"].astype(str).str.upper()
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    return df


def load_logs(path: str) -> pd.DataFrame:
    """Load a Splunk export (.csv / .jsonl / .json / .xlsx) and normalise its columns."""
    lower = path.lower()
    if lower.endswith(".csv"):
        df = pd.read_csv(path)
    elif lower.endswith((".jsonl", ".ndjson")):
        df = pd.read_json(path, lines=True)
    elif lower.endswith(".json"):
        # Splunk's JSON export is one object per line; fall back to a plain array.
        try:
            df = pd.read_json(path, lines=True)
        except ValueError:
            df = pd.read_json(path)
        if "result" in df.columns:  # {"result": {...}} rows from the REST/CLI export
            df = pd.json_normalize(df["result"])
    elif lower.endswith(".xlsx"):
        df = pd.read_excel(path)
    else:
        raise ValueError("Unsupported file type. Upload a .csv, .json, .jsonl or .xlsx export.")
    if df.empty:
        raise ValueError("The file has no rows.")
    return normalize_schema(df)


def load_sample() -> pd.DataFrame:
    return load_logs(str(SAMPLE_PATH))


# ── Query tools ─────────────────────────────────────────────────────────────

def _errors(df: pd.DataFrame) -> pd.DataFrame:
    """Only ERROR rows when the data has levels; otherwise every row is a candidate."""
    if "log_level" in df.columns:
        return df[df["log_level"] == "ERROR"]
    return df


def _apply_filters(df: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
    for column, value in (filters or {}).items():
        if column in df.columns:
            df = df[df[column].astype(str).str.lower() == str(value).lower()]
    return df


def dataset_profile(df: pd.DataFrame) -> str:
    lines = [
        f"Rows: {len(df):,}   Columns: {len(df.columns)}",
        f"Column names: {', '.join(df.columns)}",
    ]
    if "log_level" in df.columns:
        lines.append(f"Log levels: {df['log_level'].value_counts().to_dict()}")
    for column in ["service", "error_type", "environment", "downstream_service"]:
        if column in df.columns:
            values = sorted(df[column].dropna().astype(str).unique())
            shown = ", ".join(values[:30]) + (" ..." if len(values) > 30 else "")
            lines.append(f"{column} ({len(values)}): {shown}")
    if "timestamp" in df.columns and df["timestamp"].notna().any():
        lines.append(f"Date range: {df['timestamp'].min().date()} to {df['timestamp'].max().date()}")
    return "\n".join(lines)


def count_by(df: pd.DataFrame, columns: list[str], filters: dict | None = None, limit: int = 20):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        return f"Unknown column(s): {', '.join(missing)}. Available: {', '.join(df.columns)}"
    rows = _apply_filters(_errors(df), filters)
    return (
        rows.groupby(columns).size().reset_index(name="count")
        .sort_values("count", ascending=False).head(int(limit))
    )


def errors_over_time(df: pd.DataFrame, period: str = "month", filters: dict | None = None):
    if "timestamp" not in df.columns:
        return "This dataset has no timestamp column."
    rows = _apply_filters(_errors(df), filters).dropna(subset=["timestamp"])
    freq = {"day": "D", "week": "W", "month": "M"}.get(period, "M")
    bucket = rows["timestamp"].dt.tz_localize(None).dt.to_period(freq).astype(str)
    return rows.groupby(bucket).size().reset_index(name="count").rename(columns={"timestamp": period})


def recent_errors(df: pd.DataFrame, filters: dict | None = None,
                  start_date: str | None = None, end_date: str | None = None, limit: int = 20):
    rows = _apply_filters(_errors(df), filters)
    if "timestamp" in rows.columns:
        if start_date:
            rows = rows[rows["timestamp"] >= pd.to_datetime(start_date, utc=True)]
        if end_date:
            rows = rows[rows["timestamp"] < pd.to_datetime(end_date, utc=True) + pd.Timedelta(days=1)]
        rows = rows.sort_values("timestamp", ascending=False)
    if rows.empty:
        return "No matching errors."
    cols = [c for c in ["timestamp", "service", "environment", "error_type", "status_code",
                        "endpoint", "downstream_service", "message"] if c in rows.columns]
    return f"{len(rows):,} matching errors. Most recent {min(int(limit), 50)}:\n" + _table(
        rows[cols].head(min(int(limit), 50)))


FILTERS_SCHEMA = {
    "type": "object",
    "description": 'Exact-match filters, e.g. {"service": "payment-service", "environment": "prod"}.',
    "additionalProperties": {"type": "string"},
}

TOOLS = [
    {
        "name": "get_dataset_profile",
        "description": "Row count, columns, services, error types, environments and date range of the loaded logs. Call this first if unsure what the data contains.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "count_by",
        "description": "Count errors grouped by one or more columns (e.g. ['error_type'], ['service', 'environment']), most frequent first, with optional filters.",
        "input_schema": {
            "type": "object",
            "properties": {
                "columns": {"type": "array", "items": {"type": "string"}},
                "filters": FILTERS_SCHEMA,
                "limit": {"type": "integer", "description": "Max rows, default 20."},
            },
            "required": ["columns"],
        },
    },
    {
        "name": "errors_over_time",
        "description": "Error counts per day, week or month, with optional filters. Use for trends and spikes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["day", "week", "month"]},
                "filters": FILTERS_SCHEMA,
            },
        },
    },
    {
        "name": "recent_errors",
        "description": "Individual error events, newest first, with optional filters and a YYYY-MM-DD date range. Use to show examples.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filters": FILTERS_SCHEMA,
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer", "description": "Max rows, default 20, at most 50."},
            },
        },
    },
]

SYSTEM = """You are a log-analysis assistant helping people understand application error logs exported from Splunk.

Rules:
1. Get real numbers from the tools before drawing any conclusion. Never invent figures.
2. Column values are case-insensitive exact matches; check get_dataset_profile for valid names.
3. Explain findings in plain English, using a small markdown table when it helps.
4. End with the single most important pattern and one concrete next step.
5. Never mention tool or column names; phrase next steps as a follow-up question the user could ask, or an action for their team.
6. Keep answers concise."""


def _table(df: pd.DataFrame) -> str:
    return df.to_csv(index=False)


def run_tool(df: pd.DataFrame, name: str, args: dict) -> str:
    try:
        if name == "get_dataset_profile":
            result = dataset_profile(df)
        elif name == "count_by":
            result = count_by(df, args.get("columns", []), args.get("filters"), args.get("limit", 20))
        elif name == "errors_over_time":
            result = errors_over_time(df, args.get("period", "month"), args.get("filters"))
        elif name == "recent_errors":
            result = recent_errors(df, args.get("filters"), args.get("start_date"),
                                   args.get("end_date"), args.get("limit", 20))
        else:
            return f"Unknown tool: {name}"
    except Exception as exc:  # report bad arguments back to Claude so it can retry
        return f"Tool error: {exc}"
    if isinstance(result, pd.DataFrame):
        return "No results." if result.empty else _table(result)
    return str(result)


def ask(df: pd.DataFrame, messages: list[dict]):
    """Run the tool-use loop. Yields ("status", tool names) while working, then ("answer", text)."""
    messages = list(messages)
    for _ in range(MAX_TOOL_ROUNDS):
        response = _claude().messages.create(
            model=MODEL, max_tokens=2048, system=SYSTEM, tools=TOOLS, messages=messages,
        )
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            yield "answer", "\n".join(b.text for b in response.content if b.type == "text").strip()
            return
        yield "status", ", ".join(t.name for t in tool_uses)
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": t.id, "content": run_tool(df, t.name, t.input)}
            for t in tool_uses
        ]})
    yield "answer", "That question needed too many steps. Try asking something more specific."
