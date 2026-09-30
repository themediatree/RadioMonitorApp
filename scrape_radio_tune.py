"""
Scrape all South Africa radio stations from radio-tune.com:
- station name
- stream URL (the actual audio stream link)
- logo image URL (downloaded locally too)

Install first:
    pip install playwright
    playwright install chromium

Run:
    python scrape_radio_tune.py
"""

import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from playwright.sync_api import sync_playwright

BASE = "https://radio-tune.com"
LIST_URL_PAGE1 = "https://radio-tune.com/country/south-africa/"
LIST_URL_TMPL = "https://radio-tune.com/country/south-africa/page/{}/"
OUTPUT_DIR = Path("output")
LOGO_DIR = OUTPUT_DIR / "logos"
CSV_PATH = OUTPUT_DIR / "south_africa_stations.csv"

# The actual stream URL isn't identifiable by keyword (it can be any domain/port/path,
# e.g. streamtheworld.com/.../FM947.mp3 OR zas1.ndx.co.za:8008/stream). Exclude the site's
# own domain, social-share links, and common tracking/ad scripts that show up identically
# on every page (these were being mistaken for the stream link).
EXCLUDED_HOSTS = (
    "radio-tune.com",
    "facebook.com",
    "facebook.net",
    "fbcdn.net",
    "twitter.com",
    "x.com",
    "googletagmanager.com",
    "google-analytics.com",
    "googlesyndication.com",
    "doubleclick.net",
    "google.com",
    "gstatic.com",
    "googleapis.com",
    "cloudflareinsights.com",
    "hotjar.com",
    "adsystem.com",
)


def looks_like_stream_link(href: str) -> bool:
    if not href or href.startswith("mailto:") or href.startswith("#"):
        return False
    parsed = urlparse(href)
    if not parsed.scheme.startswith("http"):
        return False
    host = parsed.netloc.lower()
    return not any(host == d or host.endswith("." + d) for d in EXCLUDED_HOSTS)


# Matches bare URLs appearing as plain visible text (the stream URL on this site is
# often just printed as text, not wrapped in a real <a href>, unlike share/nav links
# which show a label like "Facebook" instead of the URL itself).
URL_PATTERN = re.compile(r"https?://[^\s\"'<>\)\]]+")


def find_stream_url_in_text(text: str) -> str | None:
    for match in URL_PATTERN.finditer(text or ""):
        candidate = match.group(0).rstrip(".,;:")
        if looks_like_stream_link(candidate):
            return candidate
    return None


def get_all_station_links(page, max_pages_cap: int = 60) -> list[str]:
    """
    Walk pagination pages and collect station detail page URLs.

    Keep visiting /country/south-africa/page/N/ and stop once a page adds no new
    station links (two consecutive empty pages, to tolerate one-off glitches) or
    returns a non-OK status.
    """
    links = set()

    def collect() -> int:
        before = len(links)
        hrefs = page.eval_on_selector_all(
            "a[href*='/south-africa/']",
            "els => els.map(e => e.getAttribute('href'))",
        )
        for h in hrefs:
            if not h:
                continue
            full = urljoin(BASE, h)
            if re.match(r"^https://radio-tune\.com/south-africa/[^/]+/$", full):
                links.add(full)
        return len(links) - before

    page_num = 1
    consecutive_empty = 0

    while page_num <= max_pages_cap:
        url = LIST_URL_PAGE1 if page_num == 1 else LIST_URL_TMPL.format(page_num)
        response = page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status >= 400:
            print(f"Page {page_num}: HTTP {response.status}, stopping")
            break

        page.wait_for_timeout(300)
        added = collect()
        print(f"Page {page_num}: {added} new stations, {len(links)} total")

        if added == 0:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                print(f"No new stations for 2 consecutive pages, stopping at page {page_num}")
                break
        else:
            consecutive_empty = 0

        page_num += 1

    return sorted(links)


def extract_stream_and_logo(page, station_url: str) -> dict:
    """
    Load a station page, trigger the audio player, and capture the real network
    request for the stream - the direct equivalent of manually opening DevTools,
    going to Network > Media, clicking Play, and copying the request URL.
    """
    result = {"url": station_url, "name": None, "stream_url": None, "logo_url": None}

    captured_media = []  # (url, resource_type) tuples, in request order

    def on_request(request):
        # Playwright classifies <audio>/<video> stream loads as resource_type == "media",
        # which is exactly what DevTools' Network "Media" filter shows. Some sites load
        # the stream via XHR/fetch instead (e.g. for HLS playlists), so also catch those
        # if the URL itself looks like a stream endpoint.
        if request.resource_type == "media" or looks_like_stream_link(request.url):
            captured_media.append((request.url, request.resource_type))

    page.on("request", on_request)

    page.goto(station_url, wait_until="domcontentloaded")

    # --- Station name ---
    try:
        result["name"] = page.locator("h1").first.inner_text(timeout=5000).strip()
    except Exception:
        pass

    # --- Logo: prefer og:image meta, fall back to first content image ---
    try:
        og_image = page.locator("meta[property='og:image']").get_attribute("content", timeout=2000)
        if og_image:
            result["logo_url"] = urljoin(BASE, og_image)
    except Exception:
        pass
    if not result["logo_url"]:
        try:
            img_src = page.locator("img[src*='/wp-content/uploads/']").first.get_attribute("src", timeout=2000)
            if img_src:
                result["logo_url"] = urljoin(BASE, img_src)
        except Exception:
            pass

    # --- Dismiss any cookie-consent overlay that might block clicks ---
    for text in ["Accept", "Accept All", "I Agree", "Agree", "OK", "Got it"]:
        try:
            btn = page.get_by_text(text, exact=False).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=1000)
                page.wait_for_timeout(300)
                break
        except Exception:
            pass

    # --- Trigger playback ---
    # Strategy 1: if an <audio>/<video> element already has a src, just call .play() on it.
    try:
        page.evaluate(
            """
            () => {
              document.querySelectorAll('audio, video').forEach(el => {
                try { el.muted = false; el.play().catch(() => {}); } catch (e) {}
              });
            }
            """
        )
    except Exception:
        pass
    page.wait_for_timeout(1500)

    # Strategy 2: click a likely "play" control if nothing captured yet.
    if not captured_media:
        play_selectors = [
            "[class*='play' i]",
            "[id*='play' i]",
            "button[aria-label*='play' i]",
            "a[aria-label*='play' i]",
            "[onclick*='play' i]",
        ]
        for sel in play_selectors:
            if captured_media:
                break
            try:
                locator = page.locator(sel).first
                if locator.is_visible(timeout=800):
                    locator.click(timeout=1500)
                    page.wait_for_timeout(2000)
            except Exception:
                continue

    # Strategy 3: last resort, click the station's cover image (common pattern on
    # these radio-directory sites: clicking the logo starts the player).
    if not captured_media:
        try:
            img = page.locator("img[src*='/wp-content/uploads/']").first
            if img.is_visible(timeout=800):
                img.click(timeout=1500)
                page.wait_for_timeout(2000)
        except Exception:
            pass

    # Give slow players (buffering, redirects) a bit more time.
    if not captured_media:
        page.wait_for_timeout(2000)

    page.remove_listener("request", on_request)

    if captured_media:
        # Prefer an explicit "media" resource type over a URL-pattern-only match.
        media_typed = [u for u, rtype in captured_media if rtype == "media"]
        result["stream_url"] = media_typed[0] if media_typed else captured_media[0][0]

    return result


def download_logo(logo_url: str, dest_dir: Path, filename_hint: str) -> str | None:
    if not logo_url:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(logo_url.split("?")[0]).suffix or ".jpg"
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", filename_hint).strip("_") or "logo"
    dest_path = dest_dir / f"{safe_name}{ext}"
    try:
        resp = requests.get(logo_url, timeout=15)
        resp.raise_for_status()
        dest_path.write_bytes(resp.content)
        return str(dest_path)
    except Exception as e:
        print(f"  logo download failed for {logo_url}: {e}")
        return None


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    fieldnames = ["name", "url", "stream_url", "logo_url", "local_logo_path"]
    rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = context.new_page()

        station_links = get_all_station_links(page)
        print(f"\nTotal stations found: {len(station_links)}\n")

        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for i, url in enumerate(station_links, 1):
                print(f"[{i}/{len(station_links)}] {url}")
                data = extract_stream_and_logo(page, url)
                local_logo = download_logo(
                    data["logo_url"], LOGO_DIR, data["name"] or url.rstrip("/").split("/")[-1]
                )
                data["local_logo_path"] = local_logo
                rows.append(data)
                writer.writerow(data)
                f.flush()  # so the file is readable mid-run, not just at the end
                time.sleep(0.3)  # be polite to the server

        browser.close()

    missing = [r["url"] for r in rows if not r["stream_url"]]
    print(f"\nDone. Saved {len(rows)} stations to {CSV_PATH}")
    print(f"Logos saved to {LOGO_DIR}/")
    if missing:
        print(f"\n{len(missing)} stations had no stream URL found:")
        for m in missing:
            print(f"  {m}")


if __name__ == "__main__":
    main()
