from strands import Agent, tool
from strands.handlers.callback_handler import null_callback_handler
from strands.models.gemini import GeminiModel
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import re
import subprocess
import sys
import time

CHAT_MODEL_ID = "gemini-2.5-flash-lite"
CHROME_PATH = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
CHROME_PROFILE_DIR = r"C:\chrome-debug-profile"

chrome_process = subprocess.Popen([
    CHROME_PATH,
    "--remote-debugging-port=9222",
    f"--user-data-dir={CHROME_PROFILE_DIR}",
])

# Give Chrome a moment to start up before Playwright tries to connect
time.sleep(2)

# Keep Playwright and browser open for the lifetime of the script
playwright = sync_playwright().start()
browser = playwright.chromium.connect_over_cdp("http://localhost:9222")
context = browser.contexts[0]


def parse_url_file(filepath: str) -> list[tuple[str, str]]:
    """Parse a .txt file with alternating label/URL lines into (label, url) pairs."""
    entries = []
    current_label = None
    with open(filepath, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("http"):
                if current_label is not None:
                    entries.append((current_label, line))
                    current_label = None
            else:
                current_label = line
    return entries

def airline_scraper(url: str) -> str:

    page = context.new_page()
    try:
        page.goto(url)

       # Award prices load asynchronously after the cards; wait for at least one
        try:
            page.wait_for_selector('[data-testid="award-price"]', timeout=15000)
        except Exception:
            # Prices may not be present (e.g. sold out), continue anyway
            pass

        html = page.content()
        #print(html)
    
    finally:
        page.close()

    return _parse_airline_results(html)


def _format_time(iso_str: str) -> str:
    m = re.search(r'T(\d{2}:\d{2})', iso_str)
    return m.group(1) if m else iso_str


def _extract_flights_from_script(html: str) -> list[str]:
    """Extract flight itineraries and award points from inline JS data."""
    if 'atmosPoints' not in html:
        return []

    blocks = re.split(r'version:"v2\.0"', html)
    results: list[str] = []

    for block in blocks:
        if 'flightNumber:' not in block or 'REFUNDABLE_' not in block:
            continue

        # Segment data lives before allSegments (which duplicates it)
        seg_section = block.split('allSegments:')[0] if 'allSegments:' in block else block

        flight_nums = re.findall(r'flightNumber:(\d+)', seg_section)
        carriers = re.findall(r'carrierCode:"(\w+)"', seg_section)
        dep_stations = re.findall(r'departureStation:"(\w+)"', seg_section)
        arr_stations = re.findall(r'arrivalStation:"(\w+)"', seg_section)
        dep_times = re.findall(r'departureTime:"([^"]+)"', seg_section)
        arr_times = re.findall(r'arrivalTime:"([^"]+)"', seg_section)

        if not flight_nums:
            continue

        # publishingCarrier + displayCarrier each emit carrierCode/flightNumber;
        # take every other entry to deduplicate.
        flight_nums = flight_nums[::2]
        carriers = carriers[::2]

        seg_labels = []
        for j, fn in enumerate(flight_nums):
            c = carriers[j] if j < len(carriers) else "??"
            seg_labels.append(f"{c}{fn}")
        flight_str = " / ".join(seg_labels)

        origin = dep_stations[0] if dep_stations else "?"
        dest = arr_stations[-1] if arr_stations else "?"
        dep_short = _format_time(dep_times[0]) if dep_times else "?"
        arr_short = _format_time(arr_times[-1]) if arr_times else "?"

        stops = len(flight_nums) - 1
        stop_str = "Nonstop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"

        costs: list[str] = []
        for fare_class, label in [("REFUNDABLE_MAIN", "Main"), ("REFUNDABLE_FIRST", "First")]:
            pts_m = re.search(rf'{fare_class}:\{{[^}}]*?atmosPoints:(\d+)', block)
            cash_m = re.search(rf'{fare_class}:\{{grandTotal:([\d.]+)', block)
            if pts_m:
                pts = f"{int(pts_m.group(1)):,}"
                cash = f" + ${cash_m.group(1)}" if cash_m else ""
                costs.append(f"{label}: {pts} pts{cash}")

        cost_str = " | ".join(costs) if costs else "N/A"
        results.append(
            f"- {flight_str} | {origin} {dep_short} → {dest} {arr_short}"
            f" | {stop_str} | {cost_str}"
        )

    return results


def _parse_airline_results(html: str) -> str:
    # Prefer structured JS data — reliably contains atmosPoints
    js_results = _extract_flights_from_script(html)
    if js_results:
        return "\n".join(js_results)

    # Fallback: DOM-based parsing (award points may be missing)
    soup = BeautifulSoup(html, "html.parser")
    results = []

    cards = soup.select("div.flight-card-container")

    if not cards:
        return "No airline results could be parsed."

    for card in cards:
        flight_num_el = card.select_one("span.flight-number")
        flight_num = flight_num_el.get_text(strip=True) if flight_num_el else "Unknown"

        duration_el = card.select_one("span.duration")
        duration = duration_el.get_text(strip=True) if duration_el else "N/A"

        dep_time_el = card.select_one("[data-testid='departure-time']")
        dep_time = dep_time_el.get_text(strip=True) if dep_time_el else "N/A"
        dep_airport_el = card.select_one(".time-col.departure .airport-code")
        dep_airport = dep_airport_el.get_text(strip=True) if dep_airport_el else "N/A"

        arr_time_el = card.select_one("[data-testid='arrival-time']")
        arr_time = arr_time_el.get_text(strip=True) if arr_time_el else "N/A"
        arr_airport_el = card.select_one(".time-col.arrival .airport-code")
        arr_airport = arr_airport_el.get_text(strip=True) if arr_airport_el else "N/A"

        points = "N/A"
        cash = "N/A"
        main_btn = card.select_one("button.main")
        if main_btn:
            award_el = main_btn.select_one("[data-testid='award-price']")
            if award_el:
                for span in award_el.find_all("span"):
                    classes = " ".join(span.get("class", []))
                    text = span.get_text(strip=True)
                    if "sr-only" in classes or span.get("aria-hidden") == "true":
                        continue
                    if text in ("+", ""):
                        continue
                    if text.startswith("$"):
                        cash = text
                    elif points == "N/A":
                        points = text

        main_cost = f"{points} pts + {cash}" if points != "N/A" else "N/A"
        results.append(
            f"- {flight_num} | {dep_airport} {dep_time} → {arr_airport} {arr_time}"
            f" | Duration: {duration} | Main (Refundable): {main_cost}"
        )

    return "\n".join(results) if results else "No airline results could be parsed."

if len(sys.argv) < 2:
    print("Usage: python agent.py <path-to-urls.txt>")
    sys.exit(1)

url_file = sys.argv[1]

SYSTEM_PROMPT = """You are a friendly assistant helping the user find the information they are looking for.

Goals:
You will be provided search results. If the information the user asks for are not in the results, say you don't have the results."""


def main() -> None:
    model = GeminiModel(
        model_id=CHAT_MODEL_ID,
        params={"temperature": 0.7,"max_output_tokens": 2048},
    )
    agent = Agent(
        model=model,
        tools=[],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=null_callback_handler,
    )

    if len(sys.argv) < 2:
        print("Usage: python agent.py <path-to-urls.txt>")
        sys.exit(1)

    url_file = sys.argv[1]

    try:
        entries = parse_url_file(url_file)
        if not entries:
            print("No label/URL pairs found in the file.")
            sys.exit(1)

        for label, url in entries:
            print(f"\n{'='*60}")
            print(f"  {label}")
            print(f"{'='*60}")
            scraped = airline_scraper(url)
            response = agent(
                f"Here are flight search results scraped from Alaska Airlines. "
                f"List the 5 cheapest flights based on how many award points they cost. "
                f"Highlight the cheapeast flight:\n\n{scraped}"
            )
            print(response)
    finally:
        context.close()
        playwright.stop()
        chrome_process.terminate()


if __name__ == "__main__":
    main()