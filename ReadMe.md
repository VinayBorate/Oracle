# Oracle Job Notifier

Monitor Oracle HCM job APIs (like JPMC, Oracle, etc.) for new software engineering jobs and get Telegram notifications.

## Features

- Scrapes multiple Oracle HCM career sites using their REST API.
- Configurable pre‑filters for location, title inclusion/exclusion.
- Keyword‑based scoring (fast) or optional AI (Ollama) for semantic matching.
- Sends Telegram notifications for matching jobs (with optional email).
- Deduplicates jobs using SQLite database.
- Runs on a schedule (default every 30 minutes).

## Setup

1. Clone the repository.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt