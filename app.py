import hashlib
import os
import secrets
import subprocess
import time
import traceback
import uuid

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db
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

app = FastAPI(title="ERROR404 Store")
templates = Jinja2Templates(directory=os.path.join(REPO_DIR, "templates"))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", REPO_DIR, *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_customer(request: Request) -> dict | None:
    token = request.cookies.get("session_token")
    if not token:
        return None
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.email, c.name FROM sessions s
                JOIN customers c ON c.id = s.customer_id
                WHERE s.token = %s
                """,
                (token,),
            )
            return cur.fetchone()
    finally:
        conn.close()


async def _report_to_autocure(exc: Exception, stack_trace: str, request_path: str) -> dict:
    payload = {
        "type": "exception",
        "environment": "staging",
        "data": {
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "stack_trace": stack_trace,
            "request_path": request_path,
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "commit_sha": _git("rev-parse", "HEAD"),
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{AUTOCURE_URL}/events",
            headers={
                "Authorization": f"Bearer {AUTOCURE_API_KEY}",
                "Idempotency-Key": f"store-{uuid.uuid4()}",
            },
            json=payload,
        )
    if resp.status_code >= 400:
        return {"error": f"Autocure event submission failed ({resp.status_code}): {resp.text}"}
    return resp.json()


async def _report_slow_query(sql: str, execution_ms: float) -> dict:
    payload = {
        "type": "slow_query",
        "environment": "staging",
        "data": {
            "sql": sql,
            "execution_ms": round(execution_ms),
            "database": "mysql",
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "commit_sha": _git("rev-parse", "HEAD"),
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{AUTOCURE_URL}/events",
            headers={
                "Authorization": f"Bearer {AUTOCURE_API_KEY}",
                "Idempotency-Key": f"slowquery-{uuid.uuid4()}",
            },
            json=payload,
        )
    if resp.status_code >= 400:
        return {"error": f"Autocure event submission failed ({resp.status_code}): {resp.text}"}
    return resp.json()


async def _error_page(
    request: Request, customer: dict | None, exc: Exception, request_path: str
) -> HTMLResponse:
    stack_trace = traceback.format_exc()
    autocure_report = await _report_to_autocure(exc, stack_trace, request_path)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "customer": customer,
            "error": str(exc),
            "workflow_id": autocure_report.get("workflow_id"),
            "autocure_error": autocure_report.get("error"),
        },
    )


@app.get("/")
async def index(request: Request):
    customer = get_customer(request)
    return RedirectResponse("/dashboard" if customer else "/login", status_code=303)


@app.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request):
    return templates.TemplateResponse(request, "signup.html", {"customer": None, "error": None})


@app.post("/signup")
async def signup_submit(
    request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...)
):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE email=%s", (email,))
            if cur.fetchone():
                return templates.TemplateResponse(
                    request,
                    "signup.html",
                    {"customer": None, "error": "That email is already registered."},
                )
            cur.execute(
                "INSERT INTO customers (email, password_hash, name) VALUES (%s,%s,%s)",
                (email, _hash_password(password), name),
            )
            customer_id = cur.lastrowid
            token = secrets.token_hex(32)
            cur.execute(
                "INSERT INTO sessions (token, customer_id) VALUES (%s,%s)", (token, customer_id)
            )
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie("session_token", token, httponly=True)
        return response
    finally:
        conn.close()


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"customer": None, "error": None})


@app.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM customers WHERE email=%s AND password_hash=%s",
                (email, _hash_password(password)),
            )
            row = cur.fetchone()
            if not row:
                return templates.TemplateResponse(
                    request,
                    "login.html",
                    {"customer": None, "error": "Invalid email or password."},
                )
            token = secrets.token_hex(32)
            cur.execute(
                "INSERT INTO sessions (token, customer_id) VALUES (%s,%s)", (token, row["id"])
            )
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie("session_token", token, httponly=True)
        return response
    finally:
        conn.close()


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session_token")
    if token:
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE token=%s", (token,))
        finally:
            conn.close()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session_token")
    return response


@app.get("/products", response_class=HTMLResponse)
async def products(request: Request):
    customer = get_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, price_cents FROM products ORDER BY id")
            rows = cur.fetchall()
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "products.html", {"customer": customer, "products": rows}
    )


@app.get("/checkout", response_class=HTMLResponse)
async def checkout_form(request: Request, product_id: int):
    customer = get_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, price_cents FROM products WHERE id=%s", (product_id,))
            product = cur.fetchone()
    finally:
        conn.close()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return templates.TemplateResponse(
        request, "checkout.html", {"customer": customer, "product": product}
    )


@app.post("/checkout")
async def checkout_submit(
    request: Request,
    product_id: int = Form(...),
    qty: int = Form(...),
    discount_code: str = Form(""),
):
    customer = get_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, price_cents FROM products WHERE id=%s", (product_id,))
            product = cur.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        discount_code_clean = discount_code.strip() or None
        order = Order(
            id=0,
            customer_email=customer["email"],
            items=[{"price_cents": product["price_cents"], "qty": qty}],
            discount_code=discount_code_clean,
        )
        total_cents = apply_discount(order)

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO orders (customer_id, discount_code, total_cents) VALUES (%s,%s,%s)",
                (customer["id"], discount_code_clean, total_cents),
            )
            order_id = cur.lastrowid
            cur.execute(
                "INSERT INTO order_items (order_id, product_id, qty, price_cents) VALUES (%s,%s,%s,%s)",
                (order_id, product_id, qty, product["price_cents"]),
            )
        return RedirectResponse(f"/orders/{order_id}", status_code=303)
    except Exception as exc:
        return await _error_page(request, customer, exc, "/checkout")
    finally:
        conn.close()


@app.get("/orders/{order_id}", response_class=HTMLResponse)
async def view_order(request: Request, order_id: int):
    customer = get_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE id=%s AND customer_id=%s", (order_id, customer["id"])
            )
            order_row = cur.fetchone()
            if not order_row:
                raise HTTPException(status_code=404, detail="Order not found")
            cur.execute(
                "SELECT qty, price_cents FROM order_items WHERE order_id=%s", (order_id,)
            )
            items = cur.fetchall()

        order = Order(id=order_row["id"], customer_email=customer["email"], items=items)
        sku = get_first_item_sku(order)
        return templates.TemplateResponse(
            request, "receipt.html", {"customer": customer, "order": order_row, "sku": sku}
        )
    except HTTPException:
        raise
    except Exception as exc:
        return await _error_page(request, customer, exc, f"/orders/{order_id}")
    finally:
        conn.close()


@app.get("/orders/{order_id}/export")
async def export_order(request: Request, order_id: int):
    customer = get_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE id=%s AND customer_id=%s", (order_id, customer["id"])
            )
            order_row = cur.fetchone()
            if not order_row:
                raise HTTPException(status_code=404, detail="Order not found")
            cur.execute(
                "SELECT qty, price_cents FROM order_items WHERE order_id=%s", (order_id,)
            )
            items = cur.fetchall()

        order = Order(id=order_row["id"], customer_email=customer["email"], items=items)
        summary = summarize_items(order)
        text = f"Order #{order_id}\n{summary}\nTotal: ${order_row['total_cents'] / 100:.2f}\n"
        return PlainTextResponse(text)
    except HTTPException:
        raise
    except Exception as exc:
        return await _error_page(request, customer, exc, f"/orders/{order_id}/export")
    finally:
        conn.close()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    customer = get_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, total_cents, created_at FROM orders WHERE customer_id=%s ORDER BY created_at DESC",
                (customer["id"],),
            )
            orders = cur.fetchall()
            cur.execute(
                """
                SELECT oi.qty, oi.price_cents FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE o.customer_id = %s
                """,
                (customer["id"],),
            )
            all_items = cur.fetchall()

        order = Order(id=0, customer_email=customer["email"], items=all_items)
        avg_item_price_cents = calculate_average_item_price(order)
        domain = extract_email_domain(order)
        greeting = f", {customer['name']} from {domain}"

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "customer": customer,
                "orders": orders,
                "avg_item_price_cents": avg_item_price_cents,
                "greeting": greeting,
            },
        )
    except Exception as exc:
        return await _error_page(request, customer, exc, "/dashboard")
    finally:
        conn.close()


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    customer = get_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "reports.html", {"customer": customer})


_SLOW_QUERY_SQL = "SELECT id, note FROM audit_log TABLESAMPLE SYSTEM(1) LIMIT 10;"
_SLOW_QUERY_THRESHOLD_MS = 300


@app.post("/reports/activity", response_class=HTMLResponse)
async def run_activity_report(request: Request):
    customer = get_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    conn = db.get_connection()
    try:
        start = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(_SLOW_QUERY_SQL)
            rows = cur.fetchall()
        execution_ms = (time.perf_counter() - start) * 1000

        is_slow = execution_ms > _SLOW_QUERY_THRESHOLD_MS
        workflow_id = None
        autocure_error = None
        if is_slow:
            autocure_report = await _report_slow_query(_SLOW_QUERY_SQL, execution_ms)
            workflow_id = autocure_report.get("workflow_id")
            autocure_error = autocure_report.get("error")

        return templates.TemplateResponse(
            request,
            "slow_query.html",
            {
                "customer": customer,
                "sql": _SLOW_QUERY_SQL,
                "execution_ms": round(execution_ms),
                "rows": rows,
                "is_slow": is_slow,
                "threshold_ms": _SLOW_QUERY_THRESHOLD_MS,
                "workflow_id": workflow_id,
                "autocure_error": autocure_error,
            },
        )
    finally:
        conn.close()


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
