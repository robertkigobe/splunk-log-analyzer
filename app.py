"""Gradio web UI for the Splunk Log Analyzer (runs as a Hugging Face Space).

Run locally:   .venv/bin/python app.py      then open http://127.0.0.1:7860
"""

import os
import time
from collections import defaultdict

import gradio as gr
from dotenv import load_dotenv

load_dotenv()  # load ANTHROPIC_API_KEY etc. from .env before importing core

import core  # noqa: E402

# Cost controls. Each question is a few tool calls on Claude Haiku 5.5 (well
# under a cent), so the global cap keeps the worst case to about $1/day.
DAILY_QUESTION_LIMIT = int(os.environ.get("DAILY_QUESTION_LIMIT", "10"))    # per visitor
GLOBAL_DAILY_LIMIT = int(os.environ.get("GLOBAL_DAILY_LIMIT", "200"))       # all visitors
MAX_FILE_SIZE = os.environ.get("MAX_FILE_SIZE", "50mb")

EXAMPLE_QUESTIONS = [
    "Summarise the biggest issues and suggest next steps",
    "Which service has the most errors in prod?",
    "Show the monthly error trend for payment-service",
    "Which downstream service causes the most timeouts?",
]

# Loaded once and shared read-only. Each visitor's own upload lives in their
# session state, so one visitor never sees another's logs.
SAMPLE_DF = core.load_sample()

# Questions asked per (visitor, day), plus a "*" total for everyone. Kept in
# memory: resets when the Space restarts, which is fine for a free-tier limit.
_usage = defaultdict(int)


def _client_ip(request: gr.Request) -> str:
    # Hugging Face (and nginx) sit in front of the app, so the direct client is
    # a proxy. The visitor's address is the first entry in X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or request.client.host


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _active(df):
    return SAMPLE_DF if df is None else df


def _status(df, label):
    return f"**Loaded:** {label} - {len(df):,} rows, {len(df.columns)} columns."


# ── Event handlers ──────────────────────────────────────────────────────────

def on_upload(file):
    if file is None:  # cleared: back to the sample
        return None, _status(SAMPLE_DF, "sample Spring Boot error logs"), SAMPLE_DF.head(20), []
    try:
        df = core.load_logs(file)
    except Exception as exc:
        return gr.update(), f"⚠️ Could not read that file: {exc}", gr.update(), gr.update()
    return df, _status(df, os.path.basename(file)), df.head(20), []


def on_ask(df, question, history, request: gr.Request):
    question = (question or "").strip()[:500]
    if not question:
        yield "", history
        return

    history = history + [{"role": "user", "content": question}]
    visitor, everyone = (_client_ip(request), _today()), ("*", _today())
    if _usage[everyone] >= GLOBAL_DAILY_LIMIT:
        yield "", history + [{"role": "assistant", "content": "The demo has reached its daily limit. Please try again tomorrow."}]
        return
    if _usage[visitor] >= DAILY_QUESTION_LIMIT:
        yield "", history + [{"role": "assistant", "content": f"You've used your {DAILY_QUESTION_LIMIT} questions for today. Please come back tomorrow."}]
        return
    _usage[visitor] += 1
    _usage[everyone] += 1

    # Only the visible conversation goes back to Claude; tool calls stay server-side.
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    yield "", history + [{"role": "assistant", "content": "_Reading the logs..._"}]
    try:
        for kind, text in core.ask(_active(df), messages):
            if kind == "status":
                yield "", history + [{"role": "assistant", "content": f"_Running `{text}`..._"}]
            else:
                left = DAILY_QUESTION_LIMIT - _usage[visitor]
                yield "", history + [{"role": "assistant",
                                      "content": f"{text}\n\n<sub>{left} AI questions left today.</sub>"}]
    except Exception as exc:
        print(f"[ask error] {type(exc).__name__}: {exc}")
        yield "", history + [{"role": "assistant", "content": "Sorry, something went wrong answering that. Please try again."}]


# ── Layout ──────────────────────────────────────────────────────────────────

# delete_cache: every 30 min, remove uploaded files older than 1 hour.
with gr.Blocks(title="Splunk Log Analyzer · kigobe.net", delete_cache=(1800, 3600),
               theme=gr.themes.Soft(primary_hue="amber")) as demo:
    df_state = gr.State(None)  # None = use the sample logs

    gr.Markdown(
        "# 🔍 Splunk Log Analyzer\n"
        "Ask questions about application error logs in plain English. Claude picks the right "
        "query, runs it over the full log export and explains what it finds - every number is "
        "computed from the data, not guessed.\n\n"
        "Sample Spring Boot error logs are preloaded (synthetic data, 25,000 events). "
        "You can also upload your own Splunk export.\n\n"
        "<sub>Uploads are only visible in your session and deleted within an hour. Claude sees "
        "query results (counts and up to 50 matching rows), never the whole file. "
        "By [Robert Kigobe](https://kigobe.net).</sub>"
    )

    with gr.Tab("Ask a question"):
        chatbot = gr.Chatbot(label="Conversation", height=480, type="messages")
        with gr.Row():
            question = gr.Textbox(label="Your question", placeholder="e.g. Which service has the most errors?", scale=4)
            ask_btn = gr.Button("Ask", variant="primary", scale=1)
        gr.Examples(EXAMPLE_QUESTIONS, inputs=question)
        clear_btn = gr.Button("Clear conversation", size="sm")

    with gr.Tab("Data"):
        file_in = gr.File(label="Upload a Splunk export (.csv, .json, .jsonl or .xlsx)",
                          file_types=[".csv", ".json", ".jsonl", ".xlsx"], type="filepath")
        status = gr.Markdown(_status(SAMPLE_DF, "sample Spring Boot error logs"))
        preview = gr.DataFrame(value=SAMPLE_DF.head(20), label="First 20 rows", interactive=False)

    file_in.change(on_upload, file_in, [df_state, status, preview, chatbot])
    ask_inputs, ask_outputs = [df_state, question, chatbot], [question, chatbot]
    ask_btn.click(on_ask, ask_inputs, ask_outputs)
    question.submit(on_ask, ask_inputs, ask_outputs)
    clear_btn.click(lambda: [], outputs=chatbot)


# Limit how many jobs run at once so a busy moment can't overload the free CPU.
demo.queue(default_concurrency_limit=2, max_size=20)

if __name__ == "__main__":
    # Host/port come from GRADIO_SERVER_NAME / GRADIO_SERVER_PORT when set
    # (Hugging Face sets them); otherwise Gradio's local defaults apply.
    demo.launch(max_file_size=MAX_FILE_SIZE, ssr_mode=False)
