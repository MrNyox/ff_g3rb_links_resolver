import os
import asyncio
import threading

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory,
    abort
)

from scraper import (
    run_fitgirl_scrape,
    run_game3rb_scrape,
    update_status,
    sanitize_game3rb_settings,
    DEFAULT_GAME3RB_SETTINGS
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LINKS_DIR = os.path.join(BASE_DIR, "links")

OUTPUT_FILES = (
    "ff_links.txt",
    "ff_direct_download_links.txt",
    "game3rb_1cloudfile_links.txt",
    "game3rb_direct_download_links.txt"
)

os.makedirs(LINKS_DIR, exist_ok=True)

# Create empty output files if they do not exist yet.
for filename in OUTPUT_FILES:
    filepath = os.path.join(LINKS_DIR, filename)
    if not os.path.exists(filepath):
        open(filepath, "a", encoding="utf-8").close()


app = Flask(__name__)

status_lock = threading.Lock()


def base_status():
    return {
        "running": False,
        "phase": "idle",
        "message": "Ready",
        "progress": 0,

        "mode": "fitgirl",
        "download_mode": "iterative",
        "settings": DEFAULT_GAME3RB_SETTINGS.copy(),

        "main_link": "",
        "concurrency": 5,

        "total": 0,
        "source_links": 0,
        "ff_links": 0,

        "processed": 0,
        "success": 0,
        "failure": 0,

        "output_files": []
    }


status = base_status()


def _run_thread(
    mode,
    main_link,
    concurrency,
    download_mode,
    settings
):
    """
    Background thread runner.
    Flask returns immediately while scraping continues here.
    """
    try:
        if mode == "game3rb":
            asyncio.run(
                run_game3rb_scrape(
                    main_link,
                    concurrency,
                    download_mode,
                    settings,
                    LINKS_DIR,
                    status,
                    status_lock
                )
            )
        else:
            asyncio.run(
                run_fitgirl_scrape(
                    main_link,
                    concurrency,
                    LINKS_DIR,
                    status,
                    status_lock
                )
            )

    except Exception as exc:
        update_status(
            status,
            status_lock,
            running=False,
            phase="error",
            message=f"Fatal backend error: {exc}",
            progress=100
        )


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/start")
def api_start():
    payload = request.get_json(silent=True) or request.form

    mode = str(payload.get("mode") or "fitgirl").strip().lower()
    if mode not in ("fitgirl", "game3rb"):
        mode = "fitgirl"

    main_link = (payload.get("main_link") or "").strip()

    download_mode = str(payload.get("game3rb_download_mode") or "iterative").strip().lower()
    if download_mode not in ("iterative", "concurrent"):
        download_mode = "iterative"

    raw_settings = payload.get("settings")
    if not isinstance(raw_settings, dict):
        raw_settings = {}

    settings = sanitize_game3rb_settings(raw_settings)

    try:
        concurrency = int(payload.get("concurrency", 5))
    except Exception:
        concurrency = 5

    if mode == "game3rb" and download_mode == "iterative":
        concurrency = 1
    else:
        concurrency = max(1, min(100, concurrency))

    if not main_link:
        return jsonify(ok=False, error="main_link is required."), 400

    if not main_link.startswith(("http://", "https://")):
        return jsonify(ok=False, error="main_link must start with http:// or https://"), 400

    with status_lock:
        if status.get("running"):
            return jsonify(ok=False, error="A task is already running."), 409

        status.clear()
        status.update(base_status())
        status.update(
            running=True,
            phase="queued",
            message="Task queued",
            mode=mode,
            download_mode=download_mode if mode == "game3rb" else "",
            settings=settings,
            main_link=main_link,
            concurrency=concurrency
        )

    threading.Thread(
        target=_run_thread,
        args=(
            mode,
            main_link,
            concurrency,
            download_mode if mode == "game3rb" else "",
            settings
        ),
        daemon=True
    ).start()

    return jsonify(ok=True, message="Task started")


@app.get("/api/status")
def api_status():
    with status_lock:
        return jsonify(status)


@app.get("/download/<path:filename>")
def download_file(filename):
    allowed = set(OUTPUT_FILES)

    if filename not in allowed:
        abort(404)

    os.makedirs(LINKS_DIR, exist_ok=True)

    return send_from_directory(
        LINKS_DIR,
        filename,
        as_attachment=True
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True
    )
