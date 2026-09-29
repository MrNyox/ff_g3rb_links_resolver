import os
import re
import time
import asyncio
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import AsyncStealthySession


# ============================================================
# Shared helpers
# ============================================================

def update_status(status, status_lock, **kwargs):
    """
    Thread-safe status updater shared between Flask and scraper.
    """
    if status_lock is not None:
        with status_lock:
            status.update(kwargs)
    else:
        status.update(kwargs)


def _fire_and_forget(func, *args, **kwargs):
    """
    Safely call a possibly-async Playwright method from a sync event callback.
    """
    try:
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            asyncio.create_task(result)
    except Exception:
        pass


def _normalize_any_link(raw, base_url):
    if not raw:
        return None

    raw = raw.strip().strip('"').strip("'").strip()

    if raw.startswith("//"):
        raw = "https:" + raw

    full = urljoin(base_url, raw)
    parsed = urlparse(full)

    if parsed.scheme not in ("http", "https"):
        return None

    return parsed._replace(fragment="").geturl()


def _clean_host_link(raw, base_url, host, strip_trailing_slash=False):
    full = _normalize_any_link(raw, base_url)

    if not full:
        return None

    if host and host not in full.lower():
        return None

    if strip_trailing_slash:
        full = full.rstrip("/")

    return full


# ============================================================
# FitGirl / FuckingFast configuration
# ============================================================

FF_DOMAIN = "fuckingfast.co"
FITGIRL_DIRECT_LINK_MARKER = "dl.fuckingfast.co/dl/"
FITGIRL_BUTTON_SELECTOR = "a.link-button.gay-button"

FITGIRL_MAX_RETRIES = 3
FITGIRL_POLL_INTERVAL = 0.2
FITGIRL_MAX_POLL_TIME = 8.0

FITGIRL_RATE_LIMIT_THRESHOLD = 15
FITGIRL_COOLDOWN_DURATION = 60.0

QUOTED_FF_PATTERN = re.compile(
    r"""["']([^"']*fuckingfast\.co[^"']*)["']""",
    re.I
)

BARE_FF_PATTERN = re.compile(
    r"""https?://[^\s"'<>]+?fuckingfast\.co[^\s"'<>]*""",
    re.I
)


def _clean_ff_link(raw, base_url):
    return _clean_host_link(
        raw,
        base_url,
        FF_DOMAIN,
        strip_trailing_slash=False
    )


# ============================================================
# Game3rb / 1CloudFile configuration
# ============================================================

GAME3RB_TARGET_HOST = "1cloudfile.com"
GAME3RB_INTERMEDIATE_SELECTOR = "a#download-link.direct"

GAME3RB_BUTTON_SELECTOR = (
    "a.uk-button.uk-button-secondary"
    ".uk-text-truncate.uk-width-1-1"
)

GAME3RB_PAGE_LOAD_TIMEOUT = 60000
GAME3RB_NAV_TIMEOUT = 60000
GAME3RB_MAX_POLL_TIME = 15.0

GAME3RB_RATE_LIMIT_THRESHOLD = 10
GAME3RB_COOLDOWN_DURATION = 45.0

DEFAULT_GAME3RB_SETTINGS = {
    "delay_between_urls": 0.0,
    "initial_warmup": 1.0,
    "max_retries": 4,
    "poll_interval": 0.25
}

QUOTED_1CLOUD_PATTERN = re.compile(
    r"""["']([^"']*1cloudfile\.com[^"']*)["']""",
    re.I
)

BARE_1CLOUD_PATTERN = re.compile(
    r"""https?://[^\s"'<>]+?1cloudfile\.com[^\s"'<>]*""",
    re.I
)


def sanitize_game3rb_settings(settings):
    settings = settings or {}

    def get_float(key, default, min_value=0.0, max_value=3600.0):
        try:
            value = float(settings.get(key, default))
        except Exception:
            value = default

        return max(min_value, min(max_value, value))

    def get_int(key, default, min_value=1, max_value=10):
        try:
            value = int(settings.get(key, default))
        except Exception:
            value = default

        return max(min_value, min(max_value, value))

    return {
        "delay_between_urls": get_float(
            "delay_between_urls",
            DEFAULT_GAME3RB_SETTINGS["delay_between_urls"],
            0.0,
            3600.0
        ),
        "initial_warmup": get_float(
            "initial_warmup",
            DEFAULT_GAME3RB_SETTINGS["initial_warmup"],
            0.0,
            3600.0
        ),
        "max_retries": get_int(
            "max_retries",
            DEFAULT_GAME3RB_SETTINGS["max_retries"],
            1,
            10
        ),
        "poll_interval": get_float(
            "poll_interval",
            DEFAULT_GAME3RB_SETTINGS["poll_interval"],
            0.05,
            10.0
        )
    }


# ============================================================
# Shared file writer
# ============================================================

async def file_writer_task(
    results_queue,
    total_urls,
    direct_file,
    status,
    status_lock
):
    success_count = 0
    failure_count = 0
    processed_count = 0
    seen_direct_links = set()

    while processed_count < total_urls:
        url, data, success = await results_queue.get()
        processed_count += 1

        if success and data and data.get("url"):
            direct_link = data["url"]

            if direct_link not in seen_direct_links:
                seen_direct_links.add(direct_link)
                success_count += 1

                with open(direct_file, "a", encoding="utf-8") as f:
                    f.write(direct_link + "\n")
        else:
            failure_count += 1

        progress = int((processed_count / total_urls) * 100) if total_urls else 100

        update_status(
            status,
            status_lock,
            processed=processed_count,
            success=success_count,
            failure=failure_count,
            progress=progress,
            message=f"Processed {processed_count}/{total_urls} source links"
        )

        results_queue.task_done()

    return success_count, failure_count


# ============================================================
# FITGIRL MODE
# ============================================================

async def extract_ff_links(session, main_url, status, status_lock):
    found = []
    seen = set()

    def add(raw):
        clean = _clean_ff_link(raw, main_url)
        if clean and clean not in seen:
            seen.add(clean)
            found.append(clean)

    # If input itself is already a fuckingfast link, keep it.
    add(main_url)

    async def extract_action(page):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass

        try:
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        try:
            hrefs = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href)"
            )
            for href in hrefs or []:
                add(href)
        except Exception:
            pass

        try:
            html = await page.content()

            for match in QUOTED_FF_PATTERN.findall(html):
                add(match)

            for match in BARE_FF_PATTERN.findall(html):
                add(match)
        except Exception:
            pass

        return page

    update_status(
        status,
        status_lock,
        message=f"Extracting {FF_DOMAIN} links from main page..."
    )

    try:
        await session.fetch(
            main_url,
            stealthy_headers=False,
            headers={"Referer": main_url},
            disable_resources=True,
            page_action=extract_action
        )
    except Exception as exc:
        update_status(
            status,
            status_lock,
            message=f"FitGirl extraction error: {exc}"
        )

    return list(dict.fromkeys(found))


async def process_fitgirl_url(
    session,
    url,
    worker_id,
    results_queue,
    state,
    status,
    status_lock
):
    for attempt in range(1, FITGIRL_MAX_RETRIES + 1):
        if not state["gate_event"].is_set():
            update_status(
                status,
                status_lock,
                message=f"Worker-{worker_id}: global FitGirl cooldown active, waiting..."
            )
            await state["gate_event"].wait()

        captured_links = []
        attempt_str = f"Attempt {attempt}/{FITGIRL_MAX_RETRIES}"

        update_status(
            status,
            status_lock,
            message=f"Worker-{worker_id}: processing {url} ({attempt_str})"
        )

        async def async_setup(page):
            def handle_request(request):
                try:
                    if FITGIRL_DIRECT_LINK_MARKER in request.url:
                        target_url = request.url

                        post_data = None
                        try:
                            post_data = request.post_data
                        except Exception:
                            pass

                        request_data = {
                            "timestamp": time.time(),
                            "url": target_url,
                            "method": request.method,
                            "headers": dict(request.headers),
                            "body": post_data,
                            "source_page": url
                        }

                        if not any(item["url"] == target_url for item in captured_links):
                            captured_links.append(request_data)
                except Exception:
                    pass

            def handle_popup(popup):
                _fire_and_forget(popup.close)

            def handle_download(download):
                _fire_and_forget(download.cancel)

            page.on("request", handle_request)
            page.on("popup", handle_popup)
            page.on("download", handle_download)

            return page

        async def async_action(page):
            try:
                await page.wait_for_selector(
                    FITGIRL_BUTTON_SELECTOR,
                    timeout=20000
                )

                download_btn = page.locator(FITGIRL_BUTTON_SELECTOR)

                if await download_btn.count() > 0:
                    await download_btn.click(force=True)

                    start_poll = time.time()
                    while time.time() - start_poll < FITGIRL_MAX_POLL_TIME:
                        if captured_links:
                            return page
                        await asyncio.sleep(FITGIRL_POLL_INTERVAL)

                    if not captured_links:
                        await download_btn.click(force=True)

                        start_poll = time.time()
                        while time.time() - start_poll < FITGIRL_MAX_POLL_TIME:
                            if captured_links:
                                return page
                            await asyncio.sleep(FITGIRL_POLL_INTERVAL)
            except Exception as exc:
                update_status(
                    status,
                    status_lock,
                    message=f"Worker-{worker_id}: FitGirl element automation failed ({exc})"
                )

            return page

        try:
            await session.fetch(
                url,
                stealthy_headers=False,
                headers={"Referer": url},
                disable_resources=True,
                page_setup=async_setup,
                page_action=async_action
            )
        except Exception as exc:
            update_status(
                status,
                status_lock,
                message=f"Worker-{worker_id}: FitGirl fetch error on {attempt_str} ({exc})"
            )

        if captured_links:
            target_data = captured_links[0]

            trigger_cooldown = False
            current_count = 0

            async with state["lock"]:
                state["success_count"] += 1
                current_count = state["success_count"]

                if current_count % FITGIRL_RATE_LIMIT_THRESHOLD == 0:
                    trigger_cooldown = True
                    state["gate_event"].clear()

            if trigger_cooldown:
                update_status(
                    status,
                    status_lock,
                    message=(
                        f"FITGIRL RATE LIMIT DEFENSE: {current_count} links grabbed. "
                        f"Pausing all workers for {int(FITGIRL_COOLDOWN_DURATION)}s..."
                    )
                )

                await asyncio.sleep(FITGIRL_COOLDOWN_DURATION)

                state["gate_event"].set()

                update_status(
                    status,
                    status_lock,
                    message="FitGirl cooldown finished. Re-opening worker gate."
                )

            await results_queue.put((url, target_data, True))
            return

        if attempt < FITGIRL_MAX_RETRIES:
            backoff_delay = attempt * 6
            update_status(
                status,
                status_lock,
                message=(
                    f"Worker-{worker_id}: no FitGirl direct link captured. "
                    f"Backing off {backoff_delay}s before retry..."
                )
            )
            await asyncio.sleep(backoff_delay)

    update_status(
        status,
        status_lock,
        message=f"Worker-{worker_id}: FitGirl failed completely after {FITGIRL_MAX_RETRIES} attempts."
    )
    await results_queue.put((url, None, False))


async def run_fitgirl_scrape(
    main_url,
    concurrency,
    links_dir,
    status,
    status_lock
):
    os.makedirs(links_dir, exist_ok=True)

    ff_file = os.path.join(links_dir, "ff_links.txt")
    direct_file = os.path.join(links_dir, "ff_direct_download_links.txt")

    open(ff_file, "w", encoding="utf-8").close()
    open(direct_file, "w", encoding="utf-8").close()

    try:
        concurrency = max(1, min(100, int(concurrency)))
    except Exception:
        concurrency = 5

    update_status(
        status,
        status_lock,
        running=True,
        mode="fitgirl",
        download_mode="",
        phase="extracting",
        progress=5,
        total=0,
        source_links=0,
        ff_links=0,
        processed=0,
        success=0,
        failure=0,
        main_link=main_url,
        concurrency=concurrency,
        output_files=[],
        message="Starting FitGirl stealth browser..."
    )

    async with AsyncStealthySession(
        headless=True,
        adblock=True,
        max_pages=concurrency
    ) as session:

        ff_links = await extract_ff_links(
            session,
            main_url,
            status,
            status_lock
        )

        with open(ff_file, "w", encoding="utf-8") as f:
            for link in ff_links:
                f.write(link + "\n")

        update_status(
            status,
            status_lock,
            phase="links_ready",
            source_links=len(ff_links),
            ff_links=len(ff_links),
            total=len(ff_links),
            progress=10 if ff_links else 100,
            message=f"Found {len(ff_links)} FitGirl FF links"
        )

        if not ff_links:
            update_status(
                status,
                status_lock,
                running=False,
                phase="done",
                progress=100,
                output_files=[
                    "ff_links.txt",
                    "ff_direct_download_links.txt"
                ],
                message="No fuckingfast links found on that page."
            )
            return

        results_queue = asyncio.Queue()

        writer = asyncio.create_task(
            file_writer_task(
                results_queue,
                len(ff_links),
                direct_file,
                status,
                status_lock
            )
        )

        state = {
            "success_count": 0,
            "gate_event": asyncio.Event(),
            "lock": asyncio.Lock()
        }
        state["gate_event"].set()

        sem = asyncio.Semaphore(concurrency)

        async def sem_worker(url, worker_id):
            async with sem:
                await process_fitgirl_url(
                    session,
                    url,
                    worker_id,
                    results_queue,
                    state,
                    status,
                    status_lock
                )

        tasks = [
            sem_worker(url, index)
            for index, url in enumerate(ff_links, 1)
        ]

        update_status(
            status,
            status_lock,
            phase="scraping",
            message=f"Scraping FitGirl direct links with concurrency {concurrency}"
        )

        try:
            await asyncio.gather(*tasks)
            success_count, failure_count = await writer
        finally:
            if not writer.done():
                writer.cancel()

        update_status(
            status,
            status_lock,
            running=False,
            phase="done",
            progress=100,
            processed=len(ff_links),
            success=success_count,
            failure=failure_count,
            output_files=[
                "ff_links.txt",
                "ff_direct_download_links.txt"
            ],
            message="FitGirl run complete."
        )


# ============================================================
# GAME3RB MODE
# ============================================================

async def _scan_game3rb_page(page, base_url, add):
    try:
        hrefs = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href)"
        )

        for href in hrefs or []:
            add(href, base_url)
    except Exception:
        pass

    try:
        html = await page.content()

        for match in QUOTED_1CLOUD_PATTERN.findall(html):
            add(match, base_url)

        for match in BARE_1CLOUD_PATTERN.findall(html):
            add(match, base_url)
    except Exception:
        pass


async def extract_game3rb_links(session, main_url, status, status_lock):
    found = []
    seen = set()
    intermediate_url = None

    def add(raw, base_url=main_url):
        clean = _clean_host_link(
            raw,
            base_url,
            GAME3RB_TARGET_HOST,
            strip_trailing_slash=True
        )

        if clean and clean not in seen:
            seen.add(clean)
            found.append(clean)

    # If input is already a 1cloudfile link, use it directly.
    add(main_url)

    if found:
        return list(dict.fromkeys(found))

    async def setup_page(page):
        try:
            page.set_default_navigation_timeout(GAME3RB_NAV_TIMEOUT)
            page.set_default_timeout(GAME3RB_PAGE_LOAD_TIMEOUT)
        except Exception:
            pass

        return page

    async def action_main(page):
        nonlocal intermediate_url

        try:
            await page.wait_for_selector("body", timeout=GAME3RB_PAGE_LOAD_TIMEOUT)
        except Exception:
            pass

        try:
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        # Try to extract the intermediate URL from Game3rb page.
        try:
            el = page.locator(GAME3RB_INTERMEDIATE_SELECTOR)

            if await el.count() > 0:
                href = await el.first.get_attribute("href")
                normalized = _normalize_any_link(href, main_url)

                if normalized:
                    intermediate_url = normalized
                    update_status(
                        status,
                        status_lock,
                        message=f"Found Game3rb intermediate link: {intermediate_url}"
                    )
        except Exception as exc:
            update_status(
                status,
                status_lock,
                message=f"Game3rb intermediate link extraction error: {exc}"
            )

        # Also scan current page directly in case 1cloudfile links are already present.
        await _scan_game3rb_page(page, main_url, add)

        return page

    update_status(
        status,
        status_lock,
        message="Extracting 1cloudfile links from Game3rb page..."
    )

    try:
        await session.fetch(
            main_url,
            stealthy_headers=True,
            disable_resources=True,
            wait_until="domcontentloaded",
            timeout=GAME3RB_NAV_TIMEOUT,
            page_setup=setup_page,
            page_action=action_main
        )
    except Exception as exc:
        update_status(
            status,
            status_lock,
            message=f"Game3rb main page fetch error: {exc}"
        )

    if not found and intermediate_url:
        update_status(
            status,
            status_lock,
            message="Scraping Game3rb intermediate page for 1cloudfile links..."
        )

        async def action_intermediate(page):
            try:
                await page.wait_for_selector("body", timeout=GAME3RB_PAGE_LOAD_TIMEOUT)
            except Exception:
                pass

            try:
                await asyncio.sleep(2)
            except Exception:
                pass

            await _scan_game3rb_page(page, intermediate_url, add)

            return page

        try:
            await session.fetch(
                intermediate_url,
                stealthy_headers=True,
                disable_resources=True,
                wait_until="domcontentloaded",
                timeout=GAME3RB_NAV_TIMEOUT,
                page_setup=setup_page,
                page_action=action_intermediate
            )
        except Exception as exc:
            update_status(
                status,
                status_lock,
                message=f"Game3rb intermediate page fetch error: {exc}"
            )

    # If the intermediate URL itself is a 1cloudfile URL, keep it.
    if not found and intermediate_url:
        add(intermediate_url, main_url)

    return list(dict.fromkeys(found))


async def process_1cloudfile_url(
    session,
    url,
    worker_id,
    results_queue,
    state,
    settings,
    status,
    status_lock,
    enable_cooldown=False,
    mode="iterative"
):
    max_retries = settings["max_retries"]
    poll_interval = settings["poll_interval"]

    for attempt in range(1, max_retries + 1):
        if enable_cooldown and state and not state["gate_event"].is_set():
            update_status(
                status,
                status_lock,
                message=f"Worker-{worker_id}: Game3rb cooldown active, waiting..."
            )
            await state["gate_event"].wait()

        captured_links = []
        attempt_str = f"Attempt {attempt}/{max_retries}"

        update_status(
            status,
            status_lock,
            message=f"Worker-{worker_id}: processing {url} ({attempt_str})"
        )

        async def async_setup(page):
            try:
                page.set_default_navigation_timeout(GAME3RB_NAV_TIMEOUT)
                page.set_default_timeout(GAME3RB_PAGE_LOAD_TIMEOUT)
            except Exception:
                pass

            def handle_request(request):
                try:
                    req_url = request.url

                    if GAME3RB_TARGET_HOST in req_url and "download_token=" in req_url:
                        post_data = None
                        try:
                            post_data = request.post_data
                        except Exception:
                            pass

                        entry = {
                            "timestamp": time.time(),
                            "url": req_url,
                            "method": request.method,
                            "headers": dict(request.headers),
                            "body": post_data,
                            "source_page": url
                        }

                        if not any(item["url"] == req_url for item in captured_links):
                            captured_links.append(entry)
                except Exception:
                    pass

            def handle_popup(popup):
                _fire_and_forget(popup.close)

            def handle_download(download):
                _fire_and_forget(download.cancel)

            page.on("request", handle_request)
            page.on("popup", handle_popup)
            page.on("download", handle_download)

            return page

        async def async_action(page):
            try:
                await page.wait_for_selector(
                    GAME3RB_BUTTON_SELECTOR,
                    timeout=GAME3RB_PAGE_LOAD_TIMEOUT
                )

                btn = page.locator(GAME3RB_BUTTON_SELECTOR)

                if await btn.count() > 0:
                    if mode == "iterative":
                        await asyncio.sleep(2.0)
                    else:
                        await asyncio.sleep(1.5)

                    await btn.first.click(force=True)

                    start = time.time()
                    while time.time() - start < GAME3RB_MAX_POLL_TIME:
                        if captured_links:
                            return page
                        await asyncio.sleep(poll_interval)

                    if not captured_links:
                        await asyncio.sleep(1.5)
                        await btn.first.click(force=True)

                        start = time.time()
                        while time.time() - start < GAME3RB_MAX_POLL_TIME:
                            if captured_links:
                                return page
                            await asyncio.sleep(poll_interval)
            except Exception as exc:
                update_status(
                    status,
                    status_lock,
                    message=f"Worker-{worker_id}: Game3rb button automation failed ({exc})"
                )

            return page

        try:
            await session.fetch(
                url,
                stealthy_headers=True,
                headers={
                    "Referer": "https://1cloudfile.com/",
                    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8"
                },
                disable_resources=True,
                wait_until="domcontentloaded",
                timeout=GAME3RB_NAV_TIMEOUT,
                page_setup=async_setup,
                page_action=async_action
            )
        except Exception as exc:
            update_status(
                status,
                status_lock,
                message=f"Worker-{worker_id}: Game3rb fetch error on {attempt_str} ({exc})"
            )

        if captured_links:
            target_data = captured_links[0]

            if enable_cooldown and state:
                trigger_cooldown = False
                current_count = 0

                async with state["lock"]:
                    state["success_count"] += 1
                    current_count = state["success_count"]

                    if current_count % GAME3RB_RATE_LIMIT_THRESHOLD == 0:
                        trigger_cooldown = True
                        state["gate_event"].clear()

                if trigger_cooldown:
                    update_status(
                        status,
                        status_lock,
                        message=(
                            f"GAME3RB COOLDOWN: {current_count} links grabbed. "
                            f"Pausing all workers for {int(GAME3RB_COOLDOWN_DURATION)}s..."
                        )
                    )

                    await asyncio.sleep(GAME3RB_COOLDOWN_DURATION)

                    state["gate_event"].set()

                    update_status(
                        status,
                        status_lock,
                        message="Game3rb cooldown finished. Re-opening worker gate."
                    )

            await results_queue.put((url, target_data, True))
            return

        if attempt < max_retries:
            if mode == "iterative":
                # Same backoff curve as your iterative script:
                # attempt 1 -> 10s, attempt 2 -> 20s, attempt 3 -> 35s, etc.
                delay = attempt * 10 + (attempt - 1) * 5
            else:
                # Same backoff curve as your concurrent script.
                delay = attempt * 5

            update_status(
                status,
                status_lock,
                message=(
                    f"Worker-{worker_id}: no Game3rb token captured. "
                    f"Backing off {delay}s before retry..."
                )
            )
            await asyncio.sleep(delay)

    update_status(
        status,
        status_lock,
        message=f"Worker-{worker_id}: Game3rb failed completely after {max_retries} attempts."
    )
    await results_queue.put((url, None, False))


async def run_game3rb_scrape(
    main_url,
    concurrency,
    download_mode,
    settings,
    links_dir,
    status,
    status_lock
):
    os.makedirs(links_dir, exist_ok=True)

    cloud_file = os.path.join(links_dir, "game3rb_1cloudfile_links.txt")
    direct_file = os.path.join(links_dir, "game3rb_direct_download_links.txt")

    open(cloud_file, "w", encoding="utf-8").close()
    open(direct_file, "w", encoding="utf-8").close()

    settings = sanitize_game3rb_settings(settings)

    download_mode = str(download_mode or "iterative").lower()
    if download_mode not in ("iterative", "concurrent"):
        download_mode = "iterative"

    try:
        concurrency = max(1, min(100, int(concurrency)))
    except Exception:
        concurrency = 2

    if download_mode == "iterative":
        effective_concurrency = 1
    else:
        effective_concurrency = concurrency

    update_status(
        status,
        status_lock,
        running=True,
        mode="game3rb",
        download_mode=download_mode,
        settings=settings,
        phase="extracting",
        progress=5,
        total=0,
        source_links=0,
        ff_links=0,
        processed=0,
        success=0,
        failure=0,
        main_link=main_url,
        concurrency=effective_concurrency,
        output_files=[],
        message="Starting Game3rb stealth browser..."
    )

    async with AsyncStealthySession(
        headless=True,
        adblock=True,
        max_pages=effective_concurrency
    ) as session:

        cloud_links = await extract_game3rb_links(
            session,
            main_url,
            status,
            status_lock
        )

        with open(cloud_file, "w", encoding="utf-8") as f:
            for link in cloud_links:
                f.write(link + "\n")

        update_status(
            status,
            status_lock,
            phase="links_ready",
            source_links=len(cloud_links),
            total=len(cloud_links),
            progress=10 if cloud_links else 100,
            message=f"Found {len(cloud_links)} 1cloudfile links"
        )

        if not cloud_links:
            update_status(
                status,
                status_lock,
                running=False,
                phase="done",
                progress=100,
                output_files=[
                    "game3rb_1cloudfile_links.txt",
                    "game3rb_direct_download_links.txt"
                ],
                message="No 1cloudfile links found."
            )
            return

        results_queue = asyncio.Queue()

        writer = asyncio.create_task(
            file_writer_task(
                results_queue,
                len(cloud_links),
                direct_file,
                status,
                status_lock
            )
        )

        update_status(
            status,
            status_lock,
            phase="scraping",
            message=f"Scraping Game3rb direct links in {download_mode} mode"
        )

        try:
            if download_mode == "iterative":
                if settings["initial_warmup"] > 0:
                    update_status(
                        status,
                        status_lock,
                        message=f"Iterative warmup: waiting {settings['initial_warmup']}s before first URL..."
                    )
                    await asyncio.sleep(settings["initial_warmup"])

                total = len(cloud_links)

                for index, url in enumerate(cloud_links, 1):
                    await process_1cloudfile_url(
                        session,
                        url,
                        index,
                        results_queue,
                        None,
                        settings,
                        status,
                        status_lock,
                        enable_cooldown=False,
                        mode="iterative"
                    )

                    if index < total and settings["delay_between_urls"] > 0:
                        update_status(
                            status,
                            status_lock,
                            message=f"Iterative delay: waiting {settings['delay_between_urls']}s before next URL..."
                        )
                        await asyncio.sleep(settings["delay_between_urls"])

            else:
                state = {
                    "success_count": 0,
                    "gate_event": asyncio.Event(),
                    "lock": asyncio.Lock()
                }
                state["gate_event"].set()

                sem = asyncio.Semaphore(effective_concurrency)

                async def sem_worker(url, worker_id):
                    async with sem:
                        if settings["delay_between_urls"] > 0:
                            await asyncio.sleep(settings["delay_between_urls"])

                        await process_1cloudfile_url(
                            session,
                            url,
                            worker_id,
                            results_queue,
                            state,
                            settings,
                            status,
                            status_lock,
                            enable_cooldown=True,
                            mode="concurrent"
                        )

                tasks = [
                    sem_worker(url, index)
                    for index, url in enumerate(cloud_links, 1)
                ]

                update_status(
                    status,
                    status_lock,
                    message=f"Scraping Game3rb direct links with concurrency {effective_concurrency}"
                )

                await asyncio.gather(*tasks)

            success_count, failure_count = await writer

        finally:
            if not writer.done():
                writer.cancel()

        update_status(
            status,
            status_lock,
            running=False,
            phase="done",
            progress=100,
            processed=len(cloud_links),
            success=success_count,
            failure=failure_count,
            output_files=[
                "game3rb_1cloudfile_links.txt",
                "game3rb_direct_download_links.txt"
            ],
            message="Game3rb run complete."
        )
