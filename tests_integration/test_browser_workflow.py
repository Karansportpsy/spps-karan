"""Real UI and HTTP, independent browser cookie stores, actual backend restart.

The mobile viewport is a browser test, not a claim of physical Android testing.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def sign_in(page, url, env, name='alice'):
    page.goto(url + '/studio')
    form = page.locator('form[data-form="login"]')
    form.locator('[name="email"]').fill(f'{name}@spps.test')
    form.locator('[name="password"]').fill(env[f'SPPS_DEV_{name.upper()}_PASSWORD'])
    form.locator('[name="terms_accepted"]').check()
    # Distinguish a rejected login/API failure from a header-rendering regression.
    with page.expect_response(
        lambda response: response.url.endswith('/api/auth/login')
            and response.request.method == 'POST',
        timeout=30000,
    ) as sign_in_response:
        form.locator('[type="submit"]').click()
    assert sign_in_response.value.status == 200, (
        f"Practitioner login failed: HTTP {sign_in_response.value.status} "
        f"{sign_in_response.value.text()[:500]}"
    )
    logout = page.locator('header.site-header [data-action="logout"]')
    expect(logout).to_have_count(1, timeout=30000)
    expect(logout).to_be_visible(timeout=30000)


def select_athlete(page, aid):
    page.locator('[data-action="view"][data-view="athletes"]').click()
    page.locator(f'[data-action="athlete"][data-id="{aid}"]').click()
    # Wait until the actually displayed record matches before opening a clinical form.
    expect(page.locator('#athlete-overview-panel')).to_have_attribute(
        'data-athlete-id', str(aid), timeout=15000
    )


def create_athlete_ui(page, name):
    page.locator('[data-action="view"][data-view="athletes"]').click()
    page.locator('[data-action="new-athlete"]').click()
    form = page.locator('form[data-form="athlete"]')
    form.locator('[name="code"]').fill('UI-' + uuid4().hex[:20])
    form.locator('[name="display_name"]').fill(name)
    form.locator('[name="sport"]').fill('Badminton')
    form.locator('[name="service_consent_recorded"]').check()
    with page.expect_response(lambda r: r.url.endswith('/api/athletes') and r.request.method == 'POST') as result:
        form.locator('[type="submit"]').click()
    assert result.value.status == 201
    expect(form).to_have_count(0)
    aid = result.value.json()['id']
    select_athlete(page, aid)
    page.locator('[data-action="new-case"]').click()
    form = page.locator('form[data-form="case"]')
    form.locator('[name="title"]').fill('Fictional cross-device case')
    form.locator('[type="submit"]').click()
    expect(form).to_have_count(0)
    return aid


def add_assessment_ui(page, value, expected_athlete_id):
    page.locator('[data-action="view"][data-view="assessments"]').click()
    page.locator('[data-action="offline-assessment"]').click()
    form = page.locator('form[data-form="offline-assessment"]')
    form_athlete_id = int(form.get_attribute('data-athlete-id') or 0)
    form_case_id = int(form.get_attribute('data-case-id') or 0)
    assert form_athlete_id == expected_athlete_id, (
        f'Wrong athlete selected in assessment form: expected={expected_athlete_id}, '
        f'actual={form_athlete_id}, case_id={form_case_id}'
    )
    form.locator('[name="instrument_name"]').fill('Fictional integration scale')
    form.locator('[name="parent_factor"]').fill('Focus')
    form.locator('[name="scores"]').fill(f'Attention | Raw Score | {value}')
    # The form disappearing alone is not evidence that the write succeeded.
    with page.expect_response(
        lambda r: r.url.endswith('/api/assessments/offline') and r.request.method == 'POST'
    ) as submitted:
        form.locator('[type="submit"]').click()
    assert submitted.value.status == 201, (
        f'Assessment POST was not accepted: HTTP {submitted.value.status}'
    )
    created_id = submitted.value.json()['id']
    payload = submitted.value.request.post_data_json
    posted_athlete_id = payload.get('athlete_id')
    posted_case_id = payload.get('case_id')
    assert posted_athlete_id == expected_athlete_id, (
        f'Assessment POST targeted wrong athlete: expected={expected_athlete_id}, '
        f'actual={posted_athlete_id}, case_id={posted_case_id}, created_id={created_id}'
    )
    readback = page.request.get(
        submitted.value.url.replace('/api/assessments/offline',
                                    f'/api/athletes/{expected_athlete_id}/assessments/offline')
    )
    assert readback.status == 200, f'Assessment readback returned HTTP {readback.status}'
    returned_ids = {record['id'] for record in readback.json()}
    assert created_id in returned_ids, (
        f'HTTP 201 assessment #{created_id} absent from immediate readback for '
        f'athlete #{expected_athlete_id}; returned_ids={sorted(returned_ids)}'
    )
    expect(form).to_have_count(0)
    return created_id


def add_monitoring_ui(page, value):
    page.locator('[data-action="view"][data-view="monitoring"]').click()
    page.locator('[data-action="new-observation"]').click()
    form = page.locator('form[data-form="observation"]')
    form.locator('[name="value"]').fill(str(value))
    form.locator('[name="context"]').fill('Fictional cross-device entry')
    form.locator('[type="submit"]').click()
    expect(form).to_have_count(0)


def test_browser_two_way_sync_restart_and_account_switch(integration_env):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    log = (output / 'browser-backend.log').open('w', encoding='utf-8')
    processes = []
    def start():
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server.main:app',
            '--host', '127.0.0.1', '--port', str(port), '--no-access-log'],
            cwd=ROOT, env={**os.environ, **integration_env}, stdout=log, stderr=log)
        processes.append(process)
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError('Backend exited; inspect output/browser-backend.log')
            try:
                if httpx.get(url + '/health/ready', timeout=1).status_code == 200:
                    return process
            except httpx.TransportError:
                pass
            time.sleep(.2)
        raise AssertionError('Backend readiness timed out')
    try:
        process = start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=playwright.chromium.executable_path)
            desktop = browser.new_context(viewport={'width': 1440, 'height': 1000})
            mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            outsider = browser.new_context()
            web, phone, bob = desktop.new_page(), mobile.new_page(), outsider.new_page()
            errors = []
            for page in (web, phone, bob):
                page.on('pageerror', lambda error: errors.append(str(error)))
            sign_in(web, url, integration_env)
            sign_in(phone, url, integration_env)
            sign_in(bob, url, integration_env, 'bob')
            aid = create_athlete_ui(web, 'Fictional Browser Athlete ' + uuid4().hex[:6])
            # Returning to the mobile client triggers the same focus sync used by Android.
            with phone.expect_response(lambda r: r.url.endswith('/api/athletes')):
                phone.evaluate("window.dispatchEvent(new Event('focus'))")
            # Optional injury endpoint failure must not stop the core athlete overview.
            phone.route('**/api/athletes/*/injury', lambda route: route.fulfill(
                status=503, content_type='application/json',
                body='{"detail":"Simulated optional injury history unavailable"}'))
            select_athlete(phone, aid)
            expect(phone.locator('#athlete-overview-panel h2')).to_contain_text('Fictional Browser Athlete')
            phone.unroute('**/api/athletes/*/injury')
            mobile_assessment_id = add_assessment_ui(phone, 12, aid)
            add_monitoring_ui(phone, 7)
            with web.expect_response(lambda r: r.url.endswith(f'/api/athletes/{aid}/assessments/offline')):
                web.evaluate("window.dispatchEvent(new Event('focus'))")
            desktop_assessment_id = add_assessment_ui(web, 18, aid)
            add_monitoring_ui(web, 8)
            # Refresh via visible control and assert persisted backend values in both cookie stores.
            for client_name, page in (('desktop', web), ('mobile-viewport', phone)):
                page.locator('[data-action="view"][data-view="athletes"]').click()
                page.locator('[data-action="refresh-data"]').click()
                select_athlete(page, aid)
                page.locator('[data-action="view"][data-view="assessments"]').click()
                displayed = page.locator('.data-table tbody tr').filter(has_text='Fictional integration scale')
                try:
                    expect(displayed).to_have_count(2)
                except AssertionError as exc:
                    # Never mask a stale UI as a pass; inspect API only on failure
                    # to distinguish a failed write from out-of-date rendering.
                    response = page.request.get(url + f'/api/athletes/{aid}/assessments/offline')
                    api_count = (
                        len([row for row in response.json()
                             if row.get('instrument_name') == 'Fictional integration scale'])
                        if response.status == 200 else 'not available'
                    )
                    raise AssertionError(
                        f'{client_name}: assessment sync mismatch; '
                        f'API HTTP={response.status}, API rows={api_count}, '
                        f'UI rows={displayed.count()}, expected=2'
                    ) from exc
                response = page.request.get(url + f'/api/athletes/{aid}/assessments/offline')
                assert response.status == 200, f'{client_name}: assessment API HTTP {response.status}'
                actual_ids = {row['id'] for row in response.json()
                              if row.get('instrument_name') == 'Fictional integration scale'}
                assert actual_ids == {mobile_assessment_id, desktop_assessment_id}, (
                    f'{client_name}: submitted assessments are missing or duplicated in API readback'
                )
                values = page.request.get(url + f'/api/athletes/{aid}/observations').json()
                assert sorted(row['value'] for row in values) == [7, 8]
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
                assert page.title()
            assert bob.request.get(url + f'/api/athletes/{aid}').status == 404
            assert aid not in [row['id'] for row in bob.request.get(url + '/api/athletes').json()]
            web.screenshot(path=str(output / 'milestone-desktop.png'), full_page=True)
            phone.screenshot(path=str(output / 'milestone-mobile.png'), full_page=True)
            # A backend process restart must preserve both sessions and clinical rows.
            process.terminate()
            process.wait(timeout=20)
            start()
            for page in (web, phone):
                page.reload()
                expect(page.locator('header.site-header [data-action="logout"]')).to_be_visible(timeout=30000)
                assert len(page.request.get(url + f'/api/athletes/{aid}/assessments/offline').json()) == 2
            phone.locator('header.site-header [data-action="logout"]').click()
            expect(phone.locator('form[data-form="login"]')).to_be_visible()
            sign_in(phone, url, integration_env, 'bob')
            assert phone.request.get(url + f'/api/athletes/{aid}').status == 404
            assert 'Fictional Browser Athlete' not in phone.locator('#app').inner_text()
            assert not errors, errors
            browser.close()
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=20)
        log.close()
