"""
LinkedIn Profile Scraper
Extracts detailed experience and education data from LinkedIn profiles.
Uses LinkedIn's Voyager API with cookie-based authentication.
"""

import json
import logging
import re
import sys
import requests
from typing import Optional
from requests.cookies import RequestsCookieJar

logger = logging.getLogger(__name__)


# API Configuration
LINKEDIN_BASE_URL = "https://www.linkedin.com"
API_BASE_URL = f"{LINKEDIN_BASE_URL}/voyager/api"
PROFILE_ENDPOINT = "/identity/dash/profiles"
DECORATION_ID = "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-109"

REQUEST_HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "accept-language": "en-US,en;q=0.9",
    "x-li-lang": "en_US",
    "x-restli-protocol-version": "2.0.0",
}


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

    def __init__(self, li_at_cookie: str, jsessionid_cookie: str):
        """
        Initialize scraper with LinkedIn session cookies.

        Args:
            li_at_cookie: The 'li_at' cookie value from your browser
            jsessionid_cookie: The 'JSESSIONID' cookie value from your browser
        """
        self.session = requests.Session()

        # Set up cookies
        cookies = RequestsCookieJar()
        cookies.set('li_at', li_at_cookie, domain='.linkedin.com', path='/')
        jsessionid_clean = jsessionid_cookie.strip('"')
        cookies.set('JSESSIONID', f'"{jsessionid_clean}"', domain='.linkedin.com', path='/')
        self.session.cookies = cookies

        # Set up headers
        headers = REQUEST_HEADERS.copy()
        headers['csrf-token'] = jsessionid_clean
        self.session.headers.update(headers)

        # Snapshot of cookie names→values used to detect Set-Cookie updates
        self._prev_cookies: dict = {c.name: c.value for c in self.session.cookies}

    def _sync_csrf_token(self) -> None:
        """Keep the csrf-token request header in sync with the current JSESSIONID cookie.

        LinkedIn's Voyager API expects the csrf-token header to equal the
        JSESSIONID cookie value with surrounding quotes stripped.  This must be
        called before every request so that a server-rotated JSESSIONID is
        reflected in the header.
        """
        jsessionid = self.session.cookies.get('JSESSIONID')
        if jsessionid:
            self.session.headers.update({'csrf-token': jsessionid.strip('"')})

    def _fetch(self, endpoint: str, params: dict = None) -> dict:
        """Make a GET request to LinkedIn API.

        The session automatically merges any Set-Cookie headers from the
        response into the cookie jar.  After each response we diff the jar
        against the previous snapshot so we can log changes and keep
        csrf-token in sync with any updated JSESSIONID.
        """
        url = f"{API_BASE_URL}{endpoint}"

        # Sync csrf-token with the current JSESSIONID before sending the request
        self._sync_csrf_token()

        res = self.session.get(url, params=params)

        # requests.Session has already merged Set-Cookie into self.session.cookies.
        # Only diff the jar when the response actually carried Set-Cookie headers
        # (res.cookies is non-empty), then re-sync csrf-token if JSESSIONID rotated.
        if res.cookies:
            current_cookies = {c.name: c.value for c in self.session.cookies}
            if current_cookies != self._prev_cookies:
                changed_keys = [
                    k for k in current_cookies
                    if self._prev_cookies.get(k) != current_cookies[k]
                ]
                new_keys = [k for k in current_cookies if k not in self._prev_cookies]
                logger.debug(
                    "[cookie update] Cookies changed after %s — updated: %s, new: %s",
                    endpoint, changed_keys, new_keys,
                )
                self._prev_cookies = current_cookies

                if 'JSESSIONID' in changed_keys:
                    # JSESSIONID was rotated by the server; keep csrf-token in sync
                    self._sync_csrf_token()
                    logger.debug("[cookie update] csrf-token synced to new JSESSIONID")

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


def create_api_with_cookie(li_at_cookie: str, jsessionid_cookie: str = None) -> LinkedInScraper:
    """
    Create LinkedIn scraper instance using session cookies.

    Args:
        li_at_cookie: The 'li_at' cookie value from your browser
        jsessionid_cookie: The 'JSESSIONID' cookie value from your browser

    Returns:
        LinkedInScraper instance
    """
    if not jsessionid_cookie:
        raise ValueError("JSESSIONID cookie is required")
    return LinkedInScraper(li_at_cookie, jsessionid_cookie)


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
