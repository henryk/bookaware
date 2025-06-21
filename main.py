from typing import Optional, Dict, List

import requests
import structlog
import datetime
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from urllib.parse import urljoin

logger = structlog.get_logger(__name__)


class WebScraper:
    def __init__(self, start_url: str, username: str, password: str) -> None:
        self.current_url = start_url
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": UserAgent().random}
        )
        self.data: Optional[str] = None
        self.logger = logger.bind()

    def load_page(self) -> bytes:
        """
        Load the initial login page.
        """
        self.logger.info("Loading page", url=self.current_url)
        response = self.session.get(self.current_url)
        if response.status_code == 200:
            self.logger.info("Page loaded successfully", status_code=response.status_code, url=response.url)
            self.current_url = response.url
            self.data = response.content
            return self.data
        else:
            self.logger.error("Failed to load page", status_code=response.status_code)
            raise Exception(f"Failed to load page: {response.status_code}")

    def find_and_submit_form(self, values: Dict[str, str], button: Optional[str] = None) -> requests.Response:
        """
        Find the first form on the page and submit login credentials.
        """
        self.logger.info("Finding and submitting form", current_url=self.current_url)
        soup = BeautifulSoup(self.data, "lxml")
        form = soup.find("form")
        if not form:
            self.logger.error("No form found on the page")
            raise Exception("No form found on the page!")

        # Extract the form's action URL
        action_url = form.get("action")
        if not action_url.startswith("http"):
            # Handle relative URLs
            action_url = urljoin(self.current_url, action_url)
        self.logger.info("Have form", action_url=action_url)

        # Build the payload for the form
        inputs = form.find_all("input")
        payload = {}
        for input_field in inputs:
            name = input_field.get("name")
            if input_field.get("type") == "submit" and name != button:
                continue
            value = input_field.get("value", "")
            if name in values:
                value = values[name]
            payload[name] = value

        # Submit the form
        self.logger.info("Submitting form", url=action_url, payload=payload)
        response = self.session.post(action_url, data=payload)
        if response.status_code == 200:
            self.logger.info("Form submitted successfully", status_code=response.status_code)
            self.current_url = response.url
            self.data = response.content
            return self.data
        else:
            self.logger.error("Form submission failed", status_code=response.status_code)
            raise Exception(f"Login failed: {response.status_code}")

    def scrape_after_login(self, url: str) -> bytes:
        """
        Perform subsequent requests after login.
        """
        self.logger.info("Scraping after login", url=url)
        response = self.session.get(url)
        if response.status_code == 200:
            self.logger.info("Data retrieved successfully", status_code=response.status_code)
            print("Data successfully retrieved.")
            return response.content
        else:
            self.logger.error("Failed to scrape after login", status_code=response.status_code)
            raise Exception(f"Failed to access {url}: {response.status_code}")

    def find_and_follow_link(self, css_selector: str) -> bytes:
        """
        Finds a link on the page using the provided CSS selector and follows it.
        """
        self.logger.info("Attempting to find and follow link", selector=css_selector)
        soup = BeautifulSoup(self.data, "lxml")
        link = soup.select_one(css_selector)
        if not link:
            self.logger.error("No link found with the given CSS selector", selector=css_selector)
            raise Exception(f"No link found with the selector: {css_selector}")

        href = link.get("href")
        if not href:
            self.logger.error("The found link does not have an href attribute")
            raise Exception("The found link does not have an href attribute")

        # Resolve relative URLs to absolute URLs
        target_url = urljoin(self.current_url, href)
        self.logger.info("Following link", target_url=target_url)
        response = self.session.get(target_url)
        if response.status_code == 200:
            self.logger.info("Successfully navigated to the link", target_url=target_url)
            self.current_url = response.url
            self.data = response.content
            return self.data
        else:
            self.logger.error("Failed to navigate to the link", status_code=response.status_code, target_url=target_url)
            raise Exception(f"Failed to follow link: {response.status_code}")

    def extract_table(self) -> List[dict]:
        soup = BeautifulSoup(self.data, "lxml")

        items = []
        for tr in soup.select("#resptable-1 tbody tr"):
            tds = tr.find_all("td")
            date_str = tds[1].get_text(strip=True)
            due_date = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
            library = tds[2].get_text(strip=True)
            title = " ".join(tds[3].stripped_strings)
            hint = tds[4].get_text(strip=True)
            items.append({
                "due_date": due_date,
                "library": library,
                "title": title,
                "hint": hint,
                "days_left": (due_date - datetime.date.today()).days
            })
        return items

    def run(self) -> None:
        """
        Main method to execute the scraping process.
        """
        self.logger.info("Starting scraping process")
        try:
            self.load_page()
            self.find_and_submit_form({"selected": "ZTEXT       *SBK"})
            self.find_and_submit_form({"L#AUSW": self.username, "LPASSW": self.password}, "LLOGIN")
            self.find_and_follow_link('div#konto-services li a[href*="S*SZA"]')
            outstanding_books = self.extract_table()
            import pprint; pprint.pprint(outstanding_books)
        except Exception as e:
            self.logger.error("Error occurred during scraping process", error=str(e))
            print(f"Error: {e}")


if __name__ == "__main__":
    # Example usage
    scraper = WebScraper(
        start_url="https://voebb.de/",
        username="USER",
        password="PASS",
    )
    scraper.run()
