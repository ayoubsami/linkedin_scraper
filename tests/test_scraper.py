"""
Tests for LinkedInScraper focusing on dynamic cookie / csrf-token synchronisation.

These tests use unittest.mock to simulate LinkedIn Voyager API responses that
rotate the JSESSIONID cookie via Set-Cookie, verifying that:
  1. The requests.Session cookie jar is updated automatically.
  2. The csrf-token header is kept in sync after each response.
  3. Multiple sequential profile fetches all see the latest cookies.
"""
import json
import logging
import sys
import unittest
from unittest.mock import MagicMock

# Allow imports from the parent package directory
sys.path.insert(0, ".")

import requests
from requests.cookies import RequestsCookieJar

from scraper import LinkedInScraper, API_BASE_URL, PROFILE_ENDPOINT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_body: dict = None, set_cookies: dict = None):
    """Build a mock requests.Response with optional Set-Cookie side-effects."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = json.dumps(json_body or {})[:200]

    # Simulate Set-Cookie side-effect: when session.get() is called the session
    # merges response cookies into its jar automatically.  We replicate this by
    # storing the desired cookies on resp.cookies so that the patch can install
    # them into the session jar.
    resp.cookies = set_cookies or {}
    return resp


def _minimal_profile_payload(public_id: str = "testuser") -> dict:
    """Return the minimal Voyager API payload that get_profile() can parse."""
    return {
        "elements": [
            {
                "firstName": "Test",
                "lastName": "User",
                "headline": "Engineer",
                "summary": None,
                "location": {},
                "geoLocation": {},
                "industry": {},
                "profilePositionGroups": {"elements": []},
                "profileEducations": {"elements": []},
                "profileSkills": {"elements": []},
                "profileCertifications": {"elements": []},
                "profileLanguages": {"elements": []},
            }
        ]
    }


def _make_get_side_effect(scraper, responses_iter):
    """Return a side-effect function that simulates requests.Session.get.

    Each call returns the next response from *responses_iter* and replicates
    the requests.Session behaviour of merging Set-Cookie headers into the
    session cookie jar.
    """
    def _side_effect(url, **kwargs):
        resp = next(responses_iter)
        for name, value in resp.cookies.items():
            scraper.session.cookies.set(name, value, domain='.linkedin.com', path='/')
        return resp
    return _side_effect


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLinkedInScraperInit(unittest.TestCase):
    """Verify that __init__ sets up the session and csrf-token correctly."""

    def setUp(self):
        self.scraper = LinkedInScraper("li_at_value", '"ajax:abc123"')

    def test_session_created(self):
        self.assertIsInstance(self.scraper.session, requests.Session)

    def test_li_at_cookie_set(self):
        self.assertEqual(self.scraper.session.cookies.get('li_at'), 'li_at_value')

    def test_jsessionid_stored_with_quotes(self):
        self.assertEqual(self.scraper.session.cookies.get('JSESSIONID'), '"ajax:abc123"')

    def test_csrf_token_header_set_without_quotes(self):
        self.assertEqual(self.scraper.session.headers.get('csrf-token'), 'ajax:abc123')

    def test_prev_cookies_snapshot_initialised(self):
        self.assertIn('JSESSIONID', self.scraper._prev_cookies)
        self.assertEqual(self.scraper._prev_cookies['JSESSIONID'], '"ajax:abc123"')


class TestSyncCsrfToken(unittest.TestCase):
    """Verify _sync_csrf_token keeps the header aligned with the cookie jar."""

    def setUp(self):
        self.scraper = LinkedInScraper("li_at_value", "ajax:initial")

    def test_sync_reflects_jar_value(self):
        # Manually update the cookie jar to simulate a server rotation
        self.scraper.session.cookies.set(
            'JSESSIONID', '"ajax:rotated"', domain='.linkedin.com', path='/'
        )
        self.scraper._sync_csrf_token()
        self.assertEqual(self.scraper.session.headers['csrf-token'], 'ajax:rotated')

    def test_sync_strips_quotes(self):
        self.scraper.session.cookies.set(
            'JSESSIONID', '"ajax:quoted"', domain='.linkedin.com', path='/'
        )
        self.scraper._sync_csrf_token()
        self.assertNotIn('"', self.scraper.session.headers['csrf-token'])


class TestFetchCookieSync(unittest.TestCase):
    """Test that _fetch detects Set-Cookie updates and syncs csrf-token."""

    def _make_scraper(self):
        return LinkedInScraper("li_at_value", '"ajax:v1"')

    def _patch_session_get(self, scraper, responses):
        """Patch scraper.session.get to return responses in order, simulating Set-Cookie merging."""
        scraper.session.get = MagicMock(
            side_effect=_make_get_side_effect(scraper, iter(responses))
        )

    # ------------------------------------------------------------------
    # No cookie change
    # ------------------------------------------------------------------

    def test_no_cookie_change_csrf_unchanged(self):
        scraper = self._make_scraper()
        resp = _make_response(json_body={"data": "ok"}, set_cookies={})
        self._patch_session_get(scraper, [resp])

        scraper._fetch("/test")

        # csrf-token must remain the original value
        self.assertEqual(scraper.session.headers['csrf-token'], 'ajax:v1')

    # ------------------------------------------------------------------
    # JSESSIONID rotated → csrf-token must update
    # ------------------------------------------------------------------

    def test_jsessionid_rotation_updates_csrf_token(self):
        scraper = self._make_scraper()
        resp = _make_response(
            json_body={"data": "ok"},
            set_cookies={"JSESSIONID": '"ajax:v2"'},
        )
        self._patch_session_get(scraper, [resp])

        scraper._fetch("/identity/profile")

        # Cookie jar must hold the new value
        self.assertEqual(scraper.session.cookies.get('JSESSIONID'), '"ajax:v2"')
        # csrf-token header must be updated without quotes
        self.assertEqual(scraper.session.headers['csrf-token'], 'ajax:v2')
        # Internal snapshot must also be updated
        self.assertEqual(scraper._prev_cookies['JSESSIONID'], '"ajax:v2"')

    # ------------------------------------------------------------------
    # Non-JSESSIONID cookie update must NOT change csrf-token
    # ------------------------------------------------------------------

    def test_other_cookie_update_does_not_change_csrf(self):
        scraper = self._make_scraper()
        resp = _make_response(
            json_body={"data": "ok"},
            set_cookies={"bcookie": "new_bc_value"},
        )
        self._patch_session_get(scraper, [resp])

        scraper._fetch("/identity/profile")

        # csrf-token must remain untouched
        self.assertEqual(scraper.session.headers['csrf-token'], 'ajax:v1')

    # ------------------------------------------------------------------
    # Multiple sequential requests with cookie rotation between each
    # ------------------------------------------------------------------

    def test_sequential_requests_track_cookie_rotation(self):
        scraper = self._make_scraper()

        responses = [
            _make_response(
                json_body=_minimal_profile_payload("user1"),
                set_cookies={"JSESSIONID": '"ajax:v2"'},
            ),
            _make_response(
                json_body=_minimal_profile_payload("user2"),
                set_cookies={"JSESSIONID": '"ajax:v3"'},
            ),
            _make_response(
                json_body=_minimal_profile_payload("user3"),
                set_cookies={},
            ),
        ]
        self._patch_session_get(scraper, responses)

        # First request: JSESSIONID rotates to v2
        scraper._fetch(PROFILE_ENDPOINT, {"q": "memberIdentity", "memberIdentity": "user1"})
        self.assertEqual(scraper.session.headers['csrf-token'], 'ajax:v2')

        # Second request: JSESSIONID rotates to v3
        scraper._fetch(PROFILE_ENDPOINT, {"q": "memberIdentity", "memberIdentity": "user2"})
        self.assertEqual(scraper.session.headers['csrf-token'], 'ajax:v3')

        # Third request: no rotation; csrf-token stays at v3
        scraper._fetch(PROFILE_ENDPOINT, {"q": "memberIdentity", "memberIdentity": "user3"})
        self.assertEqual(scraper.session.headers['csrf-token'], 'ajax:v3')

    # ------------------------------------------------------------------
    # _fetch raises on non-200 status
    # ------------------------------------------------------------------

    def test_fetch_raises_on_non_200(self):
        scraper = self._make_scraper()
        resp = _make_response(status_code=403, set_cookies={})
        self._patch_session_get(scraper, [resp])

        with self.assertRaises(Exception) as ctx:
            scraper._fetch("/some/endpoint")

        self.assertIn("403", str(ctx.exception))

    # ------------------------------------------------------------------
    # csrf-token is synced BEFORE the request is sent
    # ------------------------------------------------------------------

    def test_csrf_token_synced_before_request(self):
        """
        If the jar already holds a newer JSESSIONID (e.g. set externally),
        _fetch must send the correct csrf-token for that very first request.
        """
        scraper = self._make_scraper()
        # Manually rotate the jar before any request
        scraper.session.cookies.set(
            'JSESSIONID', '"ajax:pre_rotated"', domain='.linkedin.com', path='/'
        )
        scraper._prev_cookies['JSESSIONID'] = '"ajax:pre_rotated"'  # keep snapshot in sync

        resp = _make_response(json_body={"data": "ok"}, set_cookies={})
        self._patch_session_get(scraper, [resp])

        scraper._fetch("/test")

        # The header sent with the request should have been the pre-rotated value
        self.assertEqual(scraper.session.headers['csrf-token'], 'ajax:pre_rotated')


class TestDebugLogging(unittest.TestCase):
    """Verify that cookie changes emit a DEBUG log message."""

    def _make_scraper(self):
        return LinkedInScraper("li_at_value", '"ajax:v1"')

    def _patch_session_get(self, scraper, resp):
        """Patch scraper.session.get for a single response, simulating Set-Cookie merging."""
        scraper.session.get = MagicMock(
            side_effect=_make_get_side_effect(scraper, iter([resp]))
        )

    def test_debug_log_emitted_on_cookie_change(self):
        scraper = self._make_scraper()
        resp = _make_response(
            json_body={"ok": True},
            set_cookies={"JSESSIONID": '"ajax:v2"'},
        )
        self._patch_session_get(scraper, resp)

        with self.assertLogs('scraper', level=logging.DEBUG) as log_cm:
            scraper._fetch("/test")

        combined = "\n".join(log_cm.output)
        self.assertIn("[cookie update]", combined)
        self.assertIn("JSESSIONID", combined)


if __name__ == "__main__":
    unittest.main()
