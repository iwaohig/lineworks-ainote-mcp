# lineworks-ainote-mcp

> **Unofficial community project.** This is a personal, read-only MCP server for
> [LINE WORKS AiNote](https://line-works.com/) (AI meeting notes). It is not an official
> LINE WORKS product and is not affiliated with, endorsed by, or supported by LINE WORKS Corp.
> Provided as-is under the [MIT License](LICENSE).
>
> It lets Claude (or any MCP client) list, search, and read AiNote summaries and transcripts.
> LINE WORKS also offers an official **WORKS MCP** for mail, calendar, and tasks; AiNote is not
> part of it as of September 2026, so this project is meant to be used alongside it, not instead of it.
> Documentation below is in Japanese. See [Support](#support--サポートについて) for what is and
> is not covered here.

LINE WORKS AiNote (AI議事録) のノートを Claude から参照するための MCP サーバーです。読み取り専用。
**個人が公開している非公式の実装で、LINE WORKS の公式製品・公式サポートの対象ではありません。**
公式の WORKS MCP (メール・カレンダー・タスク) には 2026 年 9 月時点で AiNote が含まれていないため、
その補完として併用する想定です。

| 入口 | 内容 |
|---|---|
| MCP サーバーとして使う | このリポジトリの `ainote_mcp.py` (以下の手順) |
| API を直接試す | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/iwaohig/lineworks-ainote-mcp/blob/main/notebooks/lineworks_ainote_api.ipynb) `notebooks/lineworks_ainote_api.ipynb` |

解説記事 (Qiita):

- [LINE WORKS API AiNote の議事録 (文字起こし・要約) を取得する](https://qiita.com/iwaohig/items/bc95a027d76dab19647c): API の使い方とレスポンスの実例
- [LINE WORKS AiNote の議事録を Claude に読ませる MCP サーバーを作る](https://qiita.com/iwaohig/items/c7d7c1b0902613ee83cc): この MCP サーバーの設計

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

## Support / サポートについて

**English**

- This project is provided as-is under the MIT License, with no warranty and no guaranteed
  response time. It is maintained in a personal capacity.
- Issues and pull requests are welcome for problems **in this MCP server itself**: setup,
  the authentication flow, tool behavior, and documentation.
- Problems with the AiNote service or the LINE WORKS API (API errors, scopes not available in
  your Developer Console, summaries not being generated, plan eligibility) are outside the scope
  of this project. Please refer to the [LINE WORKS Developers](https://developers.worksmobile.com/)
  documentation and the official LINE WORKS support channels.
- Answers in issues are based on the public LINE WORKS Developers documentation and on behavior
  observed on a production tenant. They are not official statements about the product.
- If this project stops being maintained, the repository will be archived and a notice added here.

**日本語**

- 本プロジェクトは MIT ライセンスで現状のまま提供します。保証や応答期限はなく、個人として保守しています
- Issue / Pull Request は**この MCP サーバー自体の問題** (セットアップ、認証フロー、ツールの挙動、
  ドキュメント) について受け付けます
- AiNote や LINE WORKS API 側の問題 (API エラー、Developer Console でスコープが選べない、
  要約が生成されない、プランの対応状況) は本プロジェクトの範囲外です。
  [LINE WORKS Developers](https://developers.worksmobile.com/) のドキュメントと
  LINE WORKS の公式サポート窓口をご利用ください
- Issue での回答は公開ドキュメントと製品テナントでの動作確認に基づくもので、
  製品についての公式な見解ではありません
- メンテナンスを終了する場合はリポジトリをアーカイブし、ここに記載します
