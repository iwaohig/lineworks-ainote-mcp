# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2,<3", "httpx>=0.27", "keyring>=25"]
# ///
"""LINE WORKS AiNote (AI議事録) を Claude から参照する MCP サーバー (読み取り専用)。

使い方:
    uv run ainote_mcp.py auth     # 初回ログイン (ブラウザで LINE WORKS にログイン)
    uv run ainote_mcp.py          # MCP サーバーとして stdio で待ち受け (Claude が起動する)
    uv run ainote_mcp.py logout   # 保存済みの資格情報を削除

認証:
    AiNote API は User Account 認証 (OAuth 2.0 認可コードフロー) 専用。
    Client ID / Client Secret / トークンは keyring 経由で OS の資格情報ストア
    (Windows: 資格情報マネージャー, macOS: キーチェーン) に保存する。
    設定ファイルや環境変数にシークレットを書く必要はない。

設計:
    ノート詳細のレスポンスは文字起こし全文 (scripts) を含み、1時間の会議で数十KBになる。
    そのため要約 (get_note_summary) と全文 (get_note_transcript) を別ツールに分け、
    一覧・検索 (list_notes / search_notes) は内容を一切返さない。

    削除 (DELETE) は実装しない。統計系 (quota / statistics) は管理者権限が必要なため扱わない。
"""

from __future__ import annotations

import getpass
import logging
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import keyring
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

AUTH_URL = "https://auth.worksmobile.com/oauth2/v2.0/authorize"
TOKEN_URL = "https://auth.worksmobile.com/oauth2/v2.0/token"
API_BASE = "https://www.worksapis.com/v1.0"
SCOPE = "ainote.read"

REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"

KEYRING_SERVICE = "lineworks-ainote-mcp"
_KEYS = ("client_id", "client_secret", "access_token", "expires_at", "refresh_token")

# Windows 資格情報マネージャーは 1 エントリあたり約 2,560 バイトが上限。
# トークンが長い場合に備えて分割して保存する。
_CHUNK = 1000

server = MCPServer("lineworks-ainote")

# httpx は INFO でリクエスト行をログに出す。auth コマンドの表示に混ざるので抑える。
logging.getLogger("httpx").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 資格情報ストア
# ---------------------------------------------------------------------------


def _store(key: str, value: str) -> None:
    """値を分割して keyring に保存する。既存の余剰チャンクは削除する。"""
    chunks = [value[i : i + _CHUNK] for i in range(0, len(value), _CHUNK)] or [""]
    keyring.set_password(KEYRING_SERVICE, f"{key}.n", str(len(chunks)))
    for i, chunk in enumerate(chunks):
        keyring.set_password(KEYRING_SERVICE, f"{key}.{i}", chunk)
    # 以前より短くなった場合の残骸を消す
    i = len(chunks)
    while keyring.get_password(KEYRING_SERVICE, f"{key}.{i}") is not None:
        keyring.delete_password(KEYRING_SERVICE, f"{key}.{i}")
        i += 1


def _load(key: str) -> str | None:
    n = keyring.get_password(KEYRING_SERVICE, f"{key}.n")
    if not n:
        return None
    parts = []
    for i in range(int(n)):
        part = keyring.get_password(KEYRING_SERVICE, f"{key}.{i}")
        if part is None:
            return None
        parts.append(part)
    return "".join(parts)


def _delete(key: str) -> None:
    n = keyring.get_password(KEYRING_SERVICE, f"{key}.n")
    if n:
        for i in range(int(n)):
            try:
                keyring.delete_password(KEYRING_SERVICE, f"{key}.{i}")
            except keyring.errors.PasswordDeleteError:
                pass
        keyring.delete_password(KEYRING_SERVICE, f"{key}.n")


def _save_tokens(tokens: dict[str, Any]) -> None:
    _store("access_token", tokens["access_token"])
    _store("expires_at", str(time.time() + int(tokens.get("expires_in", 3600))))
    if tokens.get("refresh_token"):
        # リフレッシュ時に新しい refresh_token が返ることがある (Refresh Token Rotation)
        _store("refresh_token", tokens["refresh_token"])


# ---------------------------------------------------------------------------
# OAuth 2.0 (User Account 認証)
# ---------------------------------------------------------------------------


def _post_token(client_id: str, client_secret: str, params: dict[str, str]) -> dict[str, Any]:
    data = {"client_id": client_id, "client_secret": client_secret, **params}
    try:
        res = httpx.post(TOKEN_URL, data=data, timeout=30.0)
    except httpx.RequestError as exc:
        raise ToolError(f"認証サーバーに接続できません ({exc.__class__.__name__})") from exc
    if res.status_code >= 400:
        raise ToolError(f"トークン取得に失敗しました: HTTP {res.status_code} {res.text[:300]}")
    return res.json()


def _receive_code(auth_url: str, expected_state: str) -> str:
    """ローカルに一時的な HTTP サーバーを立て、認可コードを 1 回だけ受け取る。"""
    result: dict[str, str | None] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            result["code"] = (q.get("code") or [None])[0]
            result["state"] = (q.get("state") or [None])[0]
            result["error"] = (q.get("error") or [None])[0]
            result["error_description"] = (q.get("error_description") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<html><body><p>認証が完了しました。このタブは閉じてください。</p></body></html>".encode()
            )

        def log_message(self, *_: Any) -> None:
            pass

    # bind してからブラウザを開く。逆にするとリダイレクトが先に届いて接続拒否になる。
    with HTTPServer(("localhost", REDIRECT_PORT), Handler) as srv:
        srv.timeout = 300
        print(f"ブラウザが開きます。開かない場合は次の URL を開いてください:\n{auth_url}\n")
        webbrowser.open(auth_url)
        srv.handle_request()

    if result.get("error"):
        desc = result.get("error_description") or ""
        raise SystemExit(f"認可が拒否されました: {result['error']} {desc}".rstrip())
    if result.get("state") != expected_state:
        raise SystemExit("state が一致しません。認可をやり直してください。")
    if not result.get("code"):
        raise SystemExit("認可コードが取得できませんでした (タイムアウトの可能性)。")
    return result["code"]  # type: ignore[return-value]


def login() -> None:
    """初回ログイン。ターミナルから `uv run ainote_mcp.py auth` で実行する。"""
    print("LINE WORKS Developer Console で作成したアプリの情報を入力してください。")
    print(f"Redirect URL には {REDIRECT_URI} を登録しておく必要があります。\n")
    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret: ").strip()
    if not client_id or not client_secret:
        raise SystemExit("Client ID と Client Secret は必須です。")

    state = secrets.token_urlsafe(16)
    auth_url = AUTH_URL + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
        }
    )

    code = _receive_code(auth_url, state)
    tokens = _post_token(
        client_id,
        client_secret,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
    )
    if not tokens.get("refresh_token"):
        raise SystemExit("refresh_token が返されませんでした。アプリの設定を確認してください。")

    _store("client_id", client_id)
    _store("client_secret", client_secret)
    _save_tokens(tokens)
    print("\n認証が完了しました。トークンは OS の資格情報ストアに保存されています。")
    print(f"付与されたスコープ: {tokens.get('scope', '(不明)')}")


def logout() -> None:
    for key in _KEYS:
        _delete(key)
    print("保存済みの資格情報を削除しました。")


def get_access_token() -> str:
    """有効な Access Token を返す。期限が近ければ Refresh Token で更新する。"""
    token, expires_at = _load("access_token"), _load("expires_at")
    if token and expires_at and time.time() < float(expires_at) - 60:
        return token

    refresh = _load("refresh_token")
    client_id, client_secret = _load("client_id"), _load("client_secret")
    if not (refresh and client_id and client_secret):
        raise ToolError(
            "未認証です。ターミナルで `uv run ainote_mcp.py auth` を実行して "
            "LINE WORKS にログインしてください。"
        )
    try:
        tokens = _post_token(
            client_id, client_secret, {"grant_type": "refresh_token", "refresh_token": refresh}
        )
    except ToolError as exc:
        raise ToolError(
            f"{exc} Refresh Token が失効している可能性があります (有効期限 90 日)。"
            "`uv run ainote_mcp.py auth` で再ログインしてください。"
        ) from exc
    _save_tokens(tokens)
    return tokens["access_token"]


# ---------------------------------------------------------------------------
# API 呼び出し
# ---------------------------------------------------------------------------


def api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    headers = {"Authorization": f"Bearer {get_access_token()}"}
    try:
        res = httpx.get(f"{API_BASE}/{path}", headers=headers, params=clean, timeout=30.0)
    except httpx.RequestError as exc:
        raise ToolError(f"LINE WORKS API に接続できません ({exc.__class__.__name__})") from exc

    if res.status_code >= 400:
        # 403 は原因が複数ある (スコープ不足 / 権限不足 / プラン未対応)。
        # API 自身の code / description を必ず添える。
        detail = ""
        try:
            body = res.json()
            code, desc = body.get("code"), body.get("description")
            if code or desc:
                detail = f" [{code}: {desc}]"
        except ValueError:
            if res.text:
                detail = f" [{res.text[:200]}]"

        if res.status_code == 401:
            raise ToolError(
                f"HTTP 401{detail} トークンが無効です。`uv run ainote_mcp.py auth` で再ログインしてください。"
            )
        if res.status_code == 403:
            raise ToolError(
                f"HTTP 403{detail} スコープ ({SCOPE}) が付与されているか、"
                "契約プランで AiNote API が利用できるかを確認してください。"
            )
        if res.status_code == 404:
            raise ToolError(f"HTTP 404{detail} 存在しません -> {path}")
        if res.status_code == 429:
            raise ToolError(f"HTTP 429{detail} 呼び出し上限に達しました。1 分ほど待ってください。")
        raise ToolError(f"HTTP {res.status_code}{detail} -> {path}")

    if not res.content:
        return {}
    try:
        return res.json()
    except ValueError as exc:
        raise ToolError(f"応答を JSON として解釈できません -> {path}") from exc


# ---------------------------------------------------------------------------
# 整形
# ---------------------------------------------------------------------------


def _hms(milliseconds: Any) -> str:
    """ミリ秒を H:MM:SS にする。audioDuration と発言の offset はミリ秒。"""
    try:
        total = int(milliseconds) // 1000
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _summarize(note: dict[str, Any]) -> dict[str, Any]:
    """一覧用。要約も文字起こしも含めない。"""
    return {
        "noteId": note.get("noteId"),
        "title": note.get("title"),
        "createdTime": note.get("createdTime"),
        "duration": _hms(note.get("audioDuration")),
        "language": note.get("recognitionLanguage"),
    }


def _note_list(data: dict[str, Any]) -> dict[str, Any]:
    notes = data.get("notes") or []
    return {
        "count": len(notes),
        "notes": [_summarize(n) for n in notes],
        "nextCursor": (data.get("responseMetaData") or {}).get("nextCursor"),
    }


# ---------------------------------------------------------------------------
# ツール
# ---------------------------------------------------------------------------


@server.tool()
def list_notes(count: int = 20, cursor: str | None = None) -> dict[str, Any]:
    """AiNote のノート一覧を新しい順に取得する。要約も文字起こしも含まない。

    タイトル・作成日時・音声の長さのみを返す。内容を読むには、
    ここで得た noteId を get_note_summary に渡す。

    Args:
        count: 取得件数。既定 20。
        cursor: 前回の応答の nextCursor。次ページを取得する場合に指定する。
    """
    return _note_list(api_get("users/me/ainote/notes", {"count": count, "cursor": cursor}))


_SEARCH_MIN_INTERVAL = 1.0  # 秒。検索 API の上限 60 回/分に合わせる
_last_search_at = 0.0


def _throttle_search() -> None:
    """検索 API の呼び出し間隔を 1 秒以上あける。"""
    global _last_search_at
    wait = _SEARCH_MIN_INTERVAL - (time.monotonic() - _last_search_at)
    if wait > 0:
        time.sleep(wait)
    _last_search_at = time.monotonic()


@server.tool()
def search_notes(query: str, count: int = 20, cursor: str | None = None) -> dict[str, Any]:
    """AiNote のノートをキーワードで検索する。要約も文字起こしも含まない。

    検索 API の呼び出し上限は 60 回/分と他より厳しい (ツール側で 1 秒間隔に制限する)。
    同じ意図でキーワードを言い換えて繰り返し検索しないこと。
    件数が足りない場合は cursor でページを進める。

    Args:
        query: 検索キーワード (必須)。
        count: 取得件数。既定 20。
        cursor: 前回の応答の nextCursor。
    """
    if not query or not query.strip():
        raise ToolError("query は必須です。検索キーワードを指定してください。")
    _throttle_search()
    return _note_list(
        api_get("users/me/ainote/search", {"query": query.strip(), "count": count, "cursor": cursor})
    )


@server.tool()
def get_note_summary(note_id: str) -> dict[str, Any]:
    """ノートの AI 要約と参加者を取得する。文字起こし全文は含まない。

    ノートの内容を知りたい場合はまずこれを使う。要約で足りない場合にのみ
    get_note_transcript で全文を取得する。transcriptBlocks は文字起こしの
    発言ブロック数で、全文を取る際の max_blocks の目安になる。

    要約はノートの作成者が AiNote 上でテンプレートを選んで生成するもの。
    summaries が空の場合は作成者が要約を生成していないので、内容を知るには
    get_note_transcript で全文を読む。

    Args:
        note_id: ノートID。list_notes または search_notes の結果から得る。
    """
    note = api_get(f"users/me/ainote/notes/{note_id}")
    return {
        "noteId": note.get("noteId"),
        "title": note.get("title"),
        "createdTime": note.get("createdTime"),
        "duration": _hms(note.get("audioDuration")),
        "attendees": [
            a.get("attendeeName") for a in (note.get("attendees") or []) if a.get("attendeeName")
        ],
        "summaries": [
            {
                "summaryType": s.get("summaryType"),
                "summaryName": s.get("summaryName"),
                "content": s.get("content"),
            }
            for s in (note.get("summaries") or [])
        ],
        "transcriptBlocks": len(note.get("scripts") or []),
    }


@server.tool()
def get_note_transcript(note_id: str, max_blocks: int | None = None) -> dict[str, Any]:
    """ノートの文字起こし全文を取得する。

    1 時間の会議で数十 KB になる。要約で足りる場合は get_note_summary を使うこと。
    発言は `[時刻] 話者: 発言` の形に整形して返す。

    Args:
        note_id: ノートID。
        max_blocks: 返す発言ブロックの上限。省略すると全件。
    """
    note = api_get(f"users/me/ainote/notes/{note_id}")
    scripts = note.get("scripts") or []
    total = len(scripts)
    if max_blocks and max_blocks > 0:
        scripts = scripts[:max_blocks]

    lines = [
        f"[{_hms(b.get('startOffset'))}] {b.get('attendeeName') or '話者不明'}: "
        f"{(b.get('text') or '').strip()}"
        for b in scripts
    ]
    return {
        "noteId": note.get("noteId"),
        "title": note.get("title"),
        "duration": _hms(note.get("audioDuration")),
        "totalBlocks": total,
        "returnedBlocks": len(scripts),
        "truncated": len(scripts) < total,
        "transcript": "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auth":
        login()
    elif cmd == "logout":
        logout()
    elif cmd in ("", "serve"):
        server.run()  # stdio で待ち受ける
    else:
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
