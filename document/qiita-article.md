---
title: 個人開発で「育児情報配信LINE Bot」を作った話 〜要件定義からDynamoDBパーソナライズ・監視設計まで一気通貫でやってみた〜
tags:
  - AWS
  - Lambda
  - DynamoDB
  - サーバーレス
  - 個人開発
private: false
updated_at: ''
id: null
organization_url_name: null
slide: false
ignorePublish: false
---

## この記事について

毎朝8時、LINEに「今日の育児のお役立ち情報」が子供ごとに届く個人開発サービスを、要件定義からAWSインフラ構築まで一人で作りました。

この記事では、**「なぜその設計にしたか」の意思決定プロセス**を中心に書きます。特に力を入れたのは以下の3点です。

1. **DynamoDBを使ったパーソナライズ設計**(配信内容の履歴管理・評価スコアリング)
2. **ログ設計とCloudWatchアラート発報**(失敗に気づける仕組み)
3. **Lambda + EventBridge + DynamoDBによる、低コストでメンテしやすいサーバーレス構成**

コードとインフラ定義(CloudFormation)はすべて公開しています。

- リポジトリ: https://github.com/badorisu0555/childcare-info

要件定義書のフルバージョンや、ログ設計・アラーム設計だけを深掘りした補足記事も同リポジトリの`document/`配下に置いているので、興味がある方はあわせてどうぞ。

## 1. 作ったもの

### 解決したい課題

育児をしていると、「これって他の家庭はどうしてるんだろう」「そろそろ健診の時期だったかも」といった、**見落としがちだけど後で役に立つ情報**に日々の忙しさの中で気づきにくい、という課題があります。

そこで、子供の月齢・人数・親の価値観や悩みに合わせてパーソナライズした育児情報を、毎朝LINEで自動配信するサービスを作りました。

### ゴールの定義

個人開発でも「作って終わり」にしないよう、最初に定量的なゴールを1つだけ決めました。

> 配信するコンテンツのうち、**50%以上が「役立つ」と定義される評価**を得ること
> (「役立つ」の定義 = 5段階評価で4以上)

このゴールが、後述する「フィードバックをどう設計に反映するか」という技術的な工夫の出発点になっています。

### 要求一覧(抜粋)

要件定義の段階で、以下の6つの要求に整理しました。

| No | 要求 |
|---|---|
| 1 | 毎日、育児に関する役立つ情報が配信されること(毎朝8時・毎日配信は絶対) |
| 2 | 子供の年齢・月齢・人数、過去の評価傾向、ユーザーの価値観などでパーソナライズされること |
| 3 | コンテンツごとに1〜5段階でフィードバックできること |
| 4 | 配信形式はLINE(Messaging API) |
| 5 | コンテンツの重複を回避すること(似た話題を連日配信しない) |
| 6 | 季節性・イベントを考慮すること(インフルエンザ流行期、健診の時期など) |

「要求を積み上げてから機能を作る」という順番を守ったことで、後述するDynamoDBのテーブル設計やプロンプト設計が、すべて要求のどれかに紐づく形になっています。技術選定の理由を後から説明できるようにするため、要件定義書と設計判断の補足はドキュメントとして残し、コードと一緒にリポジトリで管理しています。

## 2. 全体アーキテクチャ

構成要素は以下の通りです。個人開発かつ最大想定50人規模というスコープに対して、過剰にならない構成を意識しました。

| コンポーネント | 役割 |
|---|---|
| DynamoDB(Table1) | 配信コンテンツの内容・カテゴリ・日時・評価を管理 |
| DynamoDB(Table2) | ユーザーごとの子供情報・パーソナル情報を管理 |
| DynamoDB(Table3) | カテゴリ別の評価スコア(合計・件数)を管理 |
| Lambda(配信バッチ用) | DynamoDB取得→プロンプト組み立て→LLM呼び出し→LINE配信を一括処理 |
| Lambda(Webhook受信用) | LINEからの評価(postback)を受信し、DynamoDBを更新 |
| EventBridge | 毎朝の配信バッチをスケジューリング |
| Lambda Function URL | LINE Webhookの受け口(公開HTTPSエンドポイント) |
| SSM Parameter Store | LINEのトークン・LLM APIキーを暗号化して管理 |
| CloudWatch Logs/Alarms + SNS | 実行ログの監視とメール通知 |

設計判断でこだわったのは、**「なぜそれを選んだか」を毎回言語化すること**です。例えば以下の2つは、複数の選択肢を比較した上で決めています。

**Lambda Function URLを選んだ理由**
LINEのWebhookを受けるには公開HTTPSエンドポイントが必須ですが、今回はスロットリングやカスタム認可などAPI Gatewayの高度な機能は不要でした。AWS公式でも、シンプルなWebhook受信にはFunction URLが適していると案内されており([AWS公式チュートリアル](https://docs.aws.amazon.com/lambda/latest/dg/urls-webhook-tutorial.html))、追加コストなしでAPI Gatewayより有利という点も含めて採用しました。なお、LINEはWebhookに`X-Line-Signature`ヘッダーで署名を付けてくるため、Lambda側で署名検証を実装しています(後述のコード参照)。

**SSM Parameter Storeを選んだ理由(Secrets Managerではなく)**
Secrets Managerはシークレット1つあたり月額$0.40+API呼び出し課金が発生しますが、Parameter Store(標準パラメータ・SecureString)は同じKMS暗号化・IAMアクセス制御を無料で提供します([料金比較の参考記事](https://ranthebuilder.cloud/blog/secrets-manager-vs-parameter-store-which-one-should-you-really-use/))。自動ローテーションやクロスアカウント共有が不要な場合はParameter Storeが推奨されており、今回のAPIキー管理はこの条件に合致するため採用しました。

こうした「比較した上で選ぶ」進め方は、個人開発だからこそ全部自分でやる必要がある一方、要件→設計判断→実装が1本の線でつながっていることを説明しやすいという副産物もありました。

## 3. 一番こだわった部分: DynamoDBを軸にしたパーソナライズ設計

今回のサービスで最も工夫したのが、**DynamoDBを「単なる保存先」ではなく「パーソナライズのための状態管理装置」として設計する**ことです。

### 3つのテーブルの役割分担

```
Table1(delivery_id が主キー): 配信したコンテンツ本文・カテゴリ・評価スコアの履歴
Table2(user_id が主キー)     : 子供の年齢・人数・親の価値観などのプロフィール
Table3(user_id が主キー)     : カテゴリ別の評価スコア(sum/countで平均を算出)
```

Table1には、ユーザーごとの配信履歴を高速に取り出すためのGSI(グローバルセカンダリインデックス)を張っています。

```json
"GlobalSecondaryIndexes": [
  {
    "IndexName": "GSI1",
    "KeySchema": [
      { "AttributeName": "user_id", "KeyType": "HASH" },
      { "AttributeName": "delivered_at", "KeyType": "RANGE" }
    ]
  }
]
```

`delivery_id`(主キー)だけでは「あるユーザーの直近5日分」を取り出せないため、`user_id`と`delivered_at`の複合キーでGSIを作りました。「重複回避」という要求(直近の配信履歴を見て似た話題を避ける)を、DynamoDBのクエリ設計に落とし込んだ形です。

### パーソナライズの実装: 配信バッチLambda

配信バッチのコアロジックは以下の流れです(コメントは実際のコードに書いているものです)。

```python
def lambda_handler(event, context):
    # Table1: delivery_id(PK)とは別に、「あるuser_idの直近N日分」を検索したいので、
    # GSI1(PK: user_id, SK: delivered_at)を作成しました。
    deliverycontent = get_dynamo_data(TABLE1_NAME, line_user_id, index_name="GSI1", days=5)
    deliverycontent = [{"サマリー": d["summary"], "カテゴリー": d["category"]} for d in deliverycontent]

    # Table2/Table3はuser_idで一意に取得できるのでindex_nameは不要
    userprofile = get_dynamo_data(TABLE2_NAME, line_user_id)
    categoryscore = get_dynamo_data(TABLE3_NAME, line_user_id)
    categoryscore = process_category_scores(categoryscore)  # 上位カテゴリを算出

    # プロフィール・直近履歴・カテゴリ傾向をプロンプトに埋め込んでLLMに生成させる
    answer = create_childhood_content(deliverycontent, userprofile, categoryscore)
    ...
```

`process_category_scores`は、Table3に貯めた「カテゴリごとの評価合計(`{category}_sum`)・件数(`{category}_count`)」から平均スコアを計算し、上位カテゴリを抽出する関数です。

```python
def process_category_scores(categoryscore):
    categories = set()
    for key in categoryscore:
        if key.endswith("_sum"):
            categories.add(key[:-len("_sum")])
        elif key.endswith("_count"):
            categories.add(key[:-len("_count")])
    result = {
        cat: float(categoryscore[f"{cat}_sum"]) / float(categoryscore[f"{cat}_count"])
        for cat in categories
    }
    # 上位3カテゴリだけをLLMへのプロンプトに渡す
    categoryscore = sorted(result.items(), key=lambda x: x[1], reverse=True)[:3]
    return [{"カテゴリー": CATEGORY_NAME_MAP[cat], "スコア": score} for cat, score in categoryscore]
```

こうして作った「直近5日の配信履歴」「プロフィール」「カテゴリ別評価傾向(上位3件)」の3つを、そのままLLMへのプロンプトのコンテキストとして渡しています。

```
# カテゴリ別評価傾向(上位カテゴリほど好まれている)
{categoryscore}

# 直近5日間の配信履歴(同じカテゴリ、同じような配信内容は避ける。また作成コンテンツの書き方の参考とする)
{deliverycontent}
```

「重複回避」と「好みの反映」という2つの要求を、**追加のML基盤を持たずにDynamoDBのデータとプロンプトだけで実現している**のがこの部分のポイントです。

### 評価の反映: Webhook受信Lambda

ユーザーがLINEでコンテンツを評価すると、LINEの`postback`イベントとしてWebhook用Lambdaに届きます。ここでの工夫は2つです。

**(1) 二重評価防止をConditionExpressionで表現する**

```python
# ConditionExpressionは「更新して良い条件」を指定するもの。
# attribute_not_exists(rated): Lambda1のput_item時点ではratedを設定していないので、
#   初回評価時はこの条件でヒットする
# どちらも満たさない(=rated=trueが既に入っている)場合は例外が発生し、更新は行われない
response = table1.update_item(
    Key={"delivery_id": delivery_id},
    UpdateExpression="SET score = :score, rated = :true",
    ConditionExpression="attribute_not_exists(rated) OR rated = :false",
    ExpressionAttributeValues={":score": score, ":true": True, ":false": False},
    ReturnValues="ALL_NEW"
)
```

LINEはWebhookイベントを再送してくることがあるため、「同じ評価が2回加算されてスコアが歪む」という事故を、アプリ側のロジックではなくDynamoDBの条件付き書き込みで防いでいます。

**(2) カテゴリスコアの加算をADDでアトミックに行う**

```python
# ADDアクションを使うことで、「取得→加算→書き込み」を1回のアトミックな操作で行える。
# 同時に複数の評価が来ても、値が上書きされず正しく合算される
table3.update_item(
    Key={"user_id": user_id},
    UpdateExpression=f"ADD {category_en}_sum :score, {category_en}_count :one",
    ExpressionAttributeValues={":score": score, ":one": 1}
)
```

`GetItem`→加算→`PutItem`という3ステップにすると、複数の評価がほぼ同時に来た場合にレースコンディションでスコアが失われるリスクがあります。DynamoDBの`ADD`アクションを使うことで、これを1回のAPI呼び出しでアトミックに解決しています。

### フィードバックをどう活かすか: 「軸A・軸B」で整理した設計

複数のアプローチを比較検討した結果、フィードバックの活用方法を独立した2つの軸に整理しました(個人開発+コスト最適化の方針から、ファインチューニング/RLHFは明確に対象外としています)。

| 軸 | 内容 | 状態 |
|---|---|---|
| 軸A: 何を配信するか | カテゴリごとの評価を集計し、高評価カテゴリを配信されやすくする重み付き抽選 | 次点実装 |
| 軸B: どう生成するか | カテゴリ別平均スコアと直近の高評価/低評価コンテンツをプロンプトに埋め込む(In-Context Learning) | 優先実装・実装済み |

軸Aは、Yahoo!Newsのニュース推薦などで使われる[Contextual Bandit(LinUCB)](https://arxiv.org/abs/1003.0146)の考え方を参考にしています。ただし、高評価カテゴリだけを配信し続けると「そのカテゴリしか評価されなくなり、さらに偏る」という[Degenerate Feedback Loops](https://arxiv.org/abs/1902.10730)のリスクがあるため、配信確率を0にはしない探索(exploration)を残す設計が前提になります。

軸Bは今回優先的に実装した部分で、評価データが少ないMVP初期は手動ルールベースのICL(In-Context Learning)、評価データが数十件貯まった段階でDSPy等による自動プロンプト最適化(MIPROv2、GEPAなど)へ段階移行する、というロードマップを引いています。DSPyのオプティマイザは事前に数十〜数百件の評価データを要するコールドスタート問題があるため、サービス開始直後は使えないという制約を踏まえた判断です。

「今すぐ全部自動化する」のではなく、**データ量に応じて手法を切り替えるロードマップを先に決めておく**ことで、MVPの実装をシンプルに保ちながら、後から迷わず拡張できるようにしています。

## 4. 地味だけど効いている工夫: ログ設計とアラート発報

個人開発では「エラーが起きても誰も気づかない」ことが一番怖いので、ログの書き方自体を監視の仕組みと一体で設計しました。

### 「ログの文字列」を監視の入り口にする

```python
try:
    response = table.query(...)
except ClientError as e:
    # ALARM: プレフィックスはCloudWatch Logsメトリクスフィルタで拾うための目印(他の失敗と区別するため)
    logger.error(f"[ALARM:DYNAMO_READ_FAILED] table={table_name}, user_id={user_id}, error={e}")
    raise
```

`[ALARM:xxx]`という固定文字列をログに仕込み、それをCloudWatch Logsのメトリクスフィルタで検知 → カスタムメトリクス化 → 閾値判定 → SNS経由でメール通知、という4段階のパイプラインを組んでいます。

```
[ALARM:xxx]ログ → メトリクスフィルタ → アラーム(閾値判定) → SNSトピック → メール
```

CloudFormationでの実装(3種類の失敗パターンすべてに同じ型を適用しています):

```json
"DynamoReadFailedMetricFilter": {
  "Type": "AWS::Logs::MetricFilter",
  "Properties": {
    "LogGroupName": { "Ref": "Lambda1LogGroup" },
    "FilterPattern": "\"[ALARM:DYNAMO_READ_FAILED]\"",
    "MetricTransformations": [{
      "MetricValue": "1", "DefaultValue": 0,
      "MetricNamespace": "ChildcareInfo/Alarms", "MetricName": "DynamoReadFailed"
    }]
  }
}
```

このパイプラインを設計するときに決めたルールは3つです。

1. 固定文字列のプレフィックス(`[ALARM:xxx]`)を先に決める。自然文のエラーメッセージだけで判定すると、文言修正のたびにアラームが壊れる
2. 1つの失敗パターン = 1つのタグ = 1つのメトリクスを徹底する。複数の失敗を1つのタグにまとめると、後で原因の切り分けができなくなる
3. タグは実装より先に「何を検知したいか」から逆算して決める

### あえて例外を伝播させる設計

LINEへのPush API呼び出しが失敗した場合、あえて例外を`raise`して呼び出し元(Lambdaランタイム)に伝播させています。

```python
else:
    logger.error(f"[ALARM:LINE_DELIVERY_FAILED] status={response.status_code}, body={response.text}")
    # ここで例外を送出しないと、LINE配信に失敗してもLambdaは正常終了(exit code 0)扱いになり、
    # CloudWatchアラームがLambdaの Errors メトリクスで検知できなくなるため、あえて呼び出し元に伝播させる
    raise RuntimeError(f"LINE API Error: status={response.status_code}, body={response.text}")
```

一方で、DynamoDBへの書き込み失敗は例外を再送出していません。

```python
except ClientError as e:
    # 書き込みに失敗してもLINE配信は止めない方針のため、ここでは例外を再送出しない。
    # (代わりにpostbackでのスコア更新は効かなくなるが、情報が届くことを優先する)
    logger.error(f"[ALARM:DYNAMO_WRITE_FAILED] table={TABLE1_NAME}, error={e}")
```

「毎日配信は絶対」という要求1を優先し、**フィードバック集計が多少壊れても配信自体は止めない**という優先順位づけを、例外を投げるか・握りつぶすかという実装レベルの判断に反映させています。「失敗したら例外を投げる」という単一のルールではなく、要求の優先度に応じて意図的にログレベルと制御フローを使い分けている点がこの部分の工夫です。

## 5. 地味だけど効いている工夫: メンテしやすいサーバーレス構成

個人開発は「動き続けること」と「後から自分が触れること」の両方が大事なので、以下を意識しました。

- **Lambda(配信バッチ用) / Lambda(Webhook受信用)を分離**: トリガー(EventBridge / Function URL)とIAM権限をそれぞれ独立させつつ、DynamoDB操作などの共通ロジックは重複させない構成にしています
- **IAM権限は必要最小限に**: 例えば配信バッチ用のLambdaロールは、Table1に対して`Query`/`PutItem`のみ、Table2/Table3に対して`GetItem`のみを許可しており、`UpdateItem`はWebhook受信用のLambdaロールだけに絞っています
- **DynamoDBはオンデマンド課金(PAY_PER_REQUEST)+TTL**: 想定ユーザー規模(MVP1人・将来最大50人)に対してキャパシティプランニングが不要な課金方式を選び、配信履歴には7日のTTLを設定して自動的に古いデータを削除しています
- **インフラをすべてCloudFormationでコード化**: DynamoDB・Lambda・EventBridge・CloudWatchアラームまで含めて1つのテンプレートで管理しているため、環境の再現や変更差分の確認が容易です

「サーバーが動いているかを気にしなくていい」「コストが跳ねない」「権限の見通しが良い」という3点を、個人開発の運用コストとして最初から設計に織り込んでいます。

## 6. 今後の展望

現時点でMVPスコープ外とした項目もいくつかあります。

- 軸A(カテゴリ選択の重み付き抽選)の実装
- 評価データが貯まった後のDSPyによるプロンプト自動最適化への移行
- 月齢別の発達目安データを使ったRAG化
- 医療関連の誤情報対策の強化(現状はプロンプト側で「断定的な医療表現を避ける」よう指示するのみ)

「今回のMVPで何を実装し、何を意図的に先送りしたか」を明文化しておくことで、次に着手すべき優先順位が自分の中でもぶれないようにしています。

## まとめ

- 要求定義→ゴール指標(役立つ評価50%以上)→アーキテクチャという流れを1本につなげて設計した
- DynamoDBを「保存先」ではなく「パーソナライズのための状態管理装置」として使い、配信履歴・プロフィール・カテゴリスコアの3テーブルで重複回避と好み反映を実現した
- ログの文字列自体を監視のトリガーとして設計し、失敗の種類ごとに「配信を止めるか/止めないか」を意図的に使い分けた
- Lambda + EventBridge + DynamoDBのサーバーレス構成で、個人開発でも運用コストとメンテコストを抑えられるようにした

コード・インフラ定義は以下のリポジトリで公開しています。

- https://github.com/badorisu0555/childcare-info

## 参考資料

- こども家庭庁 母子健康手帳情報支援サイト: https://mchbook.cfa.go.jp/
- LINE Messaging API(メッセージ送信): https://developers.line.biz/ja/docs/messaging-api/sending-messages/
- LINE Messaging API(Flex Message要素): https://developers.line.biz/ja/docs/messaging-api/flex-message-elements/
- Lambda Function URLでのWebhookエンドポイント構築(AWS公式チュートリアル): https://docs.aws.amazon.com/lambda/latest/dg/urls-webhook-tutorial.html
- AWS Lambda Function URLsの発表(AWS公式ブログ): https://aws.amazon.com/jp/blogs/news/announcing-aws-lambda-function-urls-built-in-https-endpoints-for-single-function-microservices/
- Secrets Manager vs Parameter Storeの料金・使い分け: https://ranthebuilder.cloud/blog/secrets-manager-vs-parameter-store-which-one-should-you-really-use/
- Contextual Bandit(Yahoo!News, LinUCB): https://arxiv.org/abs/1003.0146
- Degenerate Feedback Loops: https://arxiv.org/abs/1902.10730
