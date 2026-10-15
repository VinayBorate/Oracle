#!/usr/bin/env python3
"""
Oracle Job Notifier – Main entry point.
"""

import os
import sys
import asyncio
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

from scheduler import run_check, start_scheduler
from logger import setup_logging
from notifier import test_telegram


def load_config() -> dict:
    config_path = Path("config.yaml")
    if not config_path.exists():
        logger.error("config.yaml not found.")
        sys.exit(1)
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def validate_env(config: dict):
    if config["notifications"]["telegram"]["enabled"]:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
            sys.exit(1)
        try:
            test_telegram(token, chat_id)
        except Exception as e:
            logger.error(f"Telegram test failed: {e}")
            sys.exit(1)

    if config["notifications"]["email"]["enabled"]:
        if not os.getenv("GMAIL_APP_PASSWORD"):
            logger.error("GMAIL_APP_PASSWORD must be set in .env for email notifications")
            sys.exit(1)


def check_ollama(config: dict):
    import httpx
    
    ai_config = config.get("ai", {})
    if not ai_config.get("enabled", False):
        logger.info("AI mode is disabled. Skipping Ollama check.")
        return
    
    model = ai_config.get("model", "gemma3:4b")
    base_url = ai_config.get("base_url", "http://localhost:11434")

    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        if model not in models:
            logger.error(f"Model '{model}' not found. Run: ollama pull {model}")
            sys.exit(1)
        logger.info(f"Ollama is ready (model: {model})")
    except Exception as e:
        logger.error(f"Ollama not reachable at {base_url}: {e}")
        logger.error("Please start Ollama with: ollama serve")
        sys.exit(1)


def print_banner(config: dict):
    n = len(config["companies"])
    interval = config["scheduler"]["interval_minutes"]
    ai_config = config.get("ai", {})
    model = ai_config.get("model", "gemma3:4b")
    ai_enabled = ai_config.get("enabled", False)
    score = config["filters"]["min_confidence_score"]
    
    logger.info(
        f"\n╔══════════════════════════════════════════════╗\n"
        f"║        Oracle Job Notifier v1.0              ║\n"
        f"╠══════════════════════════════════════════════╣\n"
        f"║  Companies  : {n} configured                 ║\n"
        f"║  Interval   : Every {interval} minutes       ║\n"
        f"║  AI Mode    : {'ENABLED' if ai_enabled else 'DISABLED (ALL jobs sent)'}  ║\n"
        f"║  AI Model   : {model}                        ║\n"
        f"║  Notify via : Telegram                       ║\n"
        f"║  Min Score  : {score}/100                    ║\n"
        f"╚══════════════════════════════════════════════╝"
    )


def main():
    load_dotenv()
    setup_logging()
    config = load_config()
    validate_env(config)
    check_ollama(config)
    print_banner(config)

    if config["scheduler"].get("run_on_startup", True):
        logger.info("Running initial check on startup...")
        asyncio.run(run_check(config))

    start_scheduler(config)


if __name__ == "__main__":
    main()