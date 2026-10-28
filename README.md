# Splunk Log Analyzer

Ask questions about application error logs in plain English. A Claude agent
picks the right query, runs it with pandas over the full Splunk export and
explains what it finds, so every number in an answer is computed from the
data, not guessed.

**Live demo:** https://huggingface.co/spaces/rkigobe/splunk-log-analyzer
(also at [kigobe.net/projects](https://kigobe.net/projects))

> "Which downstream service causes the most timeouts in prod?"
>
> **payment-gateway causes the most timeouts in prod**: 363 across the full
> date range, followed closely by customer-profile (349)...

## Features

- **Plain-English questions** over error logs: top errors, worst services,
  failing dependencies, trends and spikes, example events.
- **Answers grounded in the data.** Claude never reads the raw file. It calls
  query tools that run over every row and return small result tables, then
  explains those results.
- **Works with Splunk exports** in CSV, JSON, JSONL or Excel. Common column
  names (`level`, `errorType`, `@timestamp`, `_time`, ...) are mapped
  automatically.
- **Preloaded sample data**: 25,000 synthetic Spring Boot error events, so
  the demo works without uploading anything.
- **Safe to host publicly**: each visitor's upload lives only in their own
  session and is deleted within an hour, and per-visitor and global daily
  question limits cap API cost.

## Using the app

1. Open the [live demo](https://huggingface.co/spaces/rkigobe/splunk-log-analyzer),
   or [run it locally](#run-locally).
2. The sample logs are already loaded. To use your own, open the **Data** tab
   and upload a Splunk export (see [Exporting from Splunk](#exporting-logs-from-splunk)).
   The tab shows the first 20 rows so you can check it loaded correctly.
3. In **Ask a question**, type a question or pick an example, then press
   **Ask**. While it works, the chat shows which query is running.
4. Ask follow-ups. The conversation keeps context, so you can drill down
   from a service to an error type to a date range. **Clear conversation**
   starts over.

### Example questions

| Goal | Ask |
|---|---|
| Overview | "Summarise the biggest issues and suggest next steps" |
| Worst offenders | "Which service has the most errors in prod?" |
| Dependencies | "Which downstream service causes the most timeouts?" |
| Breakdowns | "Break down payment-service errors by error type and environment" |
| Trends | "Show the monthly error trend for payment-service" |
| Spikes | "Which week had the most errors?" |
| Examples | "Show me billing-service errors from January 2026" |
| What's in the file | "What data is loaded?" |

## Exporting logs from Splunk

Run a search that returns the events you care about, keep the useful fields
with `table`, then export the results.

```spl
index=app_logs level=ERROR earliest=-30d
| table _time level service environment errorType errorMessage statusCode endpoint downstreamService host
```

Then choose **Export** (the down-arrow icon above the results) and pick
**CSV** or **JSON**. Exports from the Splunk CLI/REST API in JSON-lines
format (`{"result": {...}}` per line) work too.

The analyzer works with any columns, but these names are recognised and give
the best answers:

| Meaning | Recognised column names |
|---|---|
| Timestamp | `_time`, `@timestamp`, `timestamp` |
| Log level | `level`, `severity`, `loglevel`, `log_level` |
| Service | `service`, `app`, `application`, `service_name` |
| Error type | `errorType`, `exception`, `error`, `err_type`, `error_type` |
| Message | `errorMessage`, `msg`, `log_message`, `message` |
| HTTP status | `statusCode`, `status_code` |
| Dependency | `downstreamService`, `downstream_service` |

If a `log_level` column exists, only `ERROR` rows are analysed; otherwise
every row is treated as an error event. Any other column (`environment`,
`endpoint`, `host`, ...) can be used for grouping and filtering by name.

## How it works

```
upload / sample CSV ──► core.load_logs()  normalise column names, parse timestamps
                              │
question ──► core.ask() ──► Claude (tool use) ──► run_tool() ──► pandas over all rows
                              ▲                                        │
                              └──────── small result table ◄───────────┘
                              │
                        plain-English answer
```

Claude can call four tools, defined in [`core.py`](core.py):

| Tool | What it returns |
|---|---|
| `get_dataset_profile` | Row count, columns, services, error types, environments, date range |
| `count_by` | Error counts grouped by any columns, with optional exact-match filters |
| `errors_over_time` | Error counts per day, week or month, with optional filters |
| `recent_errors` | Matching error events, newest first, with filters and a date range (max 50 rows) |

The loop allows up to six rounds of tool calls per question. Only the
visible conversation is sent back to Claude on follow-ups; tool calls and
their results stay on the server.

### Files

```
app.py              Gradio UI, per-session data, daily question limits
core.py             Log loading, schema mapping, query tools, Claude tool-use loop
sample_data/        Synthetic Spring Boot error logs (25,000 events)
requirements.txt    Pinned dependencies
HF_SPACE_README.md  README for the Hugging Face Space (has the Space config)
deploy.sh           Publishes the app to the Hugging Face Space
```

## Run locally

Requires Python 3.11+ and an [Anthropic API key](https://console.anthropic.com/).

```bash
git clone https://github.com/robertkigobe/splunk-log-analyzer.git
cd splunk-log-analyzer
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then set ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python app.py     # open http://127.0.0.1:7860
```

### Configuration

All settings are optional environment variables (or lines in `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Claude API key |
| `CLAUDE_MODEL` | `claude-haiku-5-5` | Model used by the agent |
| `DAILY_QUESTION_LIMIT` | `10` | Questions per visitor per day |
| `GLOBAL_DAILY_LIMIT` | `200` | Questions per day across all visitors |
| `MAX_FILE_SIZE` | `50mb` | Largest upload accepted |

## Deploy to Hugging Face Spaces

```bash
hf auth login
./deploy.sh
```

The first run creates the Space. Then add `ANTHROPIC_API_KEY` as a secret
under the Space's **Settings → Variables and secrets**; never commit it.

## Privacy and cost

- The sample data is synthetic: fictional services, hosts and customer IDs.
- Uploaded files are kept only in the uploader's session and deleted from
  disk within an hour.
- Claude receives query results (counts and at most 50 matching rows per
  call), never the whole file.
- On Claude Haiku a question costs well under a cent, and the global daily
  limit caps the worst case at about $1/day.

## License

[MIT](LICENSE)
