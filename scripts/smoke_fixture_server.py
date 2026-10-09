"""Offline browser QA server. Never use this fixture as real AI/quality evidence.

Run explicitly: python scripts/smoke_fixture_server.py --allow-offline-fixtures
Uses .local/smoke-data, no API credentials, no outgoing provider HTTP.
"""
from pathlib import Path
import argparse
import os
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-offline-fixtures", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=18879)
    parser.add_argument("--frontend-port", type=int, default=3102)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.environ["ARK_API_KEY"] = ""
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["INTERIOR_FRONTEND_PORT"] = str(args.frontend_port)
    os.environ["INTERIOR_BACKEND_PORT"] = str(args.port)
    from PIL import Image, ImageDraw
    import httpx
    import uvicorn
    from webapp import db, server
    from services import asset_store

    data = root / ".local" / "smoke-data"
    data.mkdir(parents=True, exist_ok=True)
    db.DB_PATH = data / "smoke.db"
    server.WEBAPP_TMP = data / "tmp"
    server.INTERIOR_COMPOSE_DIR = server.WEBAPP_TMP / "interior_compositions"
    server.ASSET_EXTRACT_DIR = server.WEBAPP_TMP / "asset_extractions"
    server.INTERIOR_REPORT_DIR = data / "interior_reports"
    server.PDF_OUTPUT_DIR = data / "pdf"
    asset_store.DATA_DIR = data / "assets"
    asset_store.OUTPUTS_DIR = asset_store.DATA_DIR / "outputs"
    asset_store.UPLOADS_DIR = asset_store.DATA_DIR / "uploads"
    asset_store.ASSETS_CSV = asset_store.DATA_DIR / "assets.csv"
    server.ASSETS_CSV = asset_store.ASSETS_CSV
    server.ASSET_OUTPUTS_DIR = asset_store.OUTPUTS_DIR
    scene = Image.new("RGB", (960, 600), "#e9e4d7")
    draw = ImageDraw.Draw(scene)
    draw.rectangle((0, 390, 960, 600), fill="#b99d7a")
    draw.line((0, 390, 960, 390), fill="#66614f", width=4)
    draw.rectangle((75, 80, 250, 295), fill="#a8c8d7", outline="#4e5c5e", width=7)
    draw.line((162, 80, 162, 295), fill="#4e5c5e", width=5)
    draw.rectangle((720, 100, 855, 390), fill="#8d7159", outline="#5c4b3d", width=7)
    for x in range(0, 960, 100):
        draw.line((x, 390, x-70, 600), fill="#6d604f", width=2)
    draw.rectangle((340, 340, 605, 480), fill="#747b70", outline="#484f46", width=5)
    draw.rectangle((380, 480, 395, 540), fill="#484f46")
    draw.rectangle((550, 480, 565, 540), fill="#484f46")
    draw.text((310, 565), "OFFLINE QA FIXTURE - NOT AN AI RENDER", fill="#332e24")
    scene.save(data / "before.png")

    def forbid_http(*a, **kw):
        raise RuntimeError("External HTTP forbidden in offline browser fixture")
    httpx.request = forbid_http

    def fixture_render(prompt, images, output_path, label, log_parts, *, job_id=None, **kwargs):
        image = Image.open(images[0]).convert("RGB")
        ImageDraw.Draw(image).rectangle((320, 60, 650, 280), fill="#a3af98")
        image.save(output_path)
        if job_id:
            server.update_job(job_id, provider="offline_fixture", provider_model="offline-smoke-not-AI",
                              provider_usage={}, provider_usage_available=False)
        return output_path
    def fixture_review(*a, **kw):
        return {"status": "passed", "method": "offline_test_fixture_NOT_AI", "issues": [],
                "repair_instruction": "", "provider_invoked": False, "model": "offline-test-fixture",
                "usage": {}, "duration_seconds": 0}
    server.generate_ai_render = fixture_render
    server.verify_composition = fixture_review
    @server.app.middleware("http")
    async def fixture_marker(request, call_next):
        if request.url.path == "/api/ai-providers/status":
            from fastapi.responses import JSONResponse
            return JSONResponse({"provider": "OpenAI", "configured": True,
                                 "models": {"image": "offline-not-ai", "vision": "offline-not-ai"}},
                                headers={"X-Composer-QA-Fixture": "offline-not-ai"})
        response = await call_next(request)
        response.headers["X-Composer-QA-Fixture"] = "offline-not-ai"
        return response
    print("OFFLINE QA ONLY: provider and semantic review are fixtures, not real AI.", flush=True)
    uvicorn.run(server.app, host="127.0.0.1", port=args.port)

if __name__ == "__main__":
    main()
