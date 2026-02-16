#!/usr/bin/env python3
"""
LinkedIn Profile Scraper CLI
Usage:
    python cli.py --cookie "your_li_at_cookie" --profiles profile1 profile2 ...
    python cli.py --config config.json
    python cli.py --cookie "your_li_at_cookie" --input urls.txt
"""

import argparse
import json
import os
import sys
from pathlib import Path

from scraper import create_api_with_cookie, scrape_profiles


def load_urls_from_file(file_path: str) -> list[str]:
    """Load profile URLs from a text file (one per line)."""
    with open(file_path, 'r') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]


def main():
    parser = argparse.ArgumentParser(
        description='Scrape LinkedIn profiles for experience and education data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s --cookie "AQE..." --profiles williamhgates satlouis
  %(prog)s --cookie "AQE..." --input urls.txt
  %(prog)s --config config.json

To get your li_at cookie:
  1. Log into LinkedIn in Chrome
  2. Open DevTools (F12) -> Application -> Cookies -> linkedin.com
  3. Copy the 'li_at' cookie value
        '''
    )

    parser.add_argument(
        '--cookie',
        help='LinkedIn li_at session cookie (or set LINKEDIN_LI_AT_COOKIE env var)'
    )
    parser.add_argument(
        '--jsessionid',
        help='LinkedIn JSESSIONID cookie (or set LINKEDIN_JSESSIONID_COOKIE env var)'
    )
    parser.add_argument(
        '--profiles',
        nargs='+',
        help='LinkedIn profile URLs or usernames to scrape'
    )
    parser.add_argument(
        '--input', '-i',
        help='Text file with profile URLs (one per line)'
    )
    parser.add_argument(
        '--config', '-c',
        help='JSON config file with cookie and profile_urls'
    )
    parser.add_argument(
        '--output', '-o',
        default='scraped_profiles.json',
        help='Output JSON file (default: scraped_profiles.json)'
    )
    parser.add_argument(
        '--pretty',
        action='store_true',
        default=True,
        help='Pretty print JSON output (default: True)'
    )

    args = parser.parse_args()

    # Load config from file if provided
    if args.config:
        with open(args.config, 'r') as f:
            config = json.load(f)
        cookie = config.get('li_at_cookie')
        jsessionid = config.get('jsessionid_cookie')
        profiles = config.get('profile_urls', [])
    else:
        # Check environment variable, then command line arg
        cookie = args.cookie or os.environ.get('LINKEDIN_LI_AT_COOKIE')
        jsessionid = args.jsessionid or os.environ.get('LINKEDIN_JSESSIONID_COOKIE')
        profiles = []

    # Add profiles from command line
    if args.profiles:
        profiles.extend(args.profiles)

    # Add profiles from input file
    if args.input:
        profiles.extend(load_urls_from_file(args.input))

    # Validate inputs
    if not cookie:
        print("Error: No cookie provided. Use --cookie or --config")
        sys.exit(1)

    if cookie == "YOUR_LI_AT_COOKIE_HERE":
        print("Error: Please replace the placeholder cookie with your actual li_at cookie")
        sys.exit(1)

    if not profiles:
        print("Error: No profiles provided. Use --profiles, --input, or --config")
        sys.exit(1)

    # Remove duplicates while preserving order
    profiles = list(dict.fromkeys(profiles))

    print(f"Authenticating with LinkedIn...")
    try:
        api = create_api_with_cookie(cookie, jsessionid)
    except Exception as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)

    print(f"Scraping {len(profiles)} profile(s)...\n")
    results = scrape_profiles(api, profiles)

    # Count successes and failures
    successes = sum(1 for r in results if 'error' not in r)
    failures = len(results) - successes

    # Save results
    with open(args.output, 'w', encoding='utf-8') as f:
        if args.pretty:
            json.dump(results, f, indent=2, ensure_ascii=False)
        else:
            json.dump(results, f, ensure_ascii=False)

    print(f"\nDone! {successes} succeeded, {failures} failed")
    print(f"Results saved to: {args.output}")


if __name__ == "__main__":
    main()
