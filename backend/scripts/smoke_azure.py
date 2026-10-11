"""Smoke-test the Azure OpenAI GPT-5.2 deployment before building on it.

Run from the repo root:
    uv run --with openai --with python-dotenv --with pillow python backend/scripts/smoke_azure.py

Checks:
  1. Responses API + strict JSON schema (text)
  2. Responses API + image input + strict JSON schema (reads a generated mini invoice)
  3. Chat Completions + strict JSON schema (fallback path)
Prints which path works so llm.py can use `api_path: auto` confidently.
Never prints the API key.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageDraw

load_dotenv()

ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")

if not (ENDPOINT and KEY and DEPLOYMENT):
    sys.exit("Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT in .env")

client = OpenAI(base_url=f"{ENDPOINT}/openai/v1/", api_key=KEY, timeout=120)

SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": ["string", "null"]},
        "total": {"type": ["number", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["invoice_number", "total", "confidence"],
    "additionalProperties": False,
}


def mini_invoice_png() -> str:
    img = Image.new("RGB", (640, 240), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), "ACME OFFICE SUPPLIES INC", fill="black")
    d.text((20, 70), "Invoice No: INV-A-2207", fill="black")
    d.text((20, 110), "Date: 2026-10-02", fill="black")
    d.text((20, 170), "TOTAL DUE: $4,779.24", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def report(name: str, ok: bool, detail: str, started: float) -> bool:
    print(f"[{'OK ' if ok else 'FAIL'}] {name:<34} {time.time() - started:5.1f}s  {detail}")
    return ok


def check_responses_text() -> bool:
    t = time.time()
    try:
        r = client.responses.create(
            model=DEPLOYMENT,
            input="Invoice INV-77 has a total of 120.50. Extract it.",
            reasoning={"effort": "low"},
            max_output_tokens=2000,
            text={"format": {"type": "json_schema", "name": "mini", "schema": SCHEMA, "strict": True}},
        )
        data = json.loads(r.output_text)
        u = r.usage
        return report("responses + json_schema (text)", data.get("invoice_number") == "INV-77",
                      f"{data} tokens in/out={u.input_tokens}/{u.output_tokens}", t)
    except Exception as e:  # noqa: BLE001
        return report("responses + json_schema (text)", False, f"{type(e).__name__}: {str(e)[:200]}", t)


def check_responses_image() -> bool:
    t = time.time()
    try:
        r = client.responses.create(
            model=DEPLOYMENT,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Read this invoice image and extract the fields."},
                    {"type": "input_image", "image_url": mini_invoice_png()},
                ],
            }],
            reasoning={"effort": "low"},
            max_output_tokens=2000,
            text={"format": {"type": "json_schema", "name": "mini", "schema": SCHEMA, "strict": True}},
        )
        data = json.loads(r.output_text)
        ok = data.get("invoice_number") == "INV-A-2207" and abs((data.get("total") or 0) - 4779.24) < 0.01
        return report("responses + image + json_schema", ok, str(data), t)
    except Exception as e:  # noqa: BLE001
        return report("responses + image + json_schema", False, f"{type(e).__name__}: {str(e)[:200]}", t)


def check_chat() -> bool:
    t = time.time()
    try:
        r = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[{"role": "user", "content": "Invoice INV-77 has a total of 120.50. Extract it."}],
            reasoning_effort="low",
            max_completion_tokens=2000,
            response_format={"type": "json_schema", "json_schema": {"name": "mini", "schema": SCHEMA, "strict": True}},
        )
        data = json.loads(r.choices[0].message.content)
        return report("chat.completions + json_schema", data.get("invoice_number") == "INV-77", str(data), t)
    except Exception as e:  # noqa: BLE001
        return report("chat.completions + json_schema", False, f"{type(e).__name__}: {str(e)[:200]}", t)


if __name__ == "__main__":
    print(f"Endpoint: {ENDPOINT}  Deployment: {DEPLOYMENT}\n")
    resp_text = check_responses_text()
    resp_img = check_responses_image()
    chat = check_chat()
    print()
    if resp_text and resp_img:
        print("=> Use api_path: responses (preferred).")
    elif chat:
        print("=> Responses API not fully available; use api_path: chat (send images as image_url parts).")
    else:
        sys.exit("=> Neither path works. Check endpoint (must be https://<resource>.openai.azure.com), deployment name, key and region.")
