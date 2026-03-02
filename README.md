# 🪃 ブーメランチェッカー

国会議事録APIとClaude APIを使って、国会議員の過去の矛盾・ブーメラン発言を自動検出するCLIツールです。

## 仕組み

1. [国会会議録検索システムAPI](https://kokkai.ndl.go.jp/api.html)から指定した議員の発言を取得
2. Claude API（Anthropic）で発言間の矛盾を分析・スコアリング
3. 結果をターミナルに表示（SNS投稿用フォーマットにも対応）

## セットアップ

### 前提条件

- Python 3.10 以上
- [Anthropic API キー](https://console.anthropic.com/)

### インストール

```bash
git clone https://github.com/your-repo/boomerang-checker.git
cd boomerang-checker
pip install -r requirements.txt
```

### 環境変数の設定

```bash
export ANTHROPIC_API_KEY="your-api-key-here"
```

## 使い方

### 基本的な使い方

```bash
# 議員名を引数で指定
python main.py 岸田文雄

# 対話的に議員名を入力
python main.py
```

### オプション

```bash
# 取得する発言数を指定（デフォルト: 50）
python main.py 岸田文雄 --max-speeches 100

# 日付範囲を指定
python main.py 岸田文雄 --from-date 2020-01-01 --until-date 2024-12-31

# SNS投稿用フォーマットも表示
python main.py 岸田文雄 --sns

# APIキーを直接指定
python main.py 岸田文雄 --api-key sk-ant-xxxxx
```

## 出力例

### ターミナル表示

```
========================================================
  🪃 ブーメランチェッカー  議員名：〇〇太郎
========================================================

  🔥 ブーメラン #1
  矛盾スコア: [████████░░] 85点

  📅 発言A（2020年 予算委員会）
  「消費税の増税は絶対にすべきではない」

  📅 発言B（2024年 本会議）
  「消費税の引き上げは避けられない選択」

  💬 消費税に対する立場が完全に逆転している

--------------------------------------------------------

  検出数: 1件  平均矛盾スコア: 85点

========================================================
```

### SNS投稿用フォーマット（`--sns` オプション）

```
🪃 ブーメランチェッカー：〇〇太郎

🔥 ブーメラン #1（矛盾スコア: 85点）
発言A（2020年）：「消費税の増税は絶対にすべきではない」
発言B（2024年）：「消費税の引き上げは避けられない選択」
→ 消費税に対する立場が完全に逆転している

検出数: 1件

#ブーメランチェッカー #国会 #議事録
```

## プロジェクト構成

```
boomerang-checker/
├── main.py                  # CLIエントリーポイント
├── requirements.txt         # 依存パッケージ
├── README.md
└── boomerang/
    ├── __init__.py
    ├── kokkai_api.py        # 国会会議録API クライアント
    ├── analyzer.py          # Claude API 矛盾分析
    └── formatter.py         # 出力フォーマッター
```

## 使用API

- **国会会議録検索システム API**: https://kokkai.ndl.go.jp/api.html
  - 国立国会図書館が提供する無料API
  - 第1回国会（1947年）からの全会議録を検索可能
- **Anthropic Claude API**: https://docs.anthropic.com/
  - 発言間の矛盾検出・スコアリングに使用

## 注意事項

- 国会会議録APIは無料ですが、サーバー負荷軽減のためリクエスト間隔を設けています
- Claude APIの利用には別途APIキーと利用料金が必要です
- 矛盾スコアはAIによる推定値であり、文脈や状況の変化を完全には考慮できません
- 本ツールは議論・分析目的であり、特定の政治的立場を支持するものではありません

## ライセンス

MIT
