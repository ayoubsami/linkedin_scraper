# LinkedIn Voyager Profile Scraper (cookie-based)

This project scrapes **LinkedIn profile data** (experience, education, skills, etc.) by calling LinkedIn’s **internal “Voyager” API** using your **logged-in browser session cookies** (`li_at` + `JSESSIONID`).

It includes:
- `cli.py`: a simple CLI to scrape a small list into one JSON file.
- `bulk_scraper.py`: a bulk runner with delays, retries, progress/resume, and per-profile output files.
- Helper scripts to dedupe input lists and remove failed items interactively.

## Important notes / disclaimer

- This is **not** a public, documented LinkedIn API. It uses endpoints like `https://www.linkedin.com/voyager/api/...` that are intended for LinkedIn’s own web app.
- Use responsibly and comply with any policies/laws that apply to your use case.
- **Do not share your cookies**. Treat them like passwords.
- `config.json` is ignored by git (see `.gitignore`). Keep it that way.
- Note: Consider using a **temporary/secondary LinkedIn account** for scraping, since accounts can be restricted. In my own testing, I scraped **12,000+ profiles over ~4 days** (with reasonable delays) without an immediate restriction, but the account was restricted **a few days later**.

## Requirements

- Python 3.10+ recommended
- Dependencies are in `requirements.txt`:

```bash
pip install -r requirements.txt
```

## How authentication works (why cookies are required)

LinkedIn requires you to be logged in to access most profile data via Voyager.

- **`li_at`**: proves you have a valid logged-in session
- **`JSESSIONID`**: used as a CSRF token (the code also sends it as the `csrf-token` header)

If either cookie is missing/expired, requests will fail (often 401/403).

## Configure cookies (recommended: config file)

1. Copy the example config:

```bash
cp config.example.json config.json
```

2. Edit `config.json` and set:
- `li_at_cookie`
- `jsessionid_cookie` (often looks like `ajax:...`)
- `profile_urls` (a list of `https://www.linkedin.com/in/<slug>` URLs or just `<slug>`)

`config.json` is already listed in `.gitignore`.

### Getting cookies from Chrome

Typical path:
- Open LinkedIn (logged in)
- DevTools → **Application** → **Cookies** → `https://www.linkedin.com`
- Copy values for `li_at` and `JSESSIONID`

## Usage

### Option A: Simple CLI (`cli.py`)

Scrape profiles and save everything to one JSON file:

```bash
python cli.py --config config.json --output scraped_profiles.json
```

You can also provide profiles via a text file (one per line):

```bash
python cli.py --config config.json --input urls.txt --output scraped_profiles.json
```

Or pass cookies via flags/env vars:

```bash
export LINKEDIN_LI_AT_COOKIE="..."
export LINKEDIN_JSESSIONID_COOKIE='ajax:...'
python cli.py --profiles https://www.linkedin.com/in/williamhgates
```

### Option B: Bulk mode (`bulk_scraper.py`)

Bulk scrape with anti-detection style delays + resume/progress tracking:

```bash
python bulk_scraper.py --config config.json --input profiles_to_scrape.txt --output output
```

Sample command with tuned delays and batch size:

```bash
python bulk_scraper.py -c config.json -i profiles_to_scrape.txt -o output --batch-size 20 --min-delay 3 --max-delay 10
```

Common options:
- `--no-resume`: ignore any existing progress and start fresh
- `--min-delay / --max-delay`: adjust delay between profiles
- `--batch-size`: adjust how often the long “batch break” happens

#### What bulk mode writes

Inside the output directory (default `output/`):
- `progress.json`: completed/failed lists so you can resume safely
- `profiles/<public_id>.json`: each successful profile saved immediately
- `all_profiles.json`: combined results at the end

## Helper scripts

### Dedupe input list

Removes duplicates from `profiles_to_scrape.txt` (in-place):

```bash
python dedupe_lines.py
```

### Remove failed profiles interactively

Reads the `failed` list from a progress JSON and offers to remove matching lines from your profiles list (creates a timestamped backup):

```bash
python remove_failed_interactive.py --progress output/progress.json --profiles profiles_to_scrape.txt
```

## Troubleshooting

- **“Session is not valid” / 401 / 403**: cookies are missing or expired; re-copy `li_at` + `JSESSIONID`.
- **429 / rate limited**: bulk mode already sleeps longer on rate limits. Reduce speed further by increasing delays and batch breaks.
- **Some profiles “restricted”**: private or blocked profiles can fail even with valid cookies.

## Project structure (quick tour)

- `scraper.py`: core `LinkedInScraper` + parsing logic (experience/education/skills/etc.)
- `cli.py`: small-batch runner that outputs one JSON file
- `bulk_scraper.py`: bulk runner with progress/resume and human-like pacing
- `dedupe_lines.py`: dedupe the profiles input list
- `remove_failed_interactive.py`: remove failed entries from input list with prompts

