from strands import Agent, tool
from strands.handlers.callback_handler import null_callback_handler
from strands.models.gemini import GeminiModel
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
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

#ollama_model = Ollama(id="llama3.2")

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

def hotel_scraper(url: str) -> str:

    page = context.new_page()
    try:
        page.goto(url)

        try:
            page.wait_for_selector('.property-card-container', timeout=30000)
        except Exception:
            # Fallback: wait for skeleton loaders to disappear
            try:
                page.wait_for_selector('.skeleton-loader-container', state='hidden', timeout=30000)
            except Exception:
                time.sleep(15)

        html = page.content()

    finally:
        page.close()

    return _parse_hotel_results(html)


def _parse_hotel_results(html: str) -> str:
    import json
    soup = BeautifulSoup(html, "html.parser")
    results = []

    cards = soup.select("div.property-card")

    if not cards:
        return "No hotel results could be parsed."

    for card in cards:
        # data-property attribute contains pre-parsed JSON with hotelName and price
        data_prop = card.get("data-property")
        if data_prop:
            try:
                prop = json.loads(data_prop)
                name = prop.get("hotelName", "Unknown hotel")
                price = prop.get("price", "N/A")
                currency = prop.get("currency", "USD")
                results.append(f"- {name} | Price: {price} {currency}/Night")
                continue
            except (json.JSONDecodeError, AttributeError):
                pass

        # Fallback: parse from visible elements
        name_el = card.select_one("button.title-container div.t-subtitle-xl")
        price_el = card.select_one("span.m-price")
        currency_el = card.select_one("span.currency-label")

        name = name_el.get_text(strip=True) if name_el else "Unknown hotel"
        price = price_el.get_text(strip=True) if price_el else "N/A"
        currency = currency_el.get_text(strip=True) if currency_el else "USD/Night"

        results.append(f"- {name} | Price: {price} {currency}")

    return "\n".join(results) if results else "No hotel results could be parsed."

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
            scraped = hotel_scraper(url)
            response = agent(
                f"Here are hotel results scraped from Marriott. "
                f"List the first 10 hotels and their nightly prices. "
                f"Highlight the cheapeast hotel:\n\n{scraped}"
            )
            print(response)
    finally:
        context.close()
        playwright.stop()
        chrome_process.terminate()


if __name__ == "__main__":
    main()