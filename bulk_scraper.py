"""
LinkedIn Bulk Profile Scraper
Robust scraper with anti-detection features for bulk scraping.
"""

import json
import os
import random
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from scraper import LinkedInScraper, extract_public_id

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# Anti-detection settings
class ScraperConfig:
    # Delay settings (in seconds)
    MIN_DELAY = 5          # Minimum delay between requests
    MAX_DELAY = 15         # Maximum delay between requests

    # Batch settings
    BATCH_SIZE = 25        # Profiles per batch before long break
    BATCH_BREAK_MIN = 40   # Minimum break between batches (seconds)
    BATCH_BREAK_MAX = 60   # Maximum break between batches (seconds)

    # Session health
    SESSION_CHECK_INTERVAL = 50    # Check session health every N profiles
    MAX_CONSECUTIVE_ERRORS = 5     # Stop after N consecutive errors

    # Retry settings
    MAX_RETRIES = 3
    RETRY_DELAY_MIN = 30
    RETRY_DELAY_MAX = 60

    # Output
    OUTPUT_DIR = "output"
    PROGRESS_FILE = "progress.json"


def random_delay(min_sec: float = None, max_sec: float = None):
    """Sleep for a random duration to mimic human behavior."""
    min_sec = min_sec or ScraperConfig.MIN_DELAY
    max_sec = max_sec or ScraperConfig.MAX_DELAY

    # Add some randomness - occasionally take longer breaks
    if random.random() < 0.1:  # 10% chance of longer delay
        max_sec *= 2

    delay = random.uniform(min_sec, max_sec)
    logger.debug(f"Sleeping for {delay:.1f} seconds...")
    time.sleep(delay)


def load_progress(output_dir: str) -> dict:
    """Load progress from previous run."""
    progress_file = Path(output_dir) / ScraperConfig.PROGRESS_FILE
    if progress_file.exists():
        with open(progress_file, 'r') as f:
            return json.load(f)
    return {
        'completed': [],
        'failed': [],
        'last_index': 0,
        'started_at': datetime.now().isoformat(),
    }


def save_progress(output_dir: str, progress: dict):
    """Save progress to file."""
    progress_file = Path(output_dir) / ScraperConfig.PROGRESS_FILE
    progress['updated_at'] = datetime.now().isoformat()
    with open(progress_file, 'w') as f:
        json.dump(progress, f, indent=2)


def save_profile(output_dir: str, profile: dict, public_id: str):
    """Save individual profile to file immediately."""
    profiles_dir = Path(output_dir) / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)

    # Save individual profile
    profile_file = profiles_dir / f"{public_id}.json"
    with open(profile_file, 'w', encoding='utf-8') as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)

    logger.debug(f"Saved profile: {profile_file}")


def save_all_results(output_dir: str, results: list):
    """Save all results to a combined file."""
    output_file = Path(output_dir) / "all_profiles.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved all results to: {output_file}")


def check_session_health(scraper: LinkedInScraper) -> bool:
    """Check if the session is still valid."""
    try:
        # Try to access the /me endpoint
        res = scraper.session.get(
            "https://www.linkedin.com/voyager/api/me",
            timeout=10
        )
        if res.status_code == 200:
            return True
        logger.warning(f"Session health check failed: {res.status_code}")
        return False
    except Exception as e:
        logger.warning(f"Session health check error: {e}")
        return False


def scrape_with_retry(scraper: LinkedInScraper, public_id: str) -> Optional[dict]:
    """Scrape a profile with retry logic."""
    for attempt in range(ScraperConfig.MAX_RETRIES):
        try:
            profile = scraper.get_profile(public_id)
            return profile
        except Exception as e:
            error_msg = str(e)

            # Check for specific errors
            if "403" in error_msg and "can't be accessed" in error_msg:
                # Profile is private/restricted - don't retry
                logger.warning(f"Profile restricted: {public_id}")
                return {'error': 'Profile restricted', 'public_id': public_id}

            if "429" in error_msg or "rate" in error_msg.lower():
                # Rate limited - take a longer break
                logger.warning(f"Rate limited! Taking extended break...")
                time.sleep(random.uniform(120, 300))

            if attempt < ScraperConfig.MAX_RETRIES - 1:
                delay = random.uniform(
                    ScraperConfig.RETRY_DELAY_MIN,
                    ScraperConfig.RETRY_DELAY_MAX
                )
                logger.info(f"Retry {attempt + 1}/{ScraperConfig.MAX_RETRIES} for {public_id} in {delay:.0f}s")
                time.sleep(delay)
            else:
                logger.error(f"Failed after {ScraperConfig.MAX_RETRIES} attempts: {public_id}")
                return {'error': str(e), 'public_id': public_id}

    return None


def bulk_scrape(
    li_at_cookie: str,
    jsessionid_cookie: str,
    profile_urls: list[str],
    output_dir: str = None,
    resume: bool = True
) -> dict:
    """
    Bulk scrape LinkedIn profiles with anti-detection measures.

    Args:
        li_at_cookie: LinkedIn li_at cookie
        jsessionid_cookie: LinkedIn JSESSIONID cookie
        profile_urls: List of profile URLs or public IDs to scrape
        output_dir: Output directory for results
        resume: Whether to resume from previous progress

    Returns:
        Dictionary with results and statistics
    """
    output_dir = output_dir or ScraperConfig.OUTPUT_DIR
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Load or initialize progress
    progress = load_progress(output_dir) if resume else {
        'completed': [],
        'failed': [],
        'last_index': 0,
        'started_at': datetime.now().isoformat(),
    }

    # Filter out already completed profiles and deduplicate
    completed_ids = set(progress['completed'])
    failed_ids = set(progress['failed'])

    profiles_to_scrape = []
    seen_ids = set()  # Track IDs we've already added to avoid duplicates

    for url in profile_urls:
        public_id = extract_public_id(url)

        # Skip if already completed or already in our scrape list
        if public_id in completed_ids:
            continue
        if public_id in seen_ids:
            continue

        profiles_to_scrape.append((url, public_id))
        seen_ids.add(public_id)

    total_urls = len(profile_urls)
    unique_total = len(seen_ids) + len(completed_ids)
    duplicates_removed = total_urls - len(seen_ids) - len(completed_ids)
    remaining = len(profiles_to_scrape)
    already_done = len(completed_ids)
    total = unique_total  # Total unique profiles

    logger.info(f"=" * 60)
    logger.info(f"LinkedIn Bulk Scraper")
    logger.info(f"=" * 60)
    logger.info(f"URLs in file: {total_urls}")
    logger.info(f"Duplicates removed: {duplicates_removed}")
    logger.info(f"Unique profiles: {unique_total}")
    logger.info(f"Already completed: {already_done}")
    logger.info(f"To scrape: {remaining}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"=" * 60)

    if remaining == 0:
        logger.info("All profiles already scraped!")
        return {'completed': len(completed_ids), 'failed': len(failed_ids)}

    # Initialize scraper
    logger.info("Initializing LinkedIn session...")
    scraper = LinkedInScraper(li_at_cookie, jsessionid_cookie)

    # Verify session
    if not check_session_health(scraper):
        logger.error("Session is not valid! Check your cookies.")
        return {'error': 'Invalid session'}

    logger.info("Session verified. Starting scrape...\n")

    # Scraping loop
    results = []
    consecutive_errors = 0

    for i, (url, public_id) in enumerate(profiles_to_scrape):
        current = already_done + i + 1

        # Batch break
        if i > 0 and i % ScraperConfig.BATCH_SIZE == 0:
            batch_break = random.uniform(
                ScraperConfig.BATCH_BREAK_MIN,
                ScraperConfig.BATCH_BREAK_MAX
            )
            logger.info(f"\n{'='*40}")
            logger.info(f"Batch complete. Taking {batch_break:.0f}s break...")
            logger.info(f"Progress: {current}/{total} ({current/total*100:.1f}%)")
            logger.info(f"{'='*40}\n")
            time.sleep(batch_break)

        # Session health check
        if i > 0 and i % ScraperConfig.SESSION_CHECK_INTERVAL == 0:
            if not check_session_health(scraper):
                logger.error("Session expired! Stopping scrape.")
                break

        # Scrape profile
        logger.info(f"[{current}/{total}] Scraping: {public_id}")

        profile = scrape_with_retry(scraper, public_id)

        if profile and 'error' not in profile:
            # Success
            save_profile(output_dir, profile, public_id)
            progress['completed'].append(public_id)
            results.append(profile)
            consecutive_errors = 0
            logger.info(f"  ✓ Success: {profile.get('fullName', 'Unknown')}")
        else:
            # Failed
            progress['failed'].append(public_id)
            if profile:
                results.append(profile)
            consecutive_errors += 1
            logger.warning(f"  ✗ Failed: {profile.get('error', 'Unknown error') if profile else 'No response'}")

        # Save progress after each profile
        progress['last_index'] = already_done + i + 1
        save_progress(output_dir, progress)

        # Check consecutive errors
        if consecutive_errors >= ScraperConfig.MAX_CONSECUTIVE_ERRORS:
            logger.error(f"Too many consecutive errors ({consecutive_errors}). Stopping.")
            break

        # Random delay before next request
        if i < len(profiles_to_scrape) - 1:
            random_delay()

    # Save final results
    save_all_results(output_dir, results)

    # Summary
    completed = len(progress['completed'])
    failed = len(progress['failed'])

    logger.info(f"\n{'='*60}")
    logger.info(f"SCRAPING COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Completed: {completed}/{total}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Success rate: {completed/(completed+failed)*100:.1f}%" if (completed+failed) > 0 else "N/A")
    logger.info(f"Results saved to: {output_dir}/")
    logger.info(f"{'='*60}")

    return {
        'completed': completed,
        'failed': failed,
        'success_rate': completed / (completed + failed) if (completed + failed) > 0 else 0
    }


def main():
    """Main entry point for bulk scraper."""
    import argparse

    parser = argparse.ArgumentParser(description='Bulk LinkedIn Profile Scraper')
    parser.add_argument('--config', '-c', required=True, help='Config JSON file with cookies')
    parser.add_argument('--input', '-i', required=True, help='File with profile URLs (one per line)')
    parser.add_argument('--output', '-o', default='output', help='Output directory')
    parser.add_argument('--no-resume', action='store_true', help='Start fresh, ignore previous progress')

    # Override settings
    parser.add_argument('--min-delay', type=float, help='Minimum delay between requests (seconds)')
    parser.add_argument('--max-delay', type=float, help='Maximum delay between requests (seconds)')
    parser.add_argument('--batch-size', type=int, help='Profiles per batch before long break')

    args = parser.parse_args()

    # Load config
    with open(args.config, 'r') as f:
        config = json.load(f)

    li_at = config.get('li_at_cookie')
    jsessionid = config.get('jsessionid_cookie')

    if not li_at or not jsessionid:
        logger.error("Config must contain 'li_at_cookie' and 'jsessionid_cookie'")
        return

    # Load profile URLs
    with open(args.input, 'r') as f:
        profile_urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    if not profile_urls:
        logger.error("No profile URLs found in input file")
        return

    # Override settings if provided
    if args.min_delay:
        ScraperConfig.MIN_DELAY = args.min_delay
    if args.max_delay:
        ScraperConfig.MAX_DELAY = args.max_delay
    if args.batch_size:
        ScraperConfig.BATCH_SIZE = args.batch_size

    # Run scraper
    bulk_scrape(
        li_at_cookie=li_at,
        jsessionid_cookie=jsessionid,
        profile_urls=profile_urls,
        output_dir=args.output,
        resume=not args.no_resume
    )


if __name__ == "__main__":
    main()
