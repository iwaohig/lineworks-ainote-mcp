# lineworks-ainote-mcp

LINE WORKS AiNote (AI議事録) のノートを Claude から参照するための MCP サーバーです。読み取り専用。

| ツール | 内容 |
|---|---|
| `list_notes` | ノート一覧 (タイトル・作成日時・音声長のみ) |
| `search_notes` | キーワード検索 (同上) |
| `get_note_summary` | AI 要約と参加者 (文字起こしを含まない) |
| `get_note_transcript` | 文字起こし全文 (`[時刻] 話者: 発言` に整形) |

## 前提

- LINE WORKS で AiNote が利用でき、Developer Console でスコープ `ainote.read` を選べること
- Python 3.12 以上と [uv](https://docs.astral.sh/uv/)
- Claude Desktop または Claude Code

## セットアップ

### 1. Developer Console でアプリを作る

1. [LINE WORKS Developer Console](https://developers.worksmobile.com/) でアプリを新規追加
2. OAuth Scopes に `ainote.read` を追加
3. Redirect URL に `http://localhost:8765/callback` を登録 (完全一致が必要)
4. Client ID と Client Secret を控える

### 2. 初回ログイン

```bash
uv run ainote_mcp.py auth
```

Client ID / Client Secret を入力するとブラウザが開きます。LINE WORKS にログインして許可してください。
トークンは OS の資格情報ストア (Windows: 資格情報マネージャー、macOS: キーチェーン) に保存されます。
設定ファイルや環境変数にシークレットを書く必要はありません。

### 3. Claude に登録

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "lineworks-ainote": {
      "command": "uv",
      "args": ["run", "--quiet", "C:\\path\\to\\ainote_mcp.py"]
    }
  }
}
```

Windows で `uv` が見つからない場合は `command` にフルパスを書いてください。

Claude Code:

```bash
claude mcp add lineworks-ainote -- uv run --quiet /path/to/ainote_mcp.py
```

## ログアウト

```bash
uv run ainote_mcp.py logout
```

保存済みの Client ID / Secret / トークンをすべて削除します。

## 制限

- AiNote API は User Account 認証専用です。Service Account (JWT) では利用できません
- 検索 API の呼び出し上限は 60 回/分です。ツール側で 1 秒間隔に制限しています
- 削除 API と統計系 API (管理者権限が必要) は実装していません
- 管理者による利用制御やマスキングはありません。どのノートを Claude に読ませるかは利用者の判断になります

## 動作確認環境

- Windows 11 (資格情報マネージャー) で確認済み
- macOS はキーチェーンで動く設計ですが未確認です
- Linux / WSL では keyring のバックエンド (Secret Service など) が必要です。
  ない環境では起動時にエラーになります。`keyrings.alt` を入れると動きますが平文保存になります
