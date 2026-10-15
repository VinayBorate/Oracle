"""
Notifications: Telegram (primary) and optional email.
"""

import os
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import httpx
from loguru import logger


def format_telegram_message(job: dict, result: dict) -> str:
    title = job["title"]
    company = job["company"]
    location = job["location"]
    remote = job.get("remote_type", "")
    posted = job["posted_on"]
    score = result["score"]
    reason = result["reason"]

    remote_badge = ""
    if remote == "Fully Remote":
        remote_badge = "🏠 Remote"
    elif remote == "Hybrid":
        remote_badge = "🔀 Hybrid"

    location_display = location
    if remote_badge:
        location_display += f" · {remote_badge}"

    msg = (
        f"🎯 *New Job Match!*\n\n"
        f"*{title}*\n"
        f"🏢 {company}\n"
        f"📍 {location_display}\n"
        f"📅 {posted}\n"
        f"🤖 Match Score: *{score}/100*\n"
        f"💡 _{reason}_\n\n"
        f"🔗 [Apply Now]({job['url']})"
    )
    return msg


async def send_telegram(token: str, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


async def notify_telegram(matches: list[tuple[dict, dict]], config: dict):
    if not config["notifications"]["telegram"]["enabled"]:
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.error("Telegram credentials missing.")
        return

    for job, result in matches:
        msg = format_telegram_message(job, result)
        success = await send_telegram(token, chat_id, msg)
        if success:
            logger.info(f"Telegram notification sent for {job['title']} ({job['id']})")
        else:
            logger.error(f"Failed to send Telegram for {job['id']}")
        await asyncio.sleep(2)


def build_email_html(matches: list[tuple[dict, dict]]) -> str:
    rows = ""
    for job, result in matches:
        rows += f"""
        <div style="border:1px solid #ccc; margin:10px 0; padding:10px; border-radius:5px;">
            <h3>{job['title']}</h3>
            <p><b>Company:</b> {job['company']}</p>
            <p><b>Location:</b> {job['location']} {job.get('remote_type', '')}</p>
            <p><b>Posted:</b> {job['posted_on']}</p>
            <p><b>Match Score:</b> {result['score']}/100</p>
            <p><b>Reason:</b> {result['reason']}</p>
            <p><a href="{job['url']}">Apply Now</a></p>
        </div>
        """
    return f"""
    <html>
    <head><style>body {{ font-family: Arial, sans-serif; }}</style></head>
    <body>
        <h2>New Oracle Job Matches</h2>
        <p>Found {len(matches)} matching jobs on {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
        {rows}
    </body>
    </html>
    """


def send_email(matches: list[tuple[dict, dict]], config: dict):
    if not config["notifications"]["email"]["enabled"]:
        return

    smtp_server = config["notifications"]["email"]["smtp_server"]
    smtp_port = config["notifications"]["email"]["smtp_port"]
    sender = config["notifications"]["email"]["sender_email"]
    recipient = config["notifications"]["email"]["recipient_email"]
    password = os.getenv("GMAIL_APP_PASSWORD")

    if not password:
        logger.error("GMAIL_APP_PASSWORD not set.")
        return

    subject = f"🎯 {len(matches)} New Job Match(es) Found — {datetime.now().strftime('%Y-%m-%d')}"
    html = build_email_html(matches)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.info(f"Email sent with {len(matches)} matches.")
    except Exception as e:
        logger.error(f"Email sending failed: {e}")


async def notify_all(matches: list[tuple[dict, dict]], config: dict):
    await notify_telegram(matches, config)
    if config["notifications"]["email"]["enabled"]:
        send_email(matches, config)


def test_telegram(token: str, chat_id: str) -> bool:
    message = "✅ *Oracle Job Notifier is online!*\n\nYour bot is configured correctly."
    success = asyncio.run(send_telegram(token, chat_id, message))
    if success:
        logger.info("Telegram test message sent successfully.")
    else:
        logger.error("Telegram test message failed.")
    return success