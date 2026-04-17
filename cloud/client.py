'''HTTP client for the Kontext cloud control plane.'''
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


_LOCALHOST_HOSTS = {'localhost', '127.0.0.1', '::1'}


class CloudClient:
    def __init__(self, server_url: str, timeout: int = 10, allow_insecure: bool = False,
                 ssl_context: ssl.SSLContext | None = None):
        parsed = urlparse(server_url)
        scheme = (parsed.scheme or '').lower()
        host = (parsed.hostname or '').lower()
        if scheme == 'http':
            if not allow_insecure or host not in _LOCALHOST_HOSTS:
                raise ValueError(
                    'cloud: plaintext http:// is only permitted with allow_insecure=True '
                    'against localhost (got ' + server_url + ')'
                )
        elif scheme != 'https':
            raise ValueError('cloud: server_url must use https:// (got ' + server_url + ')')

        self.server_url = server_url.rstrip('/')
        self.timeout = timeout
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._token: str | None = None

    def set_token(self, token: str | None) -> None:
        self._token = token.strip() if token else None

    def _request(self, method: str, path: str, payload: dict | None = None,
                 query: dict | None = None) -> dict:
        url = f'{self.server_url}{path}'
        if query:
            url = f'{url}?{urlencode(query)}'

        headers = {'accept': 'application/json'}
        data = None
        if payload is not None:
            headers['content-type'] = 'application/json'
            data = json.dumps(payload).encode('utf-8')
        if self._token:
            headers['authorization'] = f'Bearer {self._token}'

        request = Request(url, data=data, headers=headers, method=method)
        try:
            if urlparse(url).scheme == 'https':
                with urlopen(request, timeout=self.timeout, context=self._ssl_context) as response:
                    body = response.read().decode('utf-8')
                    return json.loads(body or '{}')
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode('utf-8')
                return json.loads(body or '{}')
        except HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'cloud request failed ({exc.code}): {detail or exc.reason}') from exc
        except URLError as exc:
            raise RuntimeError(f'cloud request failed: {exc.reason}') from exc

    def _push_lane(self, workspace_id: str, lane: str, items: list[dict]) -> dict:
        return self._request(
            'POST',
            '/v1/sync/push',
            payload={
                'workspace_id': workspace_id,
                'lane': lane,
                'items': items,
            },
        )

    def _pull_lane(self, workspace_id: str, device_id: str,
                   lane: str, after: str = '', limit: int = 500) -> dict:
        return self._request(
            'GET',
            '/v1/sync/pull',
            query={
                'workspace_id': workspace_id,
                'device_id': device_id,
                'lane': lane,
                'after': after,
                'limit': limit,
            },
        )

    def create_workspace(self, workspace_id: str, name: str,
                         recovery_key_id: str, existing_token: str | None = None) -> dict:
        payload = {
            'workspace_id': workspace_id,
            'name': name,
            'recovery_key_id': recovery_key_id,
        }
        if existing_token:
            payload['workspace_token'] = existing_token
        return self._request('POST', '/v1/workspaces', payload=payload)

    def enroll_device(self, workspace_id: str, device_id: str, label: str,
                      device_class: str, device_public_key: str,
                      enrollment_code: str = 'link') -> dict:
        return self._request(
            'POST',
            '/v1/devices/enroll',
            payload={
                'workspace_id': workspace_id,
                'enrollment_code': enrollment_code,
                'label': label,
                'device_class': device_class,
                'device_public_key': device_public_key,
                'device_id': device_id,
            },
        )

    def revoke_device(self, workspace_id: str, device_id: str) -> dict:
        return self._request(
            'POST',
            '/v1/devices/revoke',
            payload={
                'workspace_id': workspace_id,
                'device_id': device_id,
            },
        )

    def create_snapshot(self, workspace_id: str, device_id: str) -> dict:
        return self._request(
            'POST',
            '/v1/snapshots/create',
            payload={
                'workspace_id': workspace_id,
                'device_id': device_id,
            },
        )

    def pull_latest_snapshot(self, workspace_id: str, device_id: str) -> dict:
        return self._request(
            'GET',
            '/v1/snapshots/latest',
            query={
                'workspace_id': workspace_id,
                'device_id': device_id,
            },
        )

    def push_history(self, workspace_id: str, items: list[dict]) -> dict:
        return self._push_lane(workspace_id, 'history', items)

    def pull_history(self, workspace_id: str, device_id: str,
                     after: str = '', limit: int = 500) -> dict:
        return self._pull_lane(workspace_id, device_id, 'history', after=after, limit=limit)

    def push_canonical(self, workspace_id: str, items: list[dict]) -> dict:
        return self._push_lane(workspace_id, 'canonical', items)

    def pull_canonical(self, workspace_id: str, device_id: str,
                       after: str = '', limit: int = 500) -> dict:
        return self._pull_lane(workspace_id, device_id, 'canonical', after=after, limit=limit)
