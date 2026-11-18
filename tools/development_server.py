"""Run the shared UI/API on loopback. Android reaches it using adb reverse."""
import os
from pathlib import Path
from dotenv import load_dotenv
import uvicorn


if __name__ == '__main__':
    load_dotenv(Path(__file__).resolve().parents[1] / '.env.integration', override=True)
    if os.getenv('MODEL_C_INTEGRATION_DEV') != 'true':
        raise SystemExit('Provision the isolated development database first')
    uvicorn.run('server.main:app', host='127.0.0.1', port=8000, workers=1, access_log=False)
