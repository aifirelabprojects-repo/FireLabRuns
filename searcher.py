from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

def duckduckgo_browser_search(query, num_results=3):
    results = []
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"https://duckduckgo.com/?q={query}")
        page.wait_for_timeout(2000)  # allow JS to render
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.select("a[data-testid='result-title-link']")[:num_results]:
            href = a.get("href")
            if href:
                results.append(href)
        browser.close()
    return results
query="analytix group"
print(duckduckgo_browser_search(query, num_results=3))