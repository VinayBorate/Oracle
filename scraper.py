"""
Scrapes Oracle HCM job APIs using GET requests with pagination.
Supports pre-filters with operators: contains_any, contains_all, contains_none
"""

import asyncio
import httpx
from urllib.parse import urlparse
from loguru import logger


def job_passes_prefilters(job: dict, pre_filters: list) -> bool:
    if not pre_filters:
        return True
    
    for cond in pre_filters:
        field = cond.get("field")
        operator = cond.get("operator", "contains_any")
        values = cond.get("values", [])
        
        if not field or not values:
            continue
            
        field_value = job.get(field, "")
        field_lower = field_value.lower()
        
        if operator == "contains_any":
            if not any(v.lower() in field_lower for v in values):
                return False
        elif operator == "contains_all":
            if not all(v.lower() in field_lower for v in values):
                return False
        elif operator == "contains_none":
            if any(v.lower() in field_lower for v in values):
                return False
                
    return True


def build_oracle_url(api_base: str, site_number: str, limit: int, offset: int) -> str:
    """
    Build the exact URL expected by Oracle HCM REST API.
    All parameters (limit, offset, sortBy) are inside the 'finder' value.
    """
    # Remove any trailing slash
    base = api_base.rstrip('/')
    
    # The expand list – keep commas raw (do not encode)
    expand = "requisitionList.workLocation,requisitionList.otherWorkLocations,requisitionList.secondaryLocations,flexFieldsFacet.values,requisitionList.requisitionFlexFields"
    
    # The finder string – includes siteNumber, facetsList, limit, sortBy, offset
    facets = "LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS"
    finder = f"findReqs;siteNumber={site_number},facetsList={facets},limit={limit},sortBy=POSTING_DATES_DESC,offset={offset}"
    
    # Build the full URL
    url = f"{base}?onlyData=true&expand={expand}&finder={finder}"
    return url


async def scrape_company(
    client: httpx.AsyncClient,
    company: dict,
    scraper_config: dict,
    pre_filters: list = None
) -> list[dict]:
    """
    Fetch jobs from Oracle API for a single company.
    """
    all_jobs = []
    api_base = company["api_base"]
    base_url = company["base_url"]
    site_number = company["site_number"]
    company_name = company["name"]
    
    # Override limit per company if specified
    limit = company.get("limit_per_request", scraper_config.get("limit_per_request", 200))
    jobs_to_fetch = scraper_config.get("jobs_to_fetch", 40)
    max_pages = scraper_config.get("max_pages_per_company", 2)
    timeout = scraper_config.get("request_timeout_seconds", 30)
    delay = scraper_config.get("delay_between_pages", 1)
    enable_prefilter = scraper_config.get("enable_prefilter", False)
    
    offset = 0
    page = 1
    fetched = 0
    
    # Headers – some APIs require a proper User-Agent and Accept
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    while fetched < jobs_to_fetch and page <= max_pages:
        url = build_oracle_url(api_base, site_number, limit, offset)
        logger.debug(f"[{company_name}] Fetching page {page} (offset={offset}, limit={limit})")
        
        try:
            resp = await client.get(url, headers=headers, timeout=timeout, follow_redirects=False)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[{company_name}] HTTP error at offset {offset}: {e.response.status_code} - {e.response.text[:200]}")
            break
        except Exception as e:
            logger.error(f"[{company_name}] Request failed at offset {offset}: {e}")
            break
        
        items = data.get("items", [])
        if not items:
            break
        
        item = items[0]
        requisitions = item.get("requisitionList", [])
        if not requisitions:
            break
        
        for req in requisitions:
            job_id = req.get("Id")
            if not job_id:
                continue
            title = req.get("Title", "")
            posted_date = req.get("PostedDate", "")
            location = req.get("PrimaryLocation", "")
            if not location:
                work_locs = req.get("workLocation", [])
                if work_locs:
                    loc = work_locs[0]
                    town = loc.get("TownOrCity", "")
                    region = loc.get("Region2", "")
                    country = loc.get("Country", "")
                    if town and region and country:
                        location = f"{town}, {region}, {country}"
                    elif town and country:
                        location = f"{town}, {country}"
                    else:
                        location = loc.get("LocationName", "")
            
            remote_type = req.get("WorkplaceTypeCode", "")
            
            # Construct job URL: base_url + "/jobs/preview/" + job_id
            if not base_url.endswith("/"):
                base_url += "/"
            job_url = f"{base_url}jobs/preview/{job_id}"
            
            job = {
                "id": job_id,
                "title": title,
                "location": location,
                "posted_on": posted_date,
                "remote_type": remote_type,
                "url": job_url,
                "company": company_name,
            }
            
            if enable_prefilter and pre_filters:
                if not job_passes_prefilters(job, pre_filters):
                    logger.debug(f"[{company_name}] Skipped (pre-filter): {job['title']} [ID: {job_id}]")
                    continue
            
            all_jobs.append(job)
            fetched += 1
            if fetched >= jobs_to_fetch:
                break
        
        total_jobs = data.get("items", [{}])[0].get("TotalJobsCount", 0)
        offset += limit
        page += 1
        if offset >= total_jobs or fetched >= jobs_to_fetch:
            break
        
        await asyncio.sleep(delay)
    
    logger.info(f"[{company_name}] Fetched {len(all_jobs)} jobs after pre‑filters (target: {jobs_to_fetch}).")
    return all_jobs


async def scrape_all(companies: list, scraper_config: dict) -> list[dict]:
    all_jobs = []
    delay_between = scraper_config.get("delay_between_companies", 2)
    pre_filters = scraper_config.get("pre_filters", [])
    
    async with httpx.AsyncClient() as client:
        for company in companies:
            try:
                jobs = await scrape_company(
                    client,
                    company,
                    scraper_config,
                    pre_filters=pre_filters if scraper_config.get("enable_prefilter", False) else None
                )
                all_jobs.extend(jobs)
            except Exception as e:
                logger.error(f"Error scraping {company.get('name', 'unknown')}: {e}")
            await asyncio.sleep(delay_between)
    
    return all_jobs