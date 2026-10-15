"""
Scheduler using APScheduler to run the job check periodically.
Supports AI toggle - when disabled, sends ALL jobs that pass pre-filters.
Thread-safe: creates new DB connection in each thread.
"""

import asyncio
import sqlite3
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from scraper import scrape_all
from filter import filter_jobs, keyword_match_jobs
from notifier import notify_all
from db import init_db, is_job_seen, mark_job_seen, log_matched_job


async def run_check(config: dict):
    logger.info("=" * 60)
    logger.info("RUN STARTING")
    logger.info("=" * 60)

    companies = config["companies"]
    scraper_config = config["scraper"]
    filters = config["filters"]
    ai_config = config.get("ai", {"enabled": False})

    conn = init_db()
    logger.info("Database connection opened for this run.")

    try:
        logger.info(f"Scraping {len(companies)} companies...")
        try:
            all_jobs = await scrape_all(companies, scraper_config)
        except Exception as e:
            logger.error(f"Scraping failed: {e}")
            return

        logger.info(f"Total jobs fetched after pre‑filters: {len(all_jobs)}")

        new_jobs = []
        for job in all_jobs:
            if not is_job_seen(conn, job["id"]):
                new_jobs.append(job)

        logger.info(f"New (unseen) jobs to evaluate: {len(new_jobs)}")

        if not new_jobs:
            logger.info("No new jobs. Run complete.")
            return

        ai_enabled = ai_config.get("enabled", False)

        async def on_match(job, result):
            logger.info(f"✅ SENDING: {job['title']} at {job['company']}")
            await notify_all([(job, result)], config)
            log_matched_job(conn, job, result)

        if ai_enabled:
            logger.info("🤖 AI mode: ENABLED - Using AI for job scoring")
            ollama_config = {
                "model": ai_config.get("model", "gemma3:4b"),
                "base_url": ai_config.get("base_url", "http://localhost:11434"),
                "timeout_seconds": ai_config.get("timeout_seconds", 400)
            }
            matches = await filter_jobs(
                new_jobs,
                filters,
                ollama_config,
                on_match_callback=on_match,
                max_concurrent=1
            )
        else:
            logger.info(f"⚡ NO SCORING MODE: Sending all {len(new_jobs)} jobs to notifications")
            matches = []
            for job in new_jobs:
                result = {
                    "match": True,
                    "score": 100,
                    "reason": "All jobs pass in no-scoring mode",
                    "title_match": True,
                    "location_match": True,
                    "tech_match": True,
                    "experience_match": True,
                }
                matches.append((job, result))
                await on_match(job, result)

        for job in new_jobs:
            mark_job_seen(conn, job)

        logger.info("=" * 60)
        logger.info(f"RUN COMPLETE. Total jobs sent: {len(matches)}")
        logger.info("=" * 60)

    finally:
        conn.close()
        logger.info("Database connection closed.")


def start_scheduler(config: dict):
    scheduler = BlockingScheduler()
    interval = config["scheduler"]["interval_minutes"]

    scheduler.add_job(
        func=lambda: asyncio.run(run_check(config)),
        trigger=IntervalTrigger(minutes=interval),
        max_instances=1,
        misfire_grace_time=300,
        id="job_check",
        name="Oracle job check"
    )

    logger.info(f"Scheduler started. Will run every {interval} minutes.")
    logger.info(f"Monitoring {len(config['companies'])} companies.")
    ai_enabled = config.get("ai", {}).get("enabled", False)
    logger.info(f"AI mode: {'ENABLED' if ai_enabled else 'DISABLED (ALL jobs sent)'}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")