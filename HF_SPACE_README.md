---
title: Splunk Log Analyzer
emoji: 🔍
colorFrom: yellow
colorTo: gray
sdk: gradio
sdk_version: 5.50.0
python_version: "3.12"
app_file: app.py
pinned: false
license: mit
short_description: Ask questions about Splunk error logs in plain English
---

# Splunk Log Analyzer

Ask questions about application error logs in plain English. A Claude agent
picks the right query tool, runs it with pandas over the full log export and
explains the result, so every number in an answer is computed from the data
rather than guessed.

- **Sample data:** `sample_data/springboot_error_logs.csv` holds 25,000
  synthetic Spring Boot error events (fictional services, hosts and customer
  IDs) in a Splunk/ECS-style schema, preloaded on start.
- **Your own logs:** upload a Splunk export as `.csv`, `.json`, `.jsonl` or
  `.xlsx`. Common column names (`level`, `errorType`, `@timestamp`, `_time`,
  ...) are mapped automatically. Uploads stay in your session only and are
  deleted within an hour.
- **Tools Claude can call:** dataset profile, grouped counts with filters,
  error trend by day/week/month, and matching error events.

## Run locally

```bash
uv venv && uv pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
.venv/bin/python app.py
```

## Deploy

`./deploy.sh` uploads the app to the `rkigobe/splunk-log-analyzer` Space.
Set `ANTHROPIC_API_KEY` as a secret in the Space settings. Optional
variables: `CLAUDE_MODEL`, `DAILY_QUESTION_LIMIT`, `GLOBAL_DAILY_LIMIT`.
