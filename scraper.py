"""
LinkedIn Profile Scraper
Extracts detailed experience and education data from LinkedIn profiles.
Uses LinkedIn's Voyager API with cookie-based authentication.
"""

import json
import logging
import os
import random
import re
import sys
import time
from typing import Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from requests.cookies import RequestsCookieJar
from urllib3.util.retry import Retry


# API Configuration
LINKEDIN_BASE_URL = "https://www.linkedin.com"
API_BASE_URL = f"{LINKEDIN_BASE_URL}/voyager/api"
PROFILE_ENDPOINT = "/identity/dash/profiles"
DECORATION_ID = "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-109"

# Request timeouts: (connect seconds, read seconds)
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 30.0

REQUEST_HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "accept-language": "en-US,en;q=0.9",
    "x-li-lang": "en_US",
    "x-restli-protocol-version": "2.0.0",
}

log = logging.getLogger(__name__)


def _cookie_dict(jar: requests.cookies.RequestsCookieJar) -> dict:
    """Return a stable name→value snapshot of a cookie jar for diffing."""
    return {c.name: c.value for c in jar}


def _redact(value: str, keep: int = 6) -> str:
    """Partially redact a sensitive string for logging."""
    if not value:
        return value
    if len(value) < keep:
        return value
    return value[:keep] + "…" + value[-2:]


def extract_public_id(url_or_id: str) -> str:
    """Extract public identifier from LinkedIn URL or return as-is if already an ID."""
    url_or_id = url_or_id.strip()

    # Handle /in/username format (most common)
    match = re.search(r'linkedin\.com/in/([^/?]+)', url_or_id)
    if match:
        return match.group(1)

    # Handle old /pub/firstname-lastname/xx/xxx/xxx format
    match = re.search(r'linkedin\.com/pub/([^/?]+)', url_or_id)
    if match:
        return match.group(1)

    # Already a public ID (no URL)
    return url_or_id.strip('/')


def format_date(date_dict: Optional[dict]) -> Optional[str]:
    """Format LinkedIn date object to MM-YYYY or YYYY string."""
    if not date_dict:
        return None
    year = date_dict.get('year')
    month = date_dict.get('month')
    if year and month:
        return f"{month:02d}-{year}"
    elif year:
        return str(year)
    return None


def parse_date_range(date_range: Optional[dict]) -> tuple:
    """Parse date range into start and end date strings."""
    if not date_range:
        return None, None
    start = format_date(date_range.get('start'))
    end = format_date(date_range.get('end'))
    return start, end


def parse_experience(position_group: dict) -> list[dict]:
    """Parse a position group into experience entries."""
    experiences = []

    positions = position_group.get('profilePositionInPositionGroup', {}).get('elements', [])

    for position in positions:
        start_date, end_date = parse_date_range(position.get('dateRange'))

        company = position.get('company', {}) or {}
        company_urn = position.get('companyUrn')
        company_id = company_urn.split(':')[-1] if company_urn else None

        # Get industry from company
        industry = None
        industry_data = company.get('industry', {})
        if industry_data:
            first_industry = next(iter(industry_data.values()), {})
            industry = first_industry.get('name')

        # Get location
        location = position.get('locationName')
        geo_location = position.get('geoLocation', {}) or {}
        if not location and geo_location:
            location = geo_location.get('name')

        experiences.append({
            'title': position.get('title'),
            'companyName': position.get('companyName'),
            'companyUrn': company_urn,
            'companyId': company_id,
            'companyLinkedinUrl': f"https://www.linkedin.com/company/{company_id}/" if company_id else None,
            'companyIndustry': industry,
            'location': location,
            'description': position.get('description'),
            'startDate': start_date,
            'endDate': end_date,
            'isCurrentRole': end_date is None,
        })

    return experiences


def parse_education(education: dict) -> dict:
    """Parse a single education entry."""
    start_date, end_date = parse_date_range(education.get('dateRange'))

    school = education.get('school', {}) or {}
    school_urn = education.get('schoolUrn')
    school_id = school_urn.split(':')[-1] if school_urn else None

    return {
        'schoolName': education.get('schoolName') or school.get('name'),
        'schoolUrn': school_urn,
        'schoolId': school_id,
        'schoolLinkedinUrl': f"https://www.linkedin.com/school/{school_id}/" if school_id else None,
        'degreeName': education.get('degreeName'),
        'fieldOfStudy': education.get('fieldOfStudy'),
        'description': education.get('description'),
        'grade': education.get('grade'),
        'activities': education.get('activities'),
        'startDate': start_date,
        'endDate': end_date,
    }


class LinkedInScraper:
    """LinkedIn profile scraper using Voyager API."""

    def __init__(
        self,
        li_at_cookie: str,
        jsessionid_cookie: str,
        *,
        debug: bool = False,
        jitter_sleep_range: Tuple[float, float] = (0.0, 0.0),
        max_retries: int = 5,
        backoff_factor: float = 0.8,
    ):
        """
        Initialize scraper with LinkedIn session cookies.

        Args:
            li_at_cookie: The 'li_at' cookie value from your browser
            jsessionid_cookie: The 'JSESSIONID' cookie value from your browser
            debug: Enable verbose logging of retries, cookies diffs, and CSRF token.
                   Can also be enabled via the LINKEDIN_DEBUG=1 environment variable.
            jitter_sleep_range: (min, max) seconds to sleep before each request to
                                 reduce rate-limit / anti-bot triggers.  E.g. (1.0, 3.0).
            max_retries: Total retry attempts for transient network / 5xx errors.
            backoff_factor: Exponential backoff multiplier between retries.
        """
        self.debug = debug or (os.getenv("LINKEDIN_DEBUG", "0") == "1")
        self.jitter_sleep_range = jitter_sleep_range

        self.session = self._build_session(max_retries, backoff_factor)

        # Seed cookies once from the supplied credentials.
        # From here on the session cookie jar is updated automatically by
        # every Set-Cookie response header — we never overwrite it manually.
        cookies = RequestsCookieJar()
        cookies.set('li_at', li_at_cookie, domain='.linkedin.com', path='/')
        jsessionid_clean = jsessionid_cookie.strip('"')
        cookies.set('JSESSIONID', f'"{jsessionid_clean}"', domain='.linkedin.com', path='/')
        self.session.cookies.update(cookies)

        # Set base headers (csrf-token will be refreshed before each request)
        self.session.headers.update(REQUEST_HEADERS)

    @staticmethod
    def _build_session(max_retries: int, backoff_factor: float) -> requests.Session:
        """Create a requests.Session with retry/backoff and keep-alive pool."""
        session = requests.Session()

        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=10,
            pool_maxsize=10,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _sync_csrf_token(self) -> None:
        """
        Keep the csrf-token request header in sync with the current JSESSIONID cookie.

        LinkedIn expects csrf-token == JSESSIONID value (without surrounding quotes).
        This must be called before every outgoing request so that cookie rotations
        performed by a previous Set-Cookie response are reflected immediately.
        """
        jsessionid = self.session.cookies.get("JSESSIONID")
        if jsessionid:
            csrf = jsessionid.strip('"')
            self.session.headers["csrf-token"] = csrf
            if self.debug:
                log.debug("csrf-token synced → %s", _redact(csrf))

    def _fetch(self, endpoint: str, params: dict = None) -> dict:
        """
        Make a GET request to the LinkedIn Voyager API.

        Cookie update point
        -------------------
        requests.Session automatically merges every Set-Cookie response header
        into self.session.cookies *immediately after* session.get() returns.
        The cookie-diff log below captures exactly which cookies changed.
        """
        url = f"{API_BASE_URL}{endpoint}"

        # Optional jitter to reduce rate-limit / anti-bot triggers
        lo, hi = self.jitter_sleep_range
        if hi > 0 and 0 <= lo <= hi:
            time.sleep(random.uniform(lo, hi))

        # Sync csrf-token with the most up-to-date JSESSIONID before sending
        self._sync_csrf_token()

        # Snapshot cookies BEFORE the request (connection resets happen here;
        # we still want to log the pre-request state)
        before = _cookie_dict(self.session.cookies)

        if self.debug:
            log.debug("GET %s", url)
            if "JSESSIONID" in before:
                log.debug("pre-request JSESSIONID=%s", _redact(before["JSESSIONID"]))

        try:
            # Do NOT pass cookies= kwarg — let the session manage the jar.
            res = self.session.get(
                url,
                params=params,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
        except requests.RequestException as exc:
            # Connection reset (and other transport errors) land here.
            # There is no response, so no Set-Cookie to apply for this attempt.
            log.warning("request failed: %r", exc)
            raise

        # ← Cookie update has already happened here (requests merged Set-Cookie).
        after = _cookie_dict(self.session.cookies)

        if self.debug:
            changed = {
                k: (before.get(k), after.get(k))
                for k in set(before) | set(after)
                if before.get(k) != after.get(k)
            }
            if changed:
                log.debug(
                    "cookies changed via Set-Cookie → %s",
                    {k: (_redact(v0 or ""), _redact(v1 or "")) for k, (v0, v1) in changed.items()},
                )

        if res.status_code != 200:
            raise Exception(f"API request failed with status {res.status_code}: {res.text[:200]}")

        return res.json()

    def get_profile(self, public_id: str) -> dict:
        """
        Fetch a LinkedIn profile by public identifier.

        Args:
            public_id: LinkedIn public identifier (e.g., 'williamhgates')

        Returns:
            Structured profile data with experience and education
        """
        params = {
            'q': 'memberIdentity',
            'memberIdentity': public_id,
            'decorationId': DECORATION_ID,
        }

        data = self._fetch(PROFILE_ENDPOINT, params)

        if not data.get('elements'):
            raise Exception(f"Profile not found: {public_id}")

        profile = data['elements'][0]
        return self._parse_profile(profile, public_id)

    def _parse_profile(self, profile: dict, public_id: str) -> dict:
        """Parse raw profile data into structured format."""
        result = {
            'linkedinUrl': f"https://www.linkedin.com/in/{public_id}",
            'publicIdentifier': public_id,
            'firstName': profile.get('firstName'),
            'lastName': profile.get('lastName'),
            'fullName': f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip(),
            'headline': profile.get('headline'),
            'summary': profile.get('summary'),
            'location': None,
            'country': None,
            'industryName': None,
        }

        # Parse location
        location = profile.get('location', {}) or {}
        geo_location = profile.get('geoLocation', {}) or {}
        result['location'] = location.get('name') or geo_location.get('name')
        result['country'] = geo_location.get('country', {}).get('name') if geo_location.get('country') else None

        # Parse industry
        industry = profile.get('industry', {}) or {}
        if industry:
            result['industryName'] = industry.get('name')

        # Parse experiences
        experiences = []
        position_groups = profile.get('profilePositionGroups', {}).get('elements', [])
        for group in position_groups:
            experiences.extend(parse_experience(group))
        result['experiencesCount'] = len(experiences)
        result['experiences'] = experiences

        # Parse education
        educations = []
        education_elements = profile.get('profileEducations', {}).get('elements', [])
        for edu in education_elements:
            educations.append(parse_education(edu))
        result['educationsCount'] = len(educations)
        result['educations'] = educations

        # Parse skills
        skills = []
        skill_elements = profile.get('profileSkills', {}).get('elements', [])
        for skill in skill_elements:
            skills.append({'name': skill.get('name')})
        result['skills'] = skills

        # Parse certifications
        certifications = []
        cert_elements = profile.get('profileCertifications', {}).get('elements', [])
        for cert in cert_elements:
            start_date, end_date = parse_date_range(cert.get('dateRange'))
            certifications.append({
                'name': cert.get('name'),
                'authority': cert.get('authority'),
                'startDate': start_date,
                'endDate': end_date,
            })
        result['certifications'] = certifications

        # Parse languages
        languages = []
        lang_elements = profile.get('profileLanguages', {}).get('elements', [])
        for lang in lang_elements:
            languages.append({
                'name': lang.get('name'),
                'proficiency': lang.get('proficiency'),
            })
        result['languages'] = languages

        return result


def create_api_with_cookie(
    li_at_cookie: str,
    jsessionid_cookie: str = None,
    *,
    debug: bool = False,
    jitter_sleep_range: Tuple[float, float] = (0.0, 0.0),
) -> LinkedInScraper:
    """
    Create LinkedIn scraper instance using session cookies.

    Args:
        li_at_cookie: The 'li_at' cookie value from your browser
        jsessionid_cookie: The 'JSESSIONID' cookie value from your browser
        debug: Enable verbose logging (retries, cookie diffs, csrf-token).
               Also enabled by the LINKEDIN_DEBUG=1 environment variable.
        jitter_sleep_range: (min_sec, max_sec) random sleep before each request.

    Returns:
        LinkedInScraper instance
    """
    if not jsessionid_cookie:
        raise ValueError("JSESSIONID cookie is required")
    return LinkedInScraper(
        li_at_cookie,
        jsessionid_cookie,
        debug=debug,
        jitter_sleep_range=jitter_sleep_range,
    )


def scrape_profile(api: LinkedInScraper, profile_url_or_id: str) -> dict:
    """
    Scrape a LinkedIn profile and return structured data.

    Args:
        api: LinkedInScraper instance
        profile_url_or_id: LinkedIn profile URL or public identifier

    Returns:
        Dictionary with profile data including experience and education
    """
    public_id = extract_public_id(profile_url_or_id)
    return api.get_profile(public_id)


def scrape_profiles(api: LinkedInScraper, profile_urls: list[str]) -> list[dict]:
    """Scrape multiple LinkedIn profiles."""
    results = []
    for url in profile_urls:
        try:
            print(f"Scraping: {url}")
            profile_data = scrape_profile(api, url)
            results.append(profile_data)
            print(f"  -> Success: {profile_data['fullName']}")
        except Exception as e:
            print(f"  -> Error: {e}")
            results.append({
                'linkedinUrl': url,
                'error': str(e)
            })
    return results


def main():
    """Main entry point."""
    LI_AT_COOKIE = "YOUR_LI_AT_COOKIE_HERE"
    JSESSIONID_COOKIE = "YOUR_JSESSIONID_COOKIE_HERE"

    PROFILE_URLS = [
        "https://linkedin.com/in/williamhgates",
    ]

    if LI_AT_COOKIE == "YOUR_LI_AT_COOKIE_HERE":
        print("Error: Please set your cookies in the script.")
        print("\nTo get your cookies:")
        print("1. Log into LinkedIn in Chrome")
        print("2. Open DevTools (F12) -> Application -> Cookies -> linkedin.com")
        print("3. Copy the 'li_at' and 'JSESSIONID' cookie values")
        sys.exit(1)

    print("Authenticating with LinkedIn...")
    api = create_api_with_cookie(LI_AT_COOKIE, JSESSIONID_COOKIE)

    print(f"\nScraping {len(PROFILE_URLS)} profiles...\n")
    results = scrape_profiles(api, PROFILE_URLS)

    output_file = "scraped_profiles.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to {output_file}")


if __name__ == "__main__":
    main()
