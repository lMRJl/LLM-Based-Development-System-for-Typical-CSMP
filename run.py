"""Quick start script — launches both backend and frontend"""

import subprocess
import sys
import os
import time


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    backend_dir = root

    print("=" * 60)
    print("  Agent Platform — Quick Start")
    print("=" * 60)

    # Check .env
    env_file = os.path.join(root, ".env")
    if not os.path.exists(env_file):
        print("[!] .env not found. Copy .env.example to .env and configure your API keys.")
        print("    cp .env.example .env")
        sys.exit(1)

    # Start backend
    # NOTE: --reload-exclude patterns MUST be passed as separate list elements to avoid shell glob expansion.
    # If running uvicorn manually in a shell, quote the patterns: --reload-exclude "projects/*"
    print("\n[1/2] Starting FastAPI backend on http://127.0.0.1:8000 ...")
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload",
         "--reload-dir", "backend",
         "--reload-dir", "frontend"],
        cwd=backend_dir,
        env={**os.environ, "PYTHONPATH": backend_dir},
    )

    time.sleep(2)

    # Start frontend
    print("[2/2] Starting Streamlit frontend on http://127.0.0.1:8501 ...")
    frontend = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "frontend/app.py", "--server.port", "8501"],
        cwd=backend_dir,
        env={**os.environ, "PYTHONPATH": backend_dir},
    )

    print("\n" + "=" * 60)
    print("  Backend API:  http://127.0.0.1:8000/docs")
    print("  Frontend:     http://127.0.0.1:8501")
    print("=" * 60)
    print("\nPress Ctrl+C to stop both services.\n")

    try:
        backend.wait()
        frontend.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        backend.terminate()
        frontend.terminate()
        backend.wait()
        frontend.wait()
        print("Done.")


if __name__ == "__main__":
    main()
