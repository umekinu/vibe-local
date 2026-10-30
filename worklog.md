# vibe-local v2 リニューアル worklog

開始: 2026-07-12

## 方針（ユーザー決定）
- 名前は **vibe-local のまま** v2 に更新（ochyaiCode改名案は撤回、2026-07-12）
- フロントエンド: Claude Code流用をやめ、**OpenCode**（MIT, マルチプロバイダネイティブ）+ vaporwaveカスタムテーマで「Claude Codeより斬新なTUI」
- 目玉機能（教室LANモード、tool calling救済、自動切替、音声等）はブレスト4本の結果を見て選定
- モデル: 2026-07時点の最新に刷新（qwen3.6 / qwen3-coder-next / gpt-oss:20b / qwen3.5）
- ルーティング: 自作1000行プロキシ廃止 → Ollamaネイティブ `/v1/messages`・OpenAI互換直結。オンライン/オフラインのヘルスゲート式自動ルーティング

## サーベイ確定事項（2026-07-12実機検証済み）
- Ollama 0.30.7 で `/v1/messages` ネイティブ動作確認済み（thinkingブロック付き応答を実測）
- Ollamaタグ実在確認: `qwen3.6:35b-a3b`(24GB) / `qwen3-coder-next`(52GB) / `gpt-oss:20b`(14GB) / `qwen3.5:4b`
- 旧構成の重大バグ: haiku系モデル名素通しで500（実ログ）、num_ctx未設定、system prompt 4000字切り詰め、max_tokens 4096 cap → **プロキシ廃止で全て消滅**
- `~/.config/vibe-local/config` は別ツール vibe-coder.py が上書きしていた（設定ドリフト）

## 新モデルマトリクス（RAM自動判定）
| RAM | モデル | サイズ |
|---|---|---|
| 8GB | qwen3.5:4b | ~2.7GB |
| 16GB | gpt-oss:20b | 14GB |
| 32GB+ | qwen3.6:35b-a3b | 24GB |
| 80GB+ | qwen3-coder-next | 52GB |

## タスクリスト
- [x] OpenCode公式ドキュメント精査（config/providers/themes/permissions）
- [x] OpenCodeインストール（brew 1.17.15）・Ollama接続の実機検証
- [x] vaporwaveカスタムテーマ作成（themes/vibe-vaporwave.json、50キー完全）
- [x] opencode.json 生成ロジック（OPENCODE_CONFIG分離、ollama provider、permission ask/allow）
- [x] vibe-local.sh v2 全面書き換え（OpenCode起動・--auto・--classic・--serve/--attach・--doctor・--rebuild）
- [x] num_ctx焼き込みエイリアス方式（vibe-coder/vibe-fast、ollama create、実機動作確認）
- [x] proxy/localllm/bench/旧tests → legacy/ 退避（legacy/README.mdに廃止理由）
- [x] install.sh v2 手術的更新（モデルマトリクス、OpenCode導入、Node/Claude Code削除、v2.conf）
- [x] README v2（4言語構成維持、v2セクション追加）
- [x] テスト tests/run.sh 13件 全通過
- [x] E2E: `vibe-local --model qwen3-coder:30b -y -p "..."` → Writeツールでhello.py生成・実行成功
- [x] TUI視覚検証: PTYキャプチャでvibe-vaporwave固有色(#170e21/#b967ff)のレンダリング確認
- [x] モデルpull完了（qwen3-coder-next 51GB / qwen3.6:35b-a3b 23GB / gpt-oss:20b 13GB / qwen3.5:4b 3.4GB）※qwen3.6は初回DNS断でsilent fail→再pullで取得。`pull | tail`はexit codeを隠すので注意
- [x] デフォルトパスE2E: 本物のqwen3-coder-next(79.7B)で fizzbuzz生成→モデル自身が実行検証、ロード込み36秒
- [x] ensure_aliasバグ修正: create失敗が既存エイリアスに隠れる問題→exit code判定に変更
- [x] ~/.local/bin/vibe-local にv2配備。旧vibe-coderエンジンは vibe-local-v1 に退避
- [ ] 目玉機能の実装（Tier1候補からユーザー選定待ち）
- [ ] git commit（ユーザー確認後）

## 実装メモ
- OpenCodeテーマはtui.jsonの"theme"キーで有効化。既存tui.jsonがある場合はthemeキーが無い時のみ追記
- OPENCODE_CONFIG環境変数で設定分離 → ユーザーの素のOpenCode設定(~/.config/opencode/opencode.json)を汚さない
- Ollama 0.30.7はモデルネイティブMAXコンテキストでロード(qwen3-coder:30b→262144/45GB実測) → 低RAM機OOM対策としてnum_ctx焼き込みエイリアス必須
- --model の一時指定はv2.confに恒久化しない(MODEL_OVERRIDEフラグ)
- grep -q + pipefail はSIGPIPE(141)でテストが偽陽性失敗 → grep -c を使う

## v2.0 追加分
- [x] 起動画面「零位相→物化」実装（vibe-local.sh内蔵python、24bitカラー8フレーム: 太陽が昇る＋ロゴがノイズから物化＋遠近グリッド＋ネオンチップ表示）。非TTY/狭幅/VIBE_LOCAL_NO_SPLASH=1でテキストにフォールバック。テスト14/14

## v2.1 (2026-07-13) — 脱opencodeブランディング・黒灰UI・サクサク化
- [x] **debrand (tools/debrand.py)**: opencodeバイナリ(Bunコンパイル、JSソース埋め込み)の可視ロゴを同一バイト長置換で "vibe code" に差し替え → `~/.local/lib/vibe-local/bin/vibe-tui` 生成 + codesign -s - 再署名。対象: 平置きロゴ×1 / TUI分割ロゴleft×2 / 縮小ロゴ×1 / opencode.ai言及の/share Tip×1。バージョン変更で自動再パッチ(sig=version|path)。失敗時は素のopencodeにフォールバック
- [x] ウィンドウタイトル: 公式env `OPENCODE_DISABLE_TERMINAL_TITLE=1` + ランチャーが `\033]0;vibe-local\007` を出力（パッチ不要だった）
- [x] **黒灰テーマ vibe-null** (themes/vibe-null.json) を新既定に。tui.jsonがvibe-vaporwave/未設定なら一度だけ移行。`--theme null|vaporwave` フラグ追加
- [x] スプラッシュをモノクロ化(白銀太陽・灰グリッド) + 高速化(8f×70ms→6f×45ms)
- [x] **サクサク化**: warmup_model()で起動と同時に `/api/generate {keep_alive:"2h"}` を裏で発火（TUI起動中にモデルロード完了→初回応答の数十秒待ちが消える）。自前でollama起動する場合は OLLAMA_KEEP_ALIVE=2h / OLLAMA_FLASH_ATTENTION=1
- [x] `--fast` フラグ: vibe-fast(小型)を主役に
- [x] テスト 14→20件 (テーマ全JSON検証・黒灰彩度チェック・debrand実バイナリ適用・パッチ後ロゴ非表示)
- [x] PTY実機検証: ホームロゴ"vibe code"・タイトル"vibe-local"・コマンドパレットにopencode表記なし・vibe-null色(#0a0a0c)レンダリング確認
- 知見: opencodeバイナリはBunバンドルでJSソースが平文埋め込み。ロゴは`█`エスケープ形式。同一バイト長なら文字列リテラル置換が安全(オフセット不変)。macOS arm64は改変後 `codesign --force -s -` 必須

## v2.2 (2026-07-13) — 応答性の根本解決 (vibe-auto ルーティング)
「こんにちは？に30秒」の調査で判明したボトルネック3つと対策:
1. **タイトル生成のthinking暴走 (主犯)**: opencodeはsmall_modelでセッションタイトルを生成するが、qwen3.5:4bがthinkingで1000-2000トークン思考ループ → 実測15-29秒。Ollamaは同一モデルのリクエストを直列処理するため本編チャットも道連れ。→ vibe-routerが**小型モデル宛の全リクエストにreasoning_effort:"none"を注入** → 0.2秒に
2. **システムプロンプト肥大**: opencodeが~/.claude/skills等からスキル63個を注入し69KB(17k+トークン)。vibe-fastのnum_ctx16384を超えてコンテキストシフト地獄(3分超の実測も)。→ `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`+`OPENCODE_DISABLE_EXTERNAL_SKILLS=1`で30KBに半減、SMALL_CTX 16384→32768
3. **プロンプト評価が毎回フル**: 80Bで15kトークン≒17秒。→ 雑談を小型に振ればプロンプト評価も4Bの速度(6秒、キャッシュ後0.7秒)

- [x] **tools/vibe-router.py**: stdlibのみのパススルーHTTPルーター(127.0.0.1:11435→Ollama)。仮想モデル`vibe-auto`を内容で振り分け: ツール使用履歴/添付/長文(>240字)/コードブロック/作業キーワード→main、それ以外の短い雑談→fast。判定不能は常にmain(安全側)。SSEはchunked透過。形式変換なし=v1プロキシの轍は踏まない
- [x] ランチャー統合: ensure_router()デーモン管理(sigベース自動再起動、失敗時は直結フォールバック)、opencode.jsonのbaseURL→ルーター+既定モデルvibe-auto(おまかせ)、`--no-router`フラグ
- [x] 両モデルwarmup、スキル無効化env、OPENCODE_DISABLE_MODELS_FETCH=1(オフライン堅牢化)
- [x] テスト23件(ルーティング判定ユニット・health起動)
- [x] **E2E実測: TUI挨拶 30秒→3.3秒 / コーディング初回 5.7秒**(router log: chat→vibe-fast, work-keyword→vibe-coder, title(pinned)→thinkingオフ)
- 知見: `opencode run`ワンショットは並行プロセスやSIGKILL残骸でinitが不安定(TUIは無問題)。計測は必ずプロセス掃除してから。qwen3.5:4bはreasoning_effort:"none"(Ollama /v1)で思考オフにできる

## kaizen 第1段階 (2026-10-30) — 学び続けるループの 検証→記録→抽出
ROADMAPの失敗パターン(P0/P1)は「プロンプトに規則を足す」で直そうとして効果未確認のまま止まっていた。
ループを閉じるため、まず失敗を自動で見つけて数える所まで作る (反映は第2段階、人が承認)。
- [x] **検証**: vibe-router が /chat/completions の応答を素通ししながら写しを取り判定。対象はツールを渡したエージェント応答: text_only(P0-1, 率で見る) / ends_with_question(P0-2) / asks_user_to_run(P0-3) / simplified_zh(P1-4, 日本語の会話のみ) / empty_response / slow(>60s) / upstream_error / parse_error
- [x] **記録**: `~/.local/state/vibe-local/kaizen/events.jsonl` (0600) に1応答1行。種類・件数・時間・モデルだけで **会話本文は残さない** (機密データを扱う環境で使うため)。`VIBE_LOCAL_NO_KAIZEN=1` で無効
- [x] **抽出**: tools/vibe-kaizen.py — モデル×パターンの件数と率を前の期間と比較し候補に印 (2回以上 / 目標率超え / 悪化)。`vibe-local --kaizen` で表示、起動時は候補があれば1行だけ。何も書き換えない
- [x] ルーターの sig に本体のチェックサムを追加 (ルーター更新時に旧デーモンが残らないように)
- [x] テスト 17→22件 (判定ユニット・偽の上流でのE2E・本文が残らないこと・ファイル権限・集計)。わざと壊した4版で該当テストが落ちることを確認
- [ ] 実機確認 (本物のモデルで数セッション → `--kaizen`)。text_only の目標20%はROADMAPの指標どおりだが、実データでうるさければ見直す
- [ ] 第2段階: パターン台帳(patterns.json)・`kaizen promote`・反映(ルーターの仕組み優先/AGENTS.md)・格上げ後の再発と前後の率の自動比較
- [ ] 第3段階: Ground-Truth TDD (答えのわかる課題の自己テストを同じ形で記録)

## 目玉機能候補（ブレスト統合済み、ユーザー選定待ち）
- Tier1(プラグインで可): あんぜんゲート(permission.askフック) / 教室ダッシュボード(serve+SDK/SSE) / 刊=セッションzine化 / 偶発性オラクル(ochiai-v20)
- Tier2(技術背骨): Ground-Truth TDDループ / Capability Probe / モデル固有失敗メモリ
- Tier3(要フォーク): TUI内演出(ヌル庵・思考波形・砂紋diff・百鬼夜行)、grammar強制。二画面プロジェクションのみplugin圏
- OpenCodeプラグインAPI要点: 25フック、permission.askでallow/deny自動化可、TUI描画介入は不可(tui?: never)、serve+SDKで外部ダッシュボード正攻法

## 進捗ログ
- 2026-07-12 14:1x: サーベイ4本完了（コード監査・モデル・ランタイム・TUI）。方針決定
- 2026-07-12 14:2x: Ollama /v1/messages 実機OK。モデル3本pull開始（バックグラウンド）
- 2026-07-12 14:3x: ユーザー決定: 名前はvibe-localのまま最新化。ochyaiCode改名は撤回
- 2026-07-12 15:0x: v2実装完了（ランチャー/テーマ/installer/README/legacy/tests）。E2E・視覚検証通過
