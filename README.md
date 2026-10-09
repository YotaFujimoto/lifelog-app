# ライフログの自動処理

Notion の「ライフログ」に書き込む GitHub Actions。どちらも外部ライブラリは使わない（Python の標準ライブラリだけ）。

| ワークフロー | スクリプト | いつ（日本時間） | すること |
|---|---|---|---|
| 数学日記を更新 | `math_diary.py` | 毎日 4:17 | 数学ノートから「数学日記」ページを作り直す |
| Fitbitを日次ログに取り込む | `fitbit_sync.py` | 毎日 10:41・22:41 | Fitbit の睡眠と運動を日次ログに書き込む |

失敗すると GitHub からメールが届く。ログ（Actions タブで実行結果をクリック）に原因が出る。

---

## 数学日記

- 毎日 4:17（日本時間）に `math_diary.py` が動く
- 数学ノートの各ページを日付見出しで区切り、日付順に並べて書き込む
  - 数学日記ページ：直近31日分の全文と、月別ページの一覧
  - その子ページ「数学日記 YYYY-MM」：月ごとの全文
- 元の数学ノートには書き込まない。内容が変わっていないページは書き換えない

### 最初の設定

#### 1. Notion のコネクトを作ってトークンを取る

1. Notion の「開発者ツール」（[開発者ポータル](https://app.notion.com/developers/connections)）→「コネクション」タブ →「＋ 新規コネクト」
2. コネクト名に「数学日記」と入れ、認証方法は「アクセストークン」を選んで「コネクトを作成」
   （「個人用アクセストークン」タブのものは期限切れがあり、自分の全ページに触れられるので使わない）
3. 作成後の画面でトークン（長い英数字）をコピーする。コンテンツの読み取り・更新・挿入が許可されていることも確かめる
4. 「ライフログ」ページを開き、右上の「•••」→「コネクト」（または「接続」）から「数学日記」を追加する
   （中の数学ノートと数学日記も対象になる。コネクト側の画面でアクセスするページを選べる場合は、そこで「ライフログ」を選んでもよい）

トークンは、スクリプトがこのコネクトとして Notion を読み書きするための鍵。パスワードと同じ扱いで、GitHub のシークレット以外には貼らない。
漏れたときは、コネクトの画面でトークンを作り直すか、コネクトを削除すれば古いトークンは使えなくなる。

#### 2. GitHub にリポジトリを作る

1. GitHub の「New repository」で、名前（例：`notion-math-diary`）を付け、**Private** を選んで作る
   （公開リポジトリは、60日間コミットがないと定期実行が止まる）
2. 作った直後の画面の「uploading an existing file」を開き、このフォルダの中身をまとめてドラッグして「Commit changes」
   - `math_diary.py`、`test_math_diary.py`、`README.md`、`.github` フォルダ
   - リポジトリに `.github/workflows/math-diary.yml` があることを確かめる。入っていなければ、「Add file」→「Create new file」でファイル名に `.github/workflows/math-diary.yml` と打ち、中身を貼る

#### 3. トークンを登録する

リポジトリの Settings → Secrets and variables → Actions → New repository secret

- Name: `NOTION_TOKEN`
- Secret: 1 でコピーしたトークン

#### 4. 動かしてみる

Actions タブ →「数学日記を更新」→「Run workflow」。緑のチェックが付けば完了で、以後は毎日自動で動く。

### 日付見出しの書き方

数学ノートのページで、その日に書き始める位置に見出しを入れる（見出し1〜4のどれでもよい）。

- `### @今日` → 今日の日付が入る（おすすめ）。後ろに `### @今日 既約⇒準素` のように小見出しを書いてもよい
- `### 2026-09-29`、`### 2026/9/29`、`### 2026年9月29日` も日付として扱う
- `### 9/29`、`### 9月29日（火）` のように年を省くのは、見出しが日付だけのときに限る
- 最初の日付見出しより前に書いた内容は、ページを作った日の記録になる
- 画像・ファイル・子ページは、日記では元のページへのリンクになる（画像の URL が短時間で切れるため）

### 変えたいとき

- 実行時刻：`.github/workflows/math-diary.yml` の `cron`（UTC で書く。日本時間 − 9時間）
- 表示する日数：ワークフローの `env` に `WINDOW_DAYS: "14"` のように足す
- 書き込まずに結果だけ見る：手元で `NOTION_TOKEN=... python3 math_diary.py --dry-run`
- テスト：`python3 -m unittest`

---

## Fitbit の取り込み

Fitbit（Google Health アプリ）の記録を Google Health API で読み、日次ログの次の5列に書き込む。
手入力の列（睡眠・0時前に就寝・中途覚醒なし・眠くない・研究など）には触れない。

| 列 | 中身 | 書き込む行 |
|---|---|---|
| 就寝・起床 | 寝た時刻・起きた時刻 | 起きた日（手入力の「睡眠」と同じ） |
| 睡眠(Fitbit) | 眠っていた時間（時間、小数1桁） | 起きた日 |
| 覚醒(Fitbit) | 就寝から起床までのうち、眠っていなかった時間（時間、小数1桁） | 起きた日 |
| 運動記録 | その日に始めた運動の種類と時間（自動認識を含む）。例：テニス 92分、ウォーキング 21分 | その日 |

- 毎回、直近3日分を書き直す（時計やスマホの同期が遅れても、あとの回で埋まる）。変わっていない列は書き換えない
- 夜中に目が覚めて Fitbit の記録が分かれたときは、前日21時〜当日12時のあいだで3時間以内の間をおいて続く記録を、ひと晩としてまとめる（あいだの時間は覚醒に入る）。夕方のうたた寝と昼寝は入れない。仮眠しか記録がない日は、睡眠の4列を書かない
- Fitbit に値がない列は、空で上書きしない
- 日次ログのページは毎日0時に Notion が自動で作るので、そこに書き込む。スクリプトはページを作らない（同じ日のページが2つできないように）。
  ページがない日はログに出して飛ばす。その日のページを作れば、3日以内なら次の回で入る
- Google Health には書き込まない（読み取りの許可だけを使う）

### 最初の設定（1回だけ。30分ほど）

**進め方**：2026年10月の時点で、Google Health API の案内に「今は新しいプロジェクトを受け付けていない」と書かれている。
そこで、まず「テスト」のまま 1〜4 で使えるかを確かめ、使えたら 5 で本番にして鍵を取り直す。

#### 1. Google Cloud でプロジェクトを作り、Google Health API を有効にする

1. [Google Cloud コンソール](https://console.cloud.google.com/) の上のプロジェクト名 →「新しいプロジェクト」。名前（例：`lifelog`）を付けて作り、そのプロジェクトを選ぶ
2. 左上のメニュー →「API とサービス」→「ライブラリ」で「Google Health API」を検索し、「有効にする」

#### 2. 同意画面を作る（テストのまま）

1. メニュー →「API とサービス」→「OAuth 同意画面」（Google Auth Platform）→「開始」（Get started）
2. アプリ名（例：`ライフログ`）とユーザーサポートメール → 対象は「外部」（External）→ 連絡先メール → ポリシーに同意して「作成」
3. 左の「データアクセス」（Data Access）→「スコープを追加または削除」→「Google Health API」で絞り、次の2つにチェック →「更新」→ 下の「保存」
   - `.../auth/googlehealth.sleep.readonly`（睡眠）
   - `.../auth/googlehealth.activity_and_fitness.readonly`（運動）
4. 左の「対象」（Audience）→「テストユーザー」の「＋ Add users」で、Google Health アプリで使っている Google アカウントを追加 →「保存」

#### 3. OAuth クライアントを作る

1. 左の「クライアント」（Clients）→「クライアントを作成」
2. アプリケーションの種類は「ウェブ アプリケーション」、名前は例えば `fitbit-sync`
3. 「承認済みのリダイレクト URI」に `https://developers.google.com/oauthplayground` を追加して「作成」
4. 表示されたクライアント ID とクライアント シークレットを控える（JSON をダウンロードしてもよい）。
   シークレットはこの画面を閉じると見られない（なくしたら、このクライアントの画面でシークレットを追加する）

#### 4. 鍵（リフレッシュトークン）を取り、使えるか確かめる

1. [OAuth 2.0 Playground](https://developers.google.com/oauthplayground) を開く
2. 右上の歯車（OAuth 2.0 configuration）→「Use your own OAuth credentials」にチェック → 3 のクライアント ID とシークレットを入れて閉じる
3. 左の「Step 1」の一番下の入力欄（Input your own scopes）に次の1行を貼り、「Authorize APIs」

   ```
   https://www.googleapis.com/auth/googlehealth.sleep.readonly https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
   ```

4. Google Health アプリで使っているアカウントを選ぶ →「このアプリは Google で確認されていません」と出たら「詳細」→「（アプリ名）（安全ではないページ）に移動」→ **睡眠と運動の両方にチェック**を入れて「続行」
5. 「Step 2」の「Exchange authorization code for tokens」→「Refresh token」欄の文字列（`1//` で始まる）が鍵
   - テストのあいだは、右側の応答に `refresh_token_expires_in` が出る（7日で切れる鍵）。確かめるだけなのでこれでよい
6. 使えるかを確かめる：「Step 3」の Request URI に `https://health.googleapis.com/v4/users/me/identity` を入れ（HTTP Method は GET）、「Send the request」
   - `200 OK` と `healthUserId` が返れば使える。5 に進む
   - `403` で `API_PRIVATE_PREVIEW_ACCESS_DENIED` が出たら、今はこのプロジェクトでは使えない（設定の誤りではないので、やり直しても直らない）。ここで止める
   - `400` で `ACCOUNT_NOT_LINKED` が出たら、選んだアカウントが Google Health アプリのアカウントと違う。4-3 から、アプリのアカウントでやり直す

クライアント シークレットとリフレッシュトークンは、パスワードと同じ扱い。GitHub のシークレット以外には貼らない。
漏れたときは、Google アカウントの「サードパーティ製のアプリとサービスとの接続」でこのアプリのアクセスを削除すれば、その鍵は使えなくなる。

#### 5. 本番にして、鍵を取り直す

「テスト」のままだと鍵が7日で切れる。本番にするには、Google の「ブランディング」にホームページとプライバシーポリシーの URL が要る（利用規約は空でよい）。GitHub Pages で1ページ作る。

1. GitHub の「New repository」で名前を `lifelog-app` にし、**Public** で作る →「uploading an existing file」で `index.html`（ホームページ兼プライバシーポリシー）を上げて「Commit changes」
2. そのリポジトリの Settings → Pages →「Deploy from a branch」で Branch を `main`・`/ (root)` にして「Save」。
   1〜2分で `https://（GitHub のユーザー名を小文字で）.github.io/lifelog-app/` が開けるようになる
3. Google Cloud の「ブランディング」（Branding）で次を入れて「保存」
   - アプリ名・ユーザーサポートメール・デベロッパーの連絡先メール（空なら入れる）
   - アプリケーションのホームページ と プライバシーポリシーのリンク：2 の URL（両方同じでよい）
   - 承認済みドメイン：`（ユーザー名の小文字）.github.io`
4. 「対象」（Audience）→「アプリを公開」（Publish app）→ 確認。「本番環境」（In production）になればよい
   - 押せないときは、ボタンにマウスを乗せると、足りない項目が表示される
   - 自分だけで使うので、Google の審査（確認）は受けなくてよい。認証のときに「確認されていないアプリ」の警告が出るだけ
5. 4 の 1〜5 をもう一度やって、新しい Refresh token を取る。今度は応答に `refresh_token_expires_in` が**ない**ことを確かめる

#### 6. GitHub に登録する

1. このリポジトリの Settings → Secrets and variables → Actions → New repository secret で3つ登録する
   - `GOOGLE_CLIENT_ID`：3 のクライアント ID
   - `GOOGLE_CLIENT_SECRET`：3 のクライアント シークレット
   - `GOOGLE_REFRESH_TOKEN`：5 で取り直したリフレッシュトークン
   - `NOTION_TOKEN` は数学日記のものをそのまま使う（コネクトは「ライフログ」につないであるので、日次ログにも書き込める）
2. 「Add file」→「Upload files」に `fitbit_sync.py`・`test_fitbit_sync.py`・`README.md`・`.github` フォルダをドラッグして「Commit changes」
   - `.github/workflows/fitbit-sync.yml` があることを確かめる。なければ「Add file」→「Create new file」でファイル名に `.github/workflows/fitbit-sync.yml` と打ち、中身を貼る

#### 7. 動かしてみる（過去の分もまとめて入れる）

Actions タブ →「Fitbitを日次ログに取り込む」→「Run workflow」→ 日数に `20` を入れて「Run workflow」。
緑のチェックが付いたら、日次ログに入っているかを見る。以後は毎日自動で動く。
（9/27 以前の日は日次ログにページがないので、ログに「ページがないので飛ばした」と出る。問題ない）

### ふだん

- 何もしなくてよい。睡眠の手入力（睡眠・0時前に就寝・中途覚醒なし・眠くない）は今まで通り続ける
- スマホの Google Health アプリが同期していないとデータが届かない（アプリを開けば同期する）
- 自動で認識されない運動（筋トレなど）は、時計で運動を開始しておけば記録される

### 認証をやり直す（「Google の認証が切れている」と出て止まったとき）

1. 上の 4 をもう一度やる（Playground の歯車の設定が消えていたら、クライアント ID とシークレットを入れ直す）
   - シークレットを控えていなければ、3 のクライアントの画面でシークレットを追加し、GitHub の `GOOGLE_CLIENT_SECRET` も新しいものに更新する（古いシークレットを消すと、登録してある方は使えなくなる）
2. GitHub の Settings → Secrets and variables → Actions → `GOOGLE_REFRESH_TOKEN` の鉛筆 → 新しいトークンを貼って「Update secret」
3. Actions タブから「Run workflow」で動かす（止まっていたあいだの分も入れるなら、日数を増やす）

### うまく動かないとき

ログの最後の数行に、原因と直し方が出る（トークンやシークレットはログに出ない）。

- 403 `API_PRIVATE_PREVIEW_ACCESS_DENIED`：Google がこのプロジェクトに Google Health API を開いていない。設定では直らない
- 403（それ以外）：Google Health API が有効か、4-4 で両方にチェックを入れたか
- 400 `ACCOUNT_NOT_LINKED`：鍵を取ったアカウントと、Google Health アプリのアカウントが同じか
- 「日次ログに列「…」がない」：Notion で列の名前を変えたら、`fitbit_sync.py` の「日次ログの列名」も同じにする

### 変えたいとき

- 実行時刻：`.github/workflows/fitbit-sync.yml` の `cron`（UTC で書く。日本時間 − 9時間）
- 書き直す日数：手動で実行するときに日数を入れる（ふだんの3日は `fitbit-sync.yml` の `'3'`）
- 書き込まずに結果だけ見る：手元で4つの環境変数を入れて `python3 fitbit_sync.py --dry-run`
- テスト：`python3 -m unittest`
