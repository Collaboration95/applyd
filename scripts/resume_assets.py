"""Private LinkedIn asset receipts. Caller holds the existing workspace writer.

Identity is account + opaque asset + revision, never the displayed filename.
Only independently verified uploaded/downloaded bytes may establish a receipt.
"""
from datetime import timedelta
import hashlib
import settings
from resolve_resume import resolve, timestamp


def _identity(asset):
    if (not isinstance(asset, dict) or set(asset) != {'account_id', 'asset_id', 'revision'}
            or any(not isinstance(v, str) or not v.strip() for v in asset.values())):
        raise ValueError('resume_asset_identity_required')
    return dict(asset)


def _path(root):
    return settings._path(settings._root(root), 'resumes/platform-assets.json')


def _load(root):
    path = _path(root)
    if not path.exists():
        if path.is_symlink():
            raise ValueError('resume_mapping_invalid')
        return {'schema_version': 1, 'receipts': []}
    state = settings._read(path, 'platform-assets.json')
    if not isinstance(state, dict) or set(state) != {'schema_version', 'receipts'} or state['schema_version'] != 1 or not isinstance(state['receipts'], list):
        raise ValueError('resume_mapping_invalid')
    for row in state['receipts']:
        if set(row) != {'asset', 'release_id', 'sha256', 'verified_at', 'expires_at', 'method'}:
            raise ValueError('resume_mapping_invalid')
        _identity(row['asset'])
        timestamp(row['verified_at']); timestamp(row['expires_at'])
        if row['method'] not in {'verified_upload', 'verified_download'}:
            raise ValueError('resume_mapping_invalid')
    return state


def _current(root, scope, now):
    root = settings._root(root)
    return resolve(settings._read(settings._path(root, 'resumes/registry.json'), 'registry.json'), scope, 'application', now, root)


def remember(root, scope, asset, observed_bytes, now, *, method, lifetime=timedelta(hours=24)):
    """Bind an observed asset only after byte verification; no receipt-only trust.

    observed_bytes must come from the trusted upload readback/download boundary,
    not a filename or a claimed digest. Retain exact prepared copies as before.
    """
    if method not in {'verified_upload', 'verified_download'} or not timedelta(0) < lifetime <= timedelta(days=1):
        raise ValueError('resume_verification_required')
    asset = _identity(asset)
    current = _current(root, scope, now)  # resolves release/status/deadlines/hash now
    if not observed_bytes or hashlib.sha256(observed_bytes).hexdigest() != current['sha256']:
        raise ValueError('resume_asset_hash_mismatch')
    state = _load(root)
    state['receipts'] = [r for r in state['receipts'] if r['asset'] != asset]
    state['receipts'].append({'asset': asset, 'release_id': current['id'], 'sha256': current['sha256'],
        'verified_at': now.isoformat(), 'expires_at': (now + lifetime).isoformat(), 'method': method})
    from workspace import atomic_json
    atomic_json(_path(root), state)
    return current


def verify(root, scope, asset, expected, now):
    """Fresh resolver on every use; a missing/changed/expired receipt is fallback."""
    current = _current(root, scope, now)  # invalid current release blocks, never fallback
    if any(current[k] != expected.get(k) for k in ('id', 'sha256', 'path')):
        raise ValueError('resume_changed')
    asset = _identity(asset)
    matches = [r for r in _load(root)['receipts'] if r['asset'] == asset]
    if len(matches) != 1:
        return False
    row = matches[0]
    return (row['release_id'] == current['id'] and row['sha256'] == current['sha256']
            and timestamp(row['verified_at']) <= now < timestamp(row['expires_at']))


class Boundary:
    """Python side of JS resumeVerifier/rememberResume integration.

    Runtime transport binds these methods to the JS callbacks under the writer.
    read_asset_bytes must independently read the exact currently selected asset;
    absence is a typed handoff, never a digest/filename shortcut.
    """
    def __init__(self, root, scope, *, guard, clock, read_asset_bytes=None):
        self.root, self.scope = settings._root(root), scope
        self.guard, self.clock, self.read_asset_bytes = guard, clock, read_asset_bytes

    def _guard(self):
        if self.guard() is not True:
            raise ValueError('resume_guard_refused')

    def resolve(self):
        self._guard()
        return _current(self.root, self.scope, self.clock())

    def verify(self, request):
        self._guard()
        return verify(self.root, self.scope, request['asset'], request['resume'], self.clock())

    def remember(self, request):
        self._guard()
        if not callable(self.read_asset_bytes):
            raise ValueError('resume_asset_verification_unavailable')
        content = self.read_asset_bytes(_identity(request['asset']))
        self._guard()
        current = _current(self.root, self.scope, self.clock())
        if any(current[k] != request['resume'].get(k) for k in ('id','sha256','path')):
            raise ValueError('resume_changed')
        remember(self.root, self.scope, request['asset'], content, self.clock(), method='verified_upload')
        return True
