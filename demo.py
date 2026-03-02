#!/usr/bin/env python3
"""デモ用スクリプト - モックデータで動作確認"""

from boomerang.kokkai_api import Speech
from boomerang.analyzer import BoomerangResult
from boomerang.formatter import format_terminal, format_sns

# 石破茂の実際の国会発言に基づくモックデータ
mock_speeches = [
    Speech(
        speaker="石破茂",
        date="2015-07-15",
        name_of_house="衆議院",
        name_of_meeting="平和安全法制特別委員会",
        speech_text="集団的自衛権の行使容認については、憲法改正によるべきだと私は考えております。解釈変更ではなく、国民の理解を得た上で、正々堂々と憲法改正を行うべきです。",
        speech_url="https://kokkai.ndl.go.jp/",
        session=189,
    ),
    Speech(
        speaker="石破茂",
        date="2024-10-09",
        name_of_house="衆議院",
        name_of_meeting="本会議",
        speech_text="安全保障環境の変化に対応するため、現行の安全保障法制を基盤として、さらなる防衛力の強化を進めてまいります。",
        speech_url="https://kokkai.ndl.go.jp/",
        session=214,
    ),
    Speech(
        speaker="石破茂",
        date="2017-11-01",
        name_of_house="衆議院",
        name_of_meeting="予算委員会",
        speech_text="国会の議論を軽視し、十分な審議時間を確保しないまま採決を強行することは、民主主義の根幹を揺るがすものであり、断じて認められません。",
        speech_url="https://kokkai.ndl.go.jp/",
        session=195,
    ),
    Speech(
        speaker="石破茂",
        date="2024-11-20",
        name_of_house="衆議院",
        name_of_meeting="予算委員会",
        speech_text="限られた会期の中で迅速に結論を得ることも、国会の重要な責務であります。",
        speech_url="https://kokkai.ndl.go.jp/",
        session=215,
    ),
    Speech(
        speaker="石破茂",
        date="2018-06-15",
        name_of_house="衆議院",
        name_of_meeting="本会議",
        speech_text="政治資金の透明性を高め、国民への説明責任を果たすことが、政治家として最も重要な責務であります。裏金問題は徹底的に解明されるべきです。",
        speech_url="https://kokkai.ndl.go.jp/",
        session=196,
    ),
    Speech(
        speaker="石破茂",
        date="2024-12-05",
        name_of_house="衆議院",
        name_of_meeting="本会議",
        speech_text="旧文通費の使途公開について、各党間の協議を見守りつつ、慎重に検討してまいりたいと考えております。",
        speech_url="https://kokkai.ndl.go.jp/",
        session=215,
    ),
]

# Claude APIの分析結果をシミュレート
mock_results = [
    BoomerangResult(
        speech_a=mock_speeches[0],
        speech_b=mock_speeches[1],
        summary_a="集団的自衛権は憲法改正によるべき、解釈変更は認めない",
        summary_b="現行の安保法制を基盤に防衛力を強化する",
        contradiction="かつて憲法改正なき安保法制に反対していたが、首相就任後は現行法制を前提に推進する立場に転換",
        score=82,
    ),
    BoomerangResult(
        speech_a=mock_speeches[2],
        speech_b=mock_speeches[3],
        summary_a="十分な審議時間なき採決強行は民主主義の根幹を揺るがす",
        summary_b="限られた会期で迅速に結論を得ることも国会の責務",
        contradiction="野党時代は審議時間の確保を強く主張していたが、与党側になると迅速な採決の必要性を強調",
        score=88,
    ),
    BoomerangResult(
        speech_a=mock_speeches[4],
        speech_b=mock_speeches[5],
        summary_a="政治資金の透明性と説明責任が最も重要な責務",
        summary_b="旧文通費の使途公開は慎重に検討したい",
        contradiction="政治資金の透明性を強く訴えていたが、首相就任後は文通費公開に消極的な姿勢",
        score=85,
    ),
]

# スコア降順にソート
mock_results.sort(key=lambda r: r.score, reverse=True)

# ターミナル表示
print(format_terminal("石破茂", mock_results))

# SNS投稿用
print("\n📋 SNS投稿用テキスト:")
print("-" * 40)
sns_text = format_sns("石破茂", mock_results)
print(sns_text)
print("-" * 40)
print(f"（{len(sns_text)}文字）")
