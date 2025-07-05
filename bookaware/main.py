import json
import os
import sys
import time
import shelve
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, TypedDict
from urllib.parse import urljoin

import paho.mqtt.client as mqtt
import requests
import select
import structlog
from bs4 import BeautifulSoup
import fcntl

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
            due_date = datetime.strptime(date_str, "%d.%m.%Y").date()
            library = tds[2].get_text(strip=True)
            title = " ".join(tds[3].stripped_strings)
            hint = tds[4].get_text(strip=True)
            items.append(
                {
                    "due_date": due_date.isoformat(),
                    "library": library,
                    "title": title,
                    "hint": hint,
                    "days_left": (due_date - date.today()).days,
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
        self.stdin_buffer = ""
        self.supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
        self.last_check = 0

        with open("/data/options.json") as f:
            self.config = json.load(f)

        # Set stdin to non-blocking mode
        fd = sys.stdin.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.logger.info("Have config", config=self.config)

        # Initialize the state storage using shelve
        self.state = shelve.open("/data/state.db")
        self.logger.info("Have state", state=dict(self.state))
        self.next_scrape = self.state.get("next_scrape", time.time())

        mqtt_service_info = {}
        for x in "host", "port", "username", "password":
            if f"mqtt_{x}" in self.config:
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

        # MQTT client setup
        self.client = mqtt.Client(protocol=mqtt.MQTTv5)
        # Configure LWT topic and payloads
        lwt_topic = f"{self.config['topic_prefix']}/availability"
        self.client.will_set(
            lwt_topic, payload="unavailable", qos=1, retain=True
        )
        self.connect_mqtt()

        self.interval_seconds = self.config["interval_hours"] * 60 * 60

    def connect_mqtt(self):
        """Configure and connect MQTT client with proper callbacks"""
        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        # Set credentials
        self.client.username_pw_set(self.mqtt_username, self.mqtt_password)

        # Enable automatic reconnection
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)

        try:
            self.client.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            self.logger.error("Failed to connect to MQTT broker", error=str(e))
            # Connection will be retried automatically

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """Callback for when the client receives a CONNACK response from the server"""
        if reason_code == 0:
            self.logger.info("Connected to MQTT broker")
            # Publish availability status
            lwt_topic = f"{self.config['topic_prefix']}/availability"
            self.client.publish(lwt_topic, payload="available", qos=1, retain=True)
            # Re-publish config in case we reconnected
            self.publish_config()
        else:
            self.logger.error("Failed to connect to MQTT broker", return_code=reason_code)

    def _on_disconnect(self, client, userdata, reason_code, properties):
        """Callback for when the client disconnects from the server"""
        if reason_code != 0:
            self.logger.warning("Unexpected MQTT disconnection", reason_code=reason_code)
        else:
            self.logger.info("Disconnected from MQTT broker")

    def publish_config(self):
        """Publish Home Assistant MQTT autodiscovery configuration topics for sensors"""
        base = self.config['topic_prefix']
        availability = {
            "topic": f"{base}/availability",
            "payload_available": "available",
            "payload_not_available": "unavailable",
            "value_template": "{{ value_json.state }}"
        }
        sensors = [
            {"id": "closest_due_date", "name": "Closest Due Date"},
            {"id": "books_due_soon", "name": "Books Due in 5 Days"},
            {"id": "books_open_total", "name": "Total Outstanding Books"},
        ]

        for sensor in sensors:
            topic = f"{base}/{sensor['id']}"
            config_topic = f"{topic}/config"
            payload = {
                "name": sensor['name'],
                "state_topic": f"{topic}/state",
                "unique_id": f"library_{sensor['id']}",
                "device": {"identifiers": ["library_books_tracker"], "name": "Library Books"},
                "availability": [availability]
            }
            if sensor['id'] == 'books_open_total':
                payload['json_attributes_topic'] = f"{base}/books_open_total/attributes"

            self.client.publish(config_topic, json.dumps(payload), retain=True)

    def process_books_data(self, books: List[BookEntry]):
        """Process scraped books data and publish states to HA"""
        # Calculate the closest due date
        due_soon_threshold = (datetime.now() + timedelta(days=5)).date().isoformat()

        books_due_within_5_days = 0
        closest_due_date: Optional[str] = None

        for book in books:
            # Update closest due date
            if not closest_due_date or book["due_date"] < closest_due_date:
                closest_due_date = book["due_date"]

            if book["due_date"] <= due_soon_threshold:
                books_due_within_5_days += 1

        # Publish state updates
        self.client.publish(
            f"{self.config['topic_prefix']}/closest_due_date/state",
            closest_due_date,
            retain=True,
        )
        self.client.publish(
            f"{self.config['topic_prefix']}/books_due_soon/state",
            books_due_within_5_days,
            retain=True,
        )
        self.client.publish(
            f"{self.config['topic_prefix']}/books_open_total/state", len(books),
            retain=True,
        )

        # Publish full list of books as a JSON attribute for the "total" sensor
        attributes_payload = {"books": books}
        self.client.publish(
            f"{self.config['topic_prefix']}/books_open_total/attributes",
            json.dumps(attributes_payload),
            retain=True,
        )

    def run(self):
        """Main loop to scrape and publish data"""
        self.publish_config()
        try:
            while True:
                try:
                    if self.should_scrape:
                        self.run_scrape()

                    # Wait at least 5 seconds
                    wait_time = max(self.next_scrape - time.time(), 5)
                    self.logger.info("Waiting", wait_time=wait_time)

                    ready, _, _ = select.select([sys.stdin], [], [], wait_time)

                    if ready:  # Check if stdin has data
                        # Read available data into the buffer
                        data = sys.stdin.read(1024)  # Read up to 1024 bytes (can adjust as needed)
                        self.stdin_buffer += data

                        # Process full lines from the buffer
                        while "\n" in self.stdin_buffer:  # Check if a full line exists
                            line, self.stdin_buffer = self.stdin_buffer.split("\n", 1)
                            line = line.strip()
                            if line:  # Only process non-empty lines
                                self.process_input(line)

                except Exception as e:
                    print(f"Error occurred: {e}")
        finally:
            # Close shelve storage
            self.state.close()

    def process_input(self, line: str):
        try:
            data = json.loads(line)
            self.logger.info("Received JSON input", data=data)
            if refresh_value := data.get("refresh", None):
                now_ = time.time()
                if refresh_value == "force":
                    self.state["next_scrape"] = now_
                else:
                    # Normal refresh only if at least 10 minutes have passed
                    if self.state.get("last_scrape", 0) < now_ - 600:
                        self.next_scrape = self.state["next_scrape"] = now_
                    else:
                        self.logger.info("Skipping refresh too soon")
        except json.JSONDecodeError as e:
            self.logger.error("Failed to decode JSON input", line=line, error=str(e))

    @property
    def should_scrape(self):
        now_ = time.time()
        if now_ >= self.next_scrape:
            self.logger.info("Scraping due")
            return True
        if now_ < self.state.get("last_scrape", 0) or now_ < self.last_check:
            # Clock moved backwards
            self.logger.info("Clock moved backwards, forcing scrape")
            return True
        self.last_check = now_
        return False

    def run_scrape(self):
        now_ = time.time()
        self.state["last_scrape"] = now_
        self.next_scrape = self.state["next_scrape"] = now_ + self.interval_seconds
        self.logger.info("Scheduling next scrape", now_=now_, next_scrape=self.state["next_scrape"])
        self.state.sync()
        # Run scraper and get outstanding books data
        scraper = VoebbScraper(
            username=self.config["username"], password=self.config["password"]
        )
        books_data = scraper.run()
        self.process_books_data(books_data)


if __name__ == "__main__":
    VoebbScraperHomeAssistant().run()
