import http.client
import socket
import ssl
from urllib.parse import urlsplit, urljoin


def fetch(url, mark=0):
    """Direct subscription fetch with optional Linux SO_MARK to bypass other TUNs."""
    for _ in range(4):
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            raise ValueError('请输入有效的 HTTP / HTTPS 订阅地址')
        port = p.port or (443 if p.scheme == 'https' else 80)
        sock = None
        for family, kind, proto, _, address in socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM):
            attempt = socket.socket(family, kind, proto)
            try:
                attempt.settimeout(25)
                if mark:
                    attempt.setsockopt(socket.SOL_SOCKET, 36, mark)
                attempt.connect(address)
                sock = attempt
                break
            except OSError:
                attempt.close()
        if sock is None:
            raise ValueError('订阅服务器连接失败，请检查地址及 IPv6 网络')
        conn = http.client.HTTPConnection(p.hostname, port, timeout=25)
        try:
            conn.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=p.hostname) if p.scheme == 'https' else sock
            path = p.path or '/'
            if p.query:
                path += '?' + p.query
            conn.request('GET', path, headers={'User-Agent': 'clash.meta/line-converter', 'Accept': '*/*', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status != 200:
                raise ValueError('订阅服务器返回 HTTP ' + str(response.status))
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError('订阅超过 4 MB 限制')
            return data.decode('utf-8-sig')
        except (OSError, http.client.HTTPException, UnicodeError):
            raise ValueError('订阅下载失败，原配置已保留') from None
        finally:
            conn.close()
            sock.close()
    raise ValueError('订阅重定向次数过多')
