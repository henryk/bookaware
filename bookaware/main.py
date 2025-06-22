import datetime
import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, TypedDict
from urllib.parse import urljoin

import paho.mqtt.client as mqtt
import requests
import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger(__name__)


class BookEntry(TypedDict):
    due_date: str
    library: str
    title: str
    hint: str
    days_left: int


class VoebbScraper:
    def __init__(self, username: str, password: str) -> None:
        self.current_url = "https://voebb.de/"
        self.last_response: Optional[str] = None
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.logger = logger.bind()

    def load_page(self) -> bytes:
        """
        Load the initial login page.
        """
        self.logger.info("Loading page", url=self.current_url)
        response = self.session.get(self.current_url)
        if response.status_code == 200:
            self.logger.info(
                "Page loaded successfully",
                status_code=response.status_code,
                url=response.url,
            )
            self.current_url = response.url
            self.last_response = response.content
            return self.last_response
        else:
            self.logger.error("Failed to load page", status_code=response.status_code)
            raise Exception(f"Failed to load page: {response.status_code}")

    def find_and_submit_form(
        self, values: Dict[str, str], button: Optional[str] = None
    ) -> requests.Response:
        """
        Find the first form on the page and submit login credentials.
        """
        self.logger.info("Finding and submitting form", current_url=self.current_url)
        soup = BeautifulSoup(self.last_response, "lxml")
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
            self.logger.info(
                "Form submitted successfully", status_code=response.status_code
            )
            self.current_url = response.url
            self.last_response = response.content
            return self.last_response
        else:
            self.logger.error(
                "Form submission failed", status_code=response.status_code
            )
            raise Exception(f"Login failed: {response.status_code}")

    def scrape_after_login(self, url: str) -> bytes:
        """
        Perform subsequent requests after login.
        """
        self.logger.info("Scraping after login", url=url)
        response = self.session.get(url)
        if response.status_code == 200:
            self.logger.info(
                "Data retrieved successfully", status_code=response.status_code
            )
            print("Data successfully retrieved.")
            return response.content
        else:
            self.logger.error(
                "Failed to scrape after login", status_code=response.status_code
            )
            raise Exception(f"Failed to access {url}: {response.status_code}")

    def find_and_follow_link(self, css_selector: str) -> bytes:
        """
        Finds a link on the page using the provided CSS selector and follows it.
        """
        self.logger.info("Attempting to find and follow link", selector=css_selector)
        soup = BeautifulSoup(self.last_response, "lxml")
        link = soup.select_one(css_selector)
        if not link:
            self.logger.error(
                "No link found with the given CSS selector", selector=css_selector
            )
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
            self.logger.info(
                "Successfully navigated to the link", target_url=target_url
            )
            self.current_url = response.url
            self.last_response = response.content
            return self.last_response
        else:
            self.logger.error(
                "Failed to navigate to the link",
                status_code=response.status_code,
                target_url=target_url,
            )
            raise Exception(f"Failed to follow link: {response.status_code}")

    def extract_table(self) -> List[BookEntry]:
        soup = BeautifulSoup(self.last_response, "lxml")

        items = []
        for tr in soup.select("#resptable-1 tbody tr"):
            tds = tr.find_all("td")
            date_str = tds[1].get_text(strip=True)
            due_date = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
            library = tds[2].get_text(strip=True)
            title = " ".join(tds[3].stripped_strings)
            hint = tds[4].get_text(strip=True)
            items.append(
                {
                    "due_date": due_date.isoformat(),
                    "library": library,
                    "title": title,
                    "hint": hint,
                    "days_left": (due_date - datetime.date.today()).days,
                }
            )
        return items

    def run(self) -> list[BookEntry]:
        """
        Main method to execute the scraping process.
        """
        self.logger.info("Starting scraping process")
        try:
            self.load_page()
            self.find_and_submit_form({"selected": "ZTEXT       *SBK"})
            self.find_and_submit_form(
                {"L#AUSW": self.username, "LPASSW": self.password}, "LLOGIN"
            )
            self.find_and_follow_link('div#konto-services li a[href*="S*SZA"]')
            outstanding_books = self.extract_table()

            return outstanding_books
        except Exception as e:
            self.logger.error("Error occurred during scraping process", error=str(e))
            raise


class VoebbScraperHomeAssistant:
    def __init__(
        self,
    ):
        self.logger = logger.bind()
        self.supervisor_token = os.environ.get("SUPERVISOR_TOKEN")

        with open("/data/options.json") as f:
            self.config = json.load(f)
        self.logger.info("Have config", config=self.config)

        mqtt_service_info = {}
        for x in "host", "port", "username", "password":
            mqtt_service_info[x] = self.config[f"mqtt_{x}"]

        if len(mqtt_service_info) != 4:
            self.logger.info("Getting MQTT configuration", token=self.supervisor_token)
            response = requests.get(
                "http://supervisor/services/mqtt",
                headers={"Authorization": f"Bearer {self.supervisor_token}"},
            ).json()
            if response.get("result", None) == "error":
                raise Exception("Failed to get MQTT service info")
            mqtt_service_info = mqtt_service_info | response
        self.logger.info("Have MQTT service info", info=mqtt_service_info)

        self.mqtt_host = mqtt_service_info["host"]
        self.mqtt_port = mqtt_service_info["port"]
        self.mqtt_username = mqtt_service_info["username"]
        self.mqtt_password = mqtt_service_info["password"]

        self.client = mqtt.Client()
        self.connect_mqtt()
        self.interval_seconds = self.config("interval_hours") * 60 * 60

    def connect_mqtt(self):
        self.client.username_pw_set(self.mqtt_username, self.mqtt_password)
        self.client.connect(self.mqtt_host, self.mqtt_port, 60)
        self.client.loop_start()

    def publish_config(self):
        """Publish Home Assistant MQTT autodiscovery configuration topics for sensors"""
        sensors = [
            {"id": "closest_due_date", "name": "Closest Due Date"},
            {"id": "books_due_soon", "name": "Books Due in 5 Days"},
            {"id": "books_due_total", "name": "Total Outstanding Books"},
        ]
        for sensor in sensors:
            config_topic = f"{self.config['topic_prefix']}/{sensor['id']}/config"
            config_payload = {
                "name": sensor["name"],
                "state_topic": f"{self.config['topic_prefix']}/{sensor['id']}/state",
                "json_attributes_topic": (
                    f"{self.config['topic_prefix']}/books_due_total/attributes"
                    if sensor["id"] == "books_due_total"
                    else None
                ),
                "unique_id": f"library_{sensor['id']}",
                "device": {
                    "identifiers": ["library_books_tracker"],
                    "name": "Library Books",
                },
            }
            # Remove None values for clean config
            config_payload = {k: v for k, v in config_payload.items() if v is not None}
            self.client.publish(config_topic, json.dumps(config_payload), retain=True)

    def process_books_data(self, books: List[Dict[str, str]]):
        """Process scraped books data and publish states to HA"""
        # Calculate the closest due date
        current_date = datetime.now()
        due_soon_threshold = current_date + timedelta(days=5)

        books_due, books_due_within_5_days = 0, 0
        closest_due_date = None

        for book in books:
            due_date = datetime.strptime(book["due_date"], "%Y-%m-%d")

            # Update closest due date
            if not closest_due_date or due_date < closest_due_date:
                closest_due_date = due_date

            # Count books that are due
            if due_date >= current_date:
                books_due += 1
                if due_date <= due_soon_threshold:
                    books_due_within_5_days += 1

        # Formatted closest due date
        closest_due_date_str = (
            closest_due_date.strftime("%Y-%m-%d") if closest_due_date else None
        )

        # Publish state updates
        self.client.publish(
            f"{self.config['topic_prefix']}/closest_due_date/state",
            closest_due_date_str,
        )
        self.client.publish(
            f"{self.config['topic_prefix']}/books_due_soon/state",
            books_due_within_5_days,
        )
        self.client.publish(
            f"{self.config['topic_prefix']}/books_due_total/state", books_due
        )

        # Publish full list of books as a JSON attribute for the "total" sensor
        attributes_payload = {"books": books}
        self.client.publish(
            f"{self.config['topic_prefix']}/books_due_total/attributes",
            json.dumps(attributes_payload),
        )

    def run(self):
        """Main loop to scrape and publish data"""
        self.publish_config()
        while True:
            try:
                # Run scraper and get outstanding books data
                scraper = VoebbScraper(
                    username=self.config["username"], password=self.config["password"]
                )
                books_data = scraper.run()
                self.process_books_data(books_data)
            except Exception as e:
                print(f"Error occurred: {e}")
            finally:
                time.sleep(self.interval_seconds)


if __name__ == "__main__":
    VoebbScraperHomeAssistant().run()
