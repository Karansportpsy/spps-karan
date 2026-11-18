"""Race regression: post an assessment while a stale focus refresh is in flight.

Uses only guarded disposable PostgreSQL (tests_integration/conftest.py), never
hosted staging or production. An old GET is captured BEFORE the new POST and
released AFTER the new assessment is rendered. No blind POST retries.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright
from tests_integration.test_browser_workflow import (
    sign_in, create_athlete_ui, add_assessment_ui,
)

ROOT = Path(__file__).resolve().parents[1]


def test_focus_loadall_loadathlete_and_assessment_post_race(integration_env, tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = 'http://127.0.0.1:' + str(port)
    with (tmp_path / 'race-backend.log').open('w', encoding='utf-8') as log:
        backend = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'server.main:app', '--host', '127.0.0.1',
             '--port', str(port), '--no-access-log'],
            cwd=ROOT, env={**os.environ, **integration_env},
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(100):
                if backend.poll() is not None:
                    raise AssertionError('Disposable backend exited')
                try:
                    if httpx.get(url + '/health/ready', timeout=1).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.2)
            else:
                raise AssertionError('Disposable backend did not become ready')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page(viewport={'width': 1280, 'height': 950})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    sign_in(page, url, integration_env)
                    aid = create_athlete_ui(page, 'Fictional race athlete')
                    page.locator('[data-action="view"][data-view="assessments"]').click()
                    for iteration in range(1, 4):
                        # Wrap fetch only for this round. Older wrappers have already
                        # released their one captured response and will not block.
                        page.evaluate("""(aid) => {
                          const original = window.fetch.bind(window);
                          const race = {captured:false, released:false, release:null};
                          window.__sppsRace = race;
                          window.fetch = async (...args) => {
                            const request = args[0];
                            const target = typeof request === 'string' ? request : request.url;
                            const method = ((args[1] || {}).method || 'GET').toUpperCase();
                            const response = await original(...args);
                            if (!race.captured && method === 'GET' &&
                                target.endsWith('/api/athletes/' + aid + '/assessments/offline')) {
                              const previousBody = await response.arrayBuffer();
                              race.captured = true;
                              await new Promise(resolve => { race.release = resolve });
                              race.released = true;
                              return new Response(previousBody, {
                                status:response.status, statusText:response.statusText,
                                headers:response.headers
                              });
                            }
                            return response;
                          };
                        }""", aid)
                        page.evaluate("window.dispatchEvent(new Event('focus'))")
                        page.wait_for_function('window.__sppsRace && window.__sppsRace.captured', timeout=15000)
                        created_id = add_assessment_ui(page, 10 + iteration, aid)
                        rows = page.locator('.data-table tbody tr').filter(
                            has_text='Fictional integration scale')
                        expect(rows).to_have_count(iteration, timeout=15000)
                        page.evaluate('window.__sppsRace.release()')
                        page.wait_for_function('window.__sppsRace.released')
                        page.wait_for_timeout(400)
                        expect(rows).to_have_count(iteration)
                        assert page.locator('[data-action="athlete-select"]').input_value() == str(aid)
                        response = page.request.get(
                            url + '/api/athletes/' + str(aid) + '/assessments/offline')
                        assert response.status == 200
                        records = response.json()
                        ids = {record['id'] for record in records
                               if record['instrument_name'] == 'Fictional integration scale'}
                        assert created_id in ids and len(ids) == iteration
                    assert not errors, errors
                finally:
                    browser.close()
        finally:
            if backend.poll() is None:
                backend.terminate()
            backend.wait(timeout=20)
