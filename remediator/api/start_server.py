"""启动入口：``python -m remediator.api.start_server``"""
from __future__ import annotations

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    import uvicorn

    from remediator.api.main import app

    uvicorn.run(app, host="0.0.0.0", port=8000)
