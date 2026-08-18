import os
import subprocess
import time
import traceback
import uuid

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from order import (
    Order,
    apply_discount,
    calculate_average_item_price,
    extract_email_domain,
    get_first_item_sku,
    summarize_items,
)

load_dotenv()

AUTOCURE_URL = os.environ["AUTOCURE_URL"].rstrip("/")
AUTOCURE_API_KEY = os.environ["AUTOCURE_API_KEY"]

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="ERROR404 demo")
app.mount("/static", StaticFiles(directory=os.path.join(REPO_DIR, "static")), name="static")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", REPO_DIR, *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _order(**overrides) -> Order:
    defaults = dict(
        id=int(time.time()),
        customer_email="jane@example.com",
        items=[{"price_cents": 1999, "qty": 1}],
    )
    defaults.update(overrides)
    return Order(**defaults)


SCENARIOS = {
    "discount": {
        "label": "Apply an invalid discount code",
        "run": lambda: apply_discount(_order(discount_code="BOGUS")),
    },
    "average-price": {
        "label": "Average item price of an empty cart",
        "run": lambda: calculate_average_item_price(_order(items=[])),
    },
    "email-domain": {
        "label": "Extract domain from a malformed email",
        "run": lambda: extract_email_domain(_order(customer_email="not-an-email")),
    },
    "summarize": {
        "label": "Summarize items for export",
        "run": lambda: summarize_items(_order()),
    },
    "sku-lookup": {
        "label": "Look up the first item's SKU",
        "run": lambda: get_first_item_sku(_order()),
    },
}


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(os.path.join(REPO_DIR, "static", "index.html"))


@app.get("/api/repo-info")
async def repo_info() -> dict:
    return {
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit_sha": _git("rev-parse", "HEAD"),
    }


@app.get("/api/scenarios")
async def scenarios() -> dict:
    return {key: {"label": s["label"]} for key, s in SCENARIOS.items()}


@app.post("/api/trigger/{scenario_key}")
async def trigger(scenario_key: str) -> dict:
    scenario = SCENARIOS.get(scenario_key)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario {scenario_key!r}")
    try:
        result = scenario["run"]()
        return {"ok": True, "result": result}
    except Exception as exc:
        stack_trace = traceback.format_exc()
        autocure_report = await _report_to_autocure(exc, stack_trace, scenario_key)
        return {"ok": False, "error": str(exc), "autocure": autocure_report}


async def _report_to_autocure(exc: Exception, stack_trace: str, scenario_key: str) -> dict:
    payload = {
        "type": "exception",
        "environment": "staging",
        "data": {
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "stack_trace": stack_trace,
            "request_path": f"/api/trigger/{scenario_key}",
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "commit_sha": _git("rev-parse", "HEAD"),
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{AUTOCURE_URL}/events",
            headers={
                "Authorization": f"Bearer {AUTOCURE_API_KEY}",
                "Idempotency-Key": f"demo-{uuid.uuid4()}",
            },
            json=payload,
        )
    if resp.status_code >= 400:
        return {"error": f"Autocure event submission failed ({resp.status_code}): {resp.text}"}
    return resp.json()


@app.get("/api/workflow-status/{workflow_id}")
async def workflow_status(workflow_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{AUTOCURE_URL}/workflows/{workflow_id}",
            headers={"Authorization": f"Bearer {AUTOCURE_API_KEY}"},
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()
