"""Optional real Android WebView validation via a debug-only ADB CDP forward.

Install the local debug APK, adb reverse tcp:8000 tcp:<Windows backend port>, then forward a
loopback port to webview_devtools_remote_<app-pid>. Backend must be running.
SPPS_ANDROID_CDP defaults to http://127.0.0.1:9223. Fictional local profile only.
"""
import faulthandler
import json
import os
from pathlib import Path
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright, expect
from tests_integration.test_browser_workflow import (
    sign_in, create_athlete_ui, select_athlete, add_assessment_ui, add_monitoring_ui)

ROOT = Path(__file__).resolve().parents[1]


def main():
    faulthandler.dump_traceback_later(120, exit=True)
    env = dotenv_values(ROOT / '.env.integration')
    assert env.get('MODEL_C_INTEGRATION_DEV') == 'true'
    url = 'http://localhost:8000'
    with sync_playwright() as playwright:
        native = playwright.chromium.connect_over_cdp(os.getenv('SPPS_ANDROID_CDP', 'http://127.0.0.1:9223'))
        phone = native.contexts[0].pages[0]
        assert phone.url.startswith(url)
        browser = playwright.chromium.launch(executable_path=playwright.chromium.executable_path)
        web = browser.new_page(viewport={'width':1440,'height':1000})
        sign_in(web, url, env)
        aid = create_athlete_ui(web, 'Fictional Android Athlete')
        # Starting from the login screen gives a fresh independent native session.
        phone.goto(url + '/studio')
        if phone.locator('[data-action="logout"]').is_visible():
            phone.locator('[data-action="logout"]').click()
        sign_in(phone, url, env)
        select_athlete(phone, aid)
        add_assessment_ui(phone, 14)
        add_monitoring_ui(phone, 6)
        select_athlete(web, aid)
        add_assessment_ui(web, 19)
        add_monitoring_ui(web, 9)
        phone.reload()
        expect(phone.locator('[data-action="logout"]')).to_be_visible(timeout=30000)
        select_athlete(phone, aid)
        phone.locator('[data-action="view"][data-view="assessments"]').click()
        expect(phone.locator('.data-table tbody tr').filter(has_text='Fictional integration scale')).to_have_count(2)
        values = phone.evaluate('(path)=>fetch(path).then(r=>r.json())', f'/api/athletes/{aid}/observations')
        assert sorted(row['value'] for row in values) == [6,9]
        phone.screenshot(path=str(ROOT / 'output/milestone-android.png'), full_page=True)
        phone.locator('[data-action="logout"]').click()
        sign_in(phone, url, env, 'bob')
        denied = phone.evaluate('(path)=>fetch(path).then(r=>r.status)', f'/api/athletes/{aid}')
        assert denied == 404
        assert 'Fictional Android Athlete' not in phone.locator('#app').inner_text()
        (ROOT / 'output/android-validation.json').write_text(json.dumps({
            'result':'passed','transport':'ADB reverse, real Capacitor WebView',
            'athlete_id':aid,'observations':[6,9],'assessments':2,'outsider_status':denied}, indent=2))
        browser.close()
        native.close()
    print('Android WebView and desktop cross-device workflow passed')


if __name__ == '__main__':
    main()
