import os

from fastapi import FastAPI

app = FastAPI(title="warehouse-sentinel")


@app.get("/")
def root():
    return {"service": "warehouse-sentinel", "status": "ok"}


@app.get("/health")
def healthz():
    return {"status": "ok", "revision": os.environ.get("K_REVISION", "local")}
