"""Resolve all local configuration and writable state from one project root."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPINE_RUNTIME = ROOT / 'frontend/public/vendor/spine-3.6/spine-webgl.js'

def project_path(value):
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()

def data_root():
    return project_path(os.environ.get('KAIBUN_DATA_PATH') or 'data')

def characters_path_from_env():
    return project_path(os.environ.get('KAIBUN_CHARACTERS_PATH') or 'characters/characters.toml')

def load_environment():
    from dotenv import dotenv_values
    local = dotenv_values(ROOT / '.env')
    values = {}
    source = os.environ.get('KAIBUN_SOURCE_ENV') or local.get('KAIBUN_SOURCE_ENV')
    if source:
        original = dotenv_values(project_path(source))
        # Read only the services used by this test copy, never the original account/database settings.
        prefixes = ('CHAT_LLM_', 'KAIBUN_BACKGROUND_LLM_', 'FISH_AUDIO_')
        optional = {'KAIBUN_PRODUCER_VOICE_ID', 'KAIBUN_FUYUKO_IDOL_VOICE_ID', 'KAIBUN_FFMPEG'}
        values.update({key: value for key, value in original.items() if key.startswith(prefixes) or key in optional})
        values['KAIBUN_PROXY_URL'] = original.get('KAIBUN_PROXY_URL') or original.get('EIDOLON_PROXY_URL') or ''
        routes = original.get('KAIBUN_PROXY_SERVICES') or original.get('EIDOLON_PROXY_SERVICES') or ''
        values['KAIBUN_PROXY_SERVICES'] = ','.join(name.strip() for name in routes.split(',') if name.strip() in {'all', 'chat_llm', 'tts'})
    values.update(local)
    for key, value in values.items():
        if value is not None:
            os.environ.setdefault(key, value)

def runtime_connection(token=None):
    import json
    import secrets
    token = token or os.environ.get('KAIBUN_API_TOKEN') or secrets.token_urlsafe(32)
    directory = ROOT / '.runtime'
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / 'connection.json'
    path.write_text(json.dumps({'url': 'http://127.0.0.1:' + os.environ.get('KAIBUN_PORT', '8011'), 'token': token}), encoding='utf-8')
    path.chmod(0o600)
    return token
