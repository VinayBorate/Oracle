"""
AI job filtering – focuses on experience (50%) and technology groups (50%).
Recency (max_days_old) is used as a bonus/penalty factor.
Supports both AI mode (Ollama) and keyword matching mode (fast).
No skip_list needed – handled by scraper pre-filters.
"""

import json
import re
import asyncio
import httpx
from loguru import logger


# ============================================================================
# KEYWORD MATCHING MODE (Fast, no AI)
# ============================================================================

def keyword_score_job(job: dict, filters: dict) -> dict | None:
    title = job.get("title", "").lower()
    
    exp_keywords = filters.get("experience_levels", [])
    exp_score = 0
    if exp_keywords:
        for exp in exp_keywords:
            if exp.lower() in title:
                exp_score = 50
                break
    if exp_score == 0:
        generic_words = ["software", "engineer", "developer", "programmer", "sde", "swe"]
        if any(word in title for word in generic_words):
            exp_score = 25
    
    tech_groups = filters.get("technology_groups", [])
    tech_score = 0
    if tech_groups:
        group_score = 50 // len(tech_groups)
        for group in tech_groups:
            group_matched = False
            for tech in group:
                if tech.lower() in title:
                    group_matched = True
                    break
            if group_matched:
                tech_score += group_score
    
    total_score = exp_score + tech_score
    
    posted = job.get("posted_on", "").lower()
    recency_bonus = 0
    if "today" in posted:
        recency_bonus = 10
    elif "yesterday" in posted:
        recency_bonus = 5
    
    total_score += recency_bonus
    
    min_score = filters.get("min_confidence_score", 70)
    match = total_score >= min_score
    
    return {
        "match": match,
        "score": total_score,
        "reason": f"Exp: {exp_score}/50, Tech: {tech_score}/50, Recent: +{recency_bonus}",
        "title_match": exp_score > 0,
        "location_match": True,
        "tech_match": tech_score > 0,
        "experience_match": exp_score > 0,
    }


async def keyword_match_jobs(
    jobs: list[dict],
    filters: dict,
    on_match_callback=None
) -> list[tuple[dict, dict]]:
    matches = []
    total = len(jobs)
    
    for idx, job in enumerate(jobs, 1):
        job_id = job.get("id", "UNKNOWN")
        job_title = job.get("title", "UNKNOWN")
        job_url = job.get("url", "UNKNOWN")
        company = job.get("company", "UNKNOWN")
        
        logger.debug(f"[Keyword] Evaluating {idx}/{total}: {job_title} at {company} [ID: {job_id}]")
        
        result = keyword_score_job(job, filters)
        
        if result and result["match"]:
            logger.info(f"✅ MATCH (Keyword): {job_title} at {company} [ID: {job_id}] - Score: {result['score']} - URL: {job_url}")
            matches.append((job, result))
            if on_match_callback:
                await on_match_callback(job, result)
        elif result:
            logger.debug(f"❌ NO MATCH (Keyword): {job_title} at {company} [ID: {job_id}] - Score: {result['score']} - URL: {job_url}")
        else:
            logger.warning(f"⚠️ SKIPPED (Keyword): {job_title} at {company} [ID: {job_id}] - URL: {job_url}")
    
    return matches


# ============================================================================
# AI MODE (Ollama - slower but smarter)
# ============================================================================

def build_prompt(job: dict, filters: dict) -> str:
    exp_list = filters.get("experience_levels", [])
    exp_text = ", ".join(exp_list) if exp_list else "any"

    tech_groups = filters.get("technology_groups", [])
    group_texts = []
    for i, group in enumerate(tech_groups, 1):
        group_texts.append(f"Group {i}: {' or '.join(group)}")
    tech_requirements = "\n".join(group_texts) if group_texts else "no technology requirements"

    max_days = filters.get("max_days_old", 0)
    recency_text = f"Jobs older than {max_days} days should receive a penalty (reduce score by up to 20 points)" if max_days > 0 else "No recency penalty"

    return f"""
You are an expert job matcher. Score the following job based **only** on:

1. **Experience match** (50% weight) – the job title/description should imply an experience level that matches one of these: {exp_text}. If not, score low.

2. **Technology match** (50% weight) – the job must mention at least one technology from **each** of the following groups:
{tech_requirements}

3. **Recency** – {recency_text}. Apply this as a penalty if the job is older than {max_days} days.

Your score must be between 0 and 100. Higher is better.

JOB DETAILS:
- Title: {job.get('title')}
- Company: {job.get('company')}
- Location: {job.get('location')}
- Remote type: {job.get('remote_type')}
- Posted: {job.get('posted_on')}

Compute a final score out of 100, with experience contributing 50 points and technology contributing 50 points, then adjust for recency.

Respond ONLY with this JSON (no markdown, no explanation):
{{"score": <number>, "reason": "<brief explanation>"}}
"""


def parse_ollama_response(response_text: str) -> dict | None:
    text = response_text.strip()
    text = re.sub(r"^```json\s*|```$", "", text, flags=re.MULTILINE)
    text = text.strip()
    try:
        data = json.loads(text)
        if "score" in data:
            return data
        return None
    except json.JSONDecodeError:
        match = re.search(r'"score"\s*:\s*(\d+)', text)
        if match:
            score = int(match.group(1))
            return {"score": score, "reason": "Parsed from raw text"}
        return None


async def evaluate_job(job: dict, filters: dict, ollama_config: dict) -> dict | None:
    model = ollama_config["model"]
    base_url = ollama_config["base_url"]
    timeout = ollama_config.get("timeout_seconds", 600)
    min_score = filters.get("min_confidence_score", 70)

    prompt = build_prompt(job, filters)

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 256, "temperature": 0.0}
    }

    job_id = job.get("id", "UNKNOWN")
    job_title = job.get("title", "UNKNOWN")
    job_url = job.get("url", "UNKNOWN")

    try:
        job_timeout = min(timeout, 120)

        async with httpx.AsyncClient(timeout=job_timeout) as client:
            resp = await client.post(
                f"{base_url}/api/generate",
                json=payload
            )
            resp.raise_for_status()
            result = resp.json()
            response_text = result.get("response", "")
            parsed = parse_ollama_response(response_text)

            if parsed is None:
                logger.warning(f"Failed to parse response for job {job_id} ({job_title}): {response_text[:200]}")
                return None

            score = parsed.get("score", 0)
            match = score >= min_score
            reason = parsed.get("reason", "")

            return {
                "match": match,
                "score": score,
                "reason": reason,
                "job_id": job_id,
                "job_url": job_url,
            }
    except httpx.TimeoutException:
        logger.error(f"⏰ TIMEOUT: Job {job_id} ({job_title}) - URL: {job_url}")
        return None
    except Exception as e:
        logger.error(f"❌ ERROR: Job {job_id} ({job_title}) - URL: {job_url} - Error: {e}")
        return None


async def filter_jobs(
    jobs: list[dict],
    filters: dict,
    ollama_config: dict,
    on_match_callback=None,
    max_concurrent: int = 1
) -> list[tuple[dict, dict]]:
    matches = []
    total = len(jobs)
    semaphore = asyncio.Semaphore(max_concurrent)

    async def evaluate_with_semaphore(job, idx):
        nonlocal matches
        job_id = job.get("id", "UNKNOWN")
        job_title = job.get("title", "UNKNOWN")
        job_url = job.get("url", "UNKNOWN")
        company = job.get("company", "UNKNOWN")

        logger.debug(f"[AI] Evaluating {idx}/{total}: {job_title} at {company} [ID: {job_id}]")

        async with semaphore:
            result = await evaluate_job(job, filters, ollama_config)

            if result and result["match"]:
                logger.info(f"✅ MATCH (AI): {job_title} at {company} [ID: {job_id}] - Score: {result['score']} - URL: {job_url}")
                matches.append((job, result))
                if on_match_callback:
                    await on_match_callback(job, result)
            elif result:
                logger.debug(f"❌ NO MATCH (AI): {job_title} at {company} [ID: {job_id}] - Score: {result['score']} - URL: {job_url}")
            else:
                logger.warning(f"⚠️ SKIPPED (AI): {job_title} at {company} [ID: {job_id}] - URL: {job_url}")

    tasks = [
        evaluate_with_semaphore(job, idx + 1)
        for idx, job in enumerate(jobs)
    ]
    await asyncio.gather(*tasks)

    return matches